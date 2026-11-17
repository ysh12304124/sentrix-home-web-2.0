"""Utility-MRAG style image helpfulness probe on the served multimodal model."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import sqlite3
import time
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

import httpx
from PIL import Image, ImageOps

from sentrix.infrastructure.assistant.model_endpoint import ServedModel

log = logging.getLogger(__name__)


class OpenAIVisualEvidenceUtility:
    """Rank with the first-token True probability; never generate an answer for scoring."""

    def __init__(
        self,
        base_url: str,
        model: ServedModel,
        *,
        timeout_s: float = 45.0,
        concurrency: int = 2,
        cache_path: Path | None = None,
        model_name: str = "",
        cache_revision: str = "v1",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout_s)
        # One shared scorer belongs to the application container, not each search.
        self._slots = BoundedSemaphore(max(1, min(2, concurrency)))
        self._lock = Lock()
        self._pending: dict[str, Future[dict[str, Any]]] = {}
        self._scores: OrderedDict[str, float] = OrderedDict()
        self._images: OrderedDict[str, str] = OrderedDict()
        self._cache_path = cache_path
        self._model_name = model_name
        self._revision = cache_revision
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(cache_path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS utility_scores (key TEXT PRIMARY KEY, score REAL, used REAL)"
                )

    @property
    def model_id(self) -> str:
        return "utility_mrag:" + self._model.current()

    def score(self, question: str, image: bytes) -> float | None:
        value = self.score_with_trace(question, image)["score"]
        return float(value) if value is not None else None

    def _lookup(self, key: str) -> float | None:
        value = self._scores.get(key)
        if value is not None:
            self._scores.move_to_end(key)
            return value
        if self._cache_path is not None:
            with sqlite3.connect(self._cache_path, timeout=10) as db:
                row = db.execute("SELECT score FROM utility_scores WHERE key=?", (key,)).fetchone()
            if row is not None:
                return float(row[0])
        return None

    def score_with_trace(self, question: str, image: bytes) -> dict[str, Any]:
        started = time.monotonic()
        model_name = self._model.current()
        if self._model_name and model_name != self._model_name:
            return {"score": None, "cache": "miss", "reason": "configured_model_mismatch"}
        image_hash = hashlib.sha256(image).hexdigest()
        # Exact query, image contents, model, endpoint and scoring/preprocessing version.
        # No question text or raw image is stored in the persistent cache.
        key = hashlib.sha256(
            json.dumps(
                [
                    self._base_url,
                    model_name,
                    self._revision,
                    "true-logprob-jpeg768-q82-v1",
                    question,
                    image_hash,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with self._lock:
            cached = self._lookup(key)
            if cached is not None:
                return {"score": cached, "cache": "hit", "latency_ms": 0.0}
            future = self._pending.get(key)
            owner = future is None
            if future is None:
                future = Future()
                self._pending[key] = future
        if not owner:
            result = dict(future.result())
            result["cache"] = "coalesced"
            result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            return result
        try:
            with self._slots:
                waited_ms = round((time.monotonic() - started) * 1000, 1)
                with self._lock:
                    image_url = self._images.get(image_hash)
                score = self._request_score(question, image, model_name, image_url, image_hash)
            result = {
                "score": score,
                "cache": "miss",
                "queue_ms": waited_ms,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "reason": "" if score is not None else "score_unavailable",
            }
            if score is not None:
                with self._lock:
                    self._scores[key] = score
                    self._scores.move_to_end(key)
                    while len(self._scores) > 8192:
                        self._scores.popitem(last=False)
                    if self._cache_path is not None:
                        with sqlite3.connect(self._cache_path, timeout=10) as db:
                            db.execute(
                                "INSERT OR REPLACE INTO utility_scores VALUES (?,?,?)",
                                (key, score, time.time()),
                            )
                            db.execute(
                                "DELETE FROM utility_scores WHERE key IN "
                                "(SELECT key FROM utility_scores ORDER BY used DESC LIMIT -1 OFFSET 50000)"
                            )
            future.set_result(result)
            return result
        except Exception as error:
            # Cache/inference failures must not strand other requests waiting on this key.
            result = {"score": None, "cache": "miss", "reason": type(error).__name__}
            future.set_result(result)
            log.warning("效用评分失败: %s", error)
            return result
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def _request_score(
        self,
        question: str,
        image: bytes,
        model_name: str,
        image_url: str | None,
        image_hash: str,
    ) -> float | None:
        try:
            if image_url is None:
                with Image.open(io.BytesIO(image)) as source:
                    picture = ImageOps.exif_transpose(source).convert("RGB")
                    picture.thumbnail((768, 768), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    picture.save(output, format="JPEG", quality=82)
                image_url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
                with self._lock:
                    self._images[image_hash] = image_url
                    while len(self._images) > 64:
                        self._images.popitem(last=False)
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": model_name,
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
