"""The helpfulness probe must use image input and the True token probability."""

from __future__ import annotations

import io

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
