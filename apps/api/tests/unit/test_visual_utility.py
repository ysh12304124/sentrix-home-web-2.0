"""The helpfulness probe must use image input and the True token probability."""

from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image

from sentrix.infrastructure.models.visual_utility import OpenAIVisualEvidenceUtility


class _Model:
    def current(self) -> str:
        return "qwen35-q4km"


def test_multimodal_true_token_scoring() -> None:
    captured: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        captured.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "token": "False",
                                    "logprob": -0.1,
                                    "top_logprobs": [{"token": "True", "logprob": -1.25}],
                                }
                            ]
                        }
                    }
                ]
            },
        )

    image = Image.new("RGB", (16, 16), "red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    scorer = OpenAIVisualEvidenceUtility("http://model/v1", _Model())  # type: ignore[arg-type]
    scorer._client = httpx.Client(transport=httpx.MockTransport(respond))

    assert scorer.score("What color?", buffer.getvalue()) == -1.25
    assert captured[0]["logprobs"] is True
    content = captured[0]["messages"][0]["content"]  # type: ignore[index]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def _image(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="JPEG")
    return output.getvalue()


def _response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"logprobs": {"content": [{"token": "True", "logprob": -0.5, "top_logprobs": []}]}}]
        },
    )


def test_persistent_cache_keys_and_revision(tmp_path: Path) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _response()

    def create(revision: str = "v1") -> OpenAIVisualEvidenceUtility:
        result = OpenAIVisualEvidenceUtility(
            "http://model/v1",
            _Model(),
            cache_path=tmp_path / "cache.sqlite",
            cache_revision=revision,
        )  # type: ignore[arg-type]
        result._client = httpx.Client(transport=httpx.MockTransport(respond))
        return result

    scorer = create()
    assert scorer.score_with_trace("question", _image())["cache"] == "miss"
    assert create().score_with_trace("question", _image())["cache"] == "hit"
    assert len(calls) == 1
    assert scorer.score("different question", _image()) == -0.5
    assert scorer.score("question", _image("blue")) == -0.5
    assert create("new-weights").score("question", _image()) == -0.5
    assert len(calls) == 4


def test_shared_concurrency_and_duplicate_coalescing() -> None:
    active = peak = calls = 0
    lock = threading.Lock()

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return _response()

    scorer = OpenAIVisualEvidenceUtility("http://model/v1", _Model(), concurrency=2)  # type: ignore[arg-type]
    scorer._client = httpx.Client(transport=httpx.MockTransport(respond))
    image = _image()
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert list(workers.map(lambda _: scorer.score("same", image), range(8))) == [-0.5] * 8
    assert calls == 1
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert list(workers.map(lambda i: scorer.score(str(i), image), range(8))) == [-0.5] * 8
    assert peak == 2
    assert calls == 9


def test_failure_is_not_cached() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else _response()

    scorer = OpenAIVisualEvidenceUtility("http://model/v1", _Model())  # type: ignore[arg-type]
    scorer._client = httpx.Client(transport=httpx.MockTransport(respond))
    assert scorer.score("question", _image()) is None
    assert scorer.score("question", _image()) == -0.5
    assert len(calls) == 2


def test_configured_model_mismatch_does_not_call_inference() -> None:
    scorer = OpenAIVisualEvidenceUtility(
        "http://model/v1",
        _Model(),
        model_name="different-model",
    )  # type: ignore[arg-type]
    assert scorer.score_with_trace("question", _image())["reason"] == "configured_model_mismatch"
