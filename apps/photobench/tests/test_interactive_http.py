"""Interactive HTTP request validation and application dispatch."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api_routes import client as client


def test_chat_http_contract_and_gt_source(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    calls = []
    service = client.app.state.container.chat

    def create(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"session_id": "chat-one", "scope_id": payload["scope_id"], "status": "preparing", "turns": []}

    monkeypatch.setattr(service, "create_chat", create)
    response = client.post(
        "/api/v1/chat/sessions",
        json={
            "scope_id": "space",
            "album_id": "album",
            "qa_set": "qa",
            "qa_id": "one",
            "model": {"base_url": "http://model/v1"},
        },
    )
    assert response.status_code == 202
    assert response.json()["session_id"] == "chat-one"
    assert calls[0]["qa_id"] == "one" and "answer" not in calls[0]
    assert client.post("/api/v1/chat/sessions", json={}).status_code == 422
    assert client.post("/api/v1/chat/sessions/chat-one/turns", json={"message": ""}).status_code == 422


def test_chat_delete_http_contract(monkeypatch, client: TestClient) -> None:
    from photobench.domain.errors import ChatBusyError

    calls = []

    def delete(session_id):
        calls.append(session_id)
        if session_id == "busy":
            raise ChatBusyError("请先停止回答")
        if session_id == "missing":
            raise KeyError(session_id)

    monkeypatch.setattr(client.app.state.container.chat, "delete_chat", delete)
    response = client.delete("/api/v1/chat/sessions/chat-one")
    assert response.status_code == 204 and response.content == b""
    assert client.delete("/api/v1/chat/sessions/busy").status_code == 409
    assert client.delete("/api/v1/chat/sessions/missing").status_code == 404
    assert calls == ["chat-one", "busy", "missing"]


def test_chat_light_read_and_lazy_trace_http_contract(monkeypatch, client: TestClient) -> None:
    calls = []
    service = client.app.state.container.chat

    def read(session_id, *, include_items=True):
        calls.append((session_id, include_items))
        return {"session_id": session_id, "scope_id": "space", "status": "ready", "turns": []}

    def trace(session_id, index, **options):
        calls.append((session_id, index, options))
        return {"read_revision": "pinned"}

    monkeypatch.setattr(service, "get_chat", read)
    monkeypatch.setattr(service, "get_chat_trace", trace)
    assert client.get("/api/v1/chat/sessions/chat-one").status_code == 200
    assert calls[-1] == ("chat-one", False)
    assert client.get("/api/v1/chat/sessions/chat-one?include_items=true").status_code == 200
    assert calls[-1] == ("chat-one", True)
    base = "/api/v1/chat/sessions/chat-one/turns/2"
    assert client.get(base + "/trace?revision=pinned&offset=50").json()["read_revision"] == "pinned"
    assert calls[-1][1] == 2 and calls[-1][2]["offset"] == 50
    assert client.get(base + "/trace/t0_gcalls_i0?view=timing&revision=pinned").status_code == 200
    assert calls[-1][2]["node_id"] == "t0_gcalls_i0" and calls[-1][2]["view"] == "timing"
    assert client.get(base + "/content?path=runtime_turns.0.answer").status_code == 200
    assert calls[-1][2]["path"] == ["runtime_turns", 0, "answer"]
    assert client.get(base + "/trace?limit=999").status_code == 422
    assert client.get(base + "/trace/t0_gcalls_i0?view=invalid").status_code == 422


def test_retest_http_passes_model_and_batch(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    calls = []

    def start(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((run_id, payload))
        return {"run_id": "single", "retest_context": {"source_run_id": run_id}}

    monkeypatch.setattr(client.app.state.container.run_suites, "start_retest", start)
    response = client.post(
        "/api/v1/runs/original/retest",
        json={"qa_id": "one", "scoring_batch": "batch", "model": {"profile": "other"}},
    )
    assert response.status_code == 202
    assert calls[0][0] == "original" and calls[0][1]["model"]["profile"] == "other"
    assert calls[0][1]["scoring_batch"] == "batch"


@pytest.mark.parametrize(
    "path,inputs",
    [
        ("/api/v1/chat/sessions", {"scope_id": "space"}),
        ("/api/v1/chat/sessions/chat-one/turns", {"message": "hello"}),
        ("/api/v1/runs/original/retest", {"qa_id": "one"}),
    ],
)
def test_reasoning_http_rejects_non_boolean(client: TestClient, path, inputs) -> None:
    assert client.post(path, json={**inputs, "reasoning": "false"}).status_code == 422


def test_reasoning_http_default_and_explicit_false(monkeypatch, client: TestClient) -> None:
    calls = []

    def create(payload):
        calls.append(payload)
        return {"session_id": "chat-one", "scope_id": "space", "status": "ready", "turns": []}

    monkeypatch.setattr(client.app.state.container.chat, "create_chat", create)
    assert (
        client.post("/api/v1/chat/sessions", json={"scope_id": "space", "reasoning": False}).status_code
        == 202
    )
    assert calls[-1]["reasoning"] is False
    spec = client.app.openapi()
    assert spec["components"]["schemas"]["ChatCreateRequest"]["properties"]["reasoning"]["default"] is True
    assert spec["components"]["schemas"]["RetestRequest"]["properties"]["reasoning"]["default"] is True
    assert (
        spec["paths"]["/api/v1/runs"]["post"]["requestBody"]["content"]["application/json"]["schema"][
            "properties"
        ]["reasoning"]["default"]
        is True
    )


def test_retest_progress_http_dispatch(monkeypatch, client: TestClient) -> None:
    progress = {
        "run_id": "single",
        "status": "running",
        "item": {"runtime_turns": [{"trace": [{"text": "event"}]}]},
    }
    monkeypatch.setattr(client.app.state.container.run_suites, "get_retest_progress", lambda run_id: progress)
    response = client.get("/api/v1/runs/single/progress")
    assert response.status_code == 200
    assert response.json() == progress
