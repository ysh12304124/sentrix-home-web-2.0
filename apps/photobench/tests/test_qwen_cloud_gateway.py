"""Provider-boundary checks without cloud requests or credentials."""

import importlib.util
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

SOURCE = Path(__file__).resolve().parents[3] / "scripts/benchmarks/qwen_cloud_gateway.py"
spec = importlib.util.spec_from_file_location("qwen_cloud_gateway", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_provider_contract_and_private_logging(tmp_path, monkeypatch):
    config = tmp_path / "provider.json"
    config.write_text(
        json.dumps(
            {"url": "https://provider.invalid/v1", "model": "qwen3.7-plus", "api_key": "private-test-key"}
        )
    )
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("QWEN_EVAL_CONFIG", str(config))
    monkeypatch.setenv("QWEN_EVAL_LOG", str(log))
    seen = []

    async def upstream(request):
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer private-test-key"
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "model": "qwen3.7-plus",
                "choices": [{"finish_reason": "stop", "message": {"content": "unchanged-output"}}],
            },
        )

    app = module.create_app()
    with TestClient(app) as client:
        assert client.get("/props").json()["default_generation_settings"]["n_ctx"] == 16384
        assert client.get("/props").json()["total_slots"] == 4
        assert client.get("/v1/models").json()["data"][0]["id"] == "qwen3.7-plus"
        assert client.post("/v1/chat/completions", json={"model": "wrong"}).status_code == 400
        assert (
            client.post("/v1/chat/completions", json={"model": "qwen3.7-plus", "stream": True}).status_code
            == 400
        )
        original = app.state.client

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as fake:
                app.state.client = fake
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as api:
                    payload = {
                        "model": "qwen3.7-plus",
                        "max_tokens": 2048,
                        "temperature": 0.7,
                        "messages": [{"role": "user", "content": "private-prompt"}],
                        "tools": [{"type": "function", "function": {"name": "echo"}}],
                        "chat_template_kwargs": {"enable_thinking": True},
                        "thinking": {"type": "enabled"},
                        "enable_thinking": True,
                    }
                    result = await api.post("/v1/chat/completions", json=payload)
                    assert result.json()["choices"][0]["message"]["content"] == "unchanged-output"
            app.state.client = original

        client.portal.call(exercise)
        assert client.get("/health").json()["inflight_requests"] == 0
    assert len(seen) == 1 and seen[0]["enable_thinking"] is False
    assert seen[0]["max_tokens"] == 2048 and seen[0]["temperature"] == 0.7
    assert seen[0]["messages"] == [{"role": "user", "content": "private-prompt"}]
    assert seen[0]["tools"][0]["function"]["name"] == "echo"
    assert "thinking" not in seen[0] and "chat_template_kwargs" not in seen[0]
    text = log.read_text()
    assert "private-test-key" not in text and "private-prompt" not in text
    assert json.loads(text)["actual_model"] == "qwen3.7-plus"
