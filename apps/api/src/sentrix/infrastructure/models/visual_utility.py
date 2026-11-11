"""Utility-MRAG style image helpfulness probe on the served multimodal model."""

from __future__ import annotations

import base64
import io
import logging
import math

import httpx
from PIL import Image, ImageOps

from sentrix.infrastructure.assistant.model_endpoint import ServedModel

log = logging.getLogger(__name__)


class OpenAIVisualEvidenceUtility:
    """Rank with the first-token True probability; never generate an answer for scoring."""

    def __init__(self, base_url: str, model: ServedModel, *, timeout_s: float = 45.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout_s)

    @property
    def model_id(self) -> str:
        return "utility_mrag:" + self._model.current()

    def score(self, question: str, image: bytes) -> float | None:
        try:
            with Image.open(io.BytesIO(image)) as source:
                picture = ImageOps.exif_transpose(source).convert("RGB")
                picture.thumbnail((768, 768), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                picture.save(output, format="JPEG", quality=82)
            image_url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": self._model.current(),
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Question: "
                                        + question
                                        + "\nIs this image helpful for answering the question? "
                                        "Answer True or False only."
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 1,
                    "logprobs": True,
                    "top_logprobs": 20,
                },
            )
            response.raise_for_status()
            choice = response.json()["choices"][0]
            first = choice["logprobs"]["content"][0]
            tokens = [first, *(first.get("top_logprobs") or [])]
            matches = [
                float(token["logprob"]) for token in tokens if str(token.get("token", "")).strip() == "True"
            ]
            return max(matches) if matches and math.isfinite(max(matches)) else None
        except (OSError, ValueError, KeyError, IndexError, TypeError, httpx.HTTPError) as error:
            log.warning("视觉效用评分不可用: %s", error)
            return None
