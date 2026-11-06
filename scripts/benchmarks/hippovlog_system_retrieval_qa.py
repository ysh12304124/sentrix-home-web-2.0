#!/usr/bin/env python3
"""Run HippoVlog QA through Sentrix's product retrieval orchestration.

``visual`` filters audio-segment vectors from the existing text ANN channel;
``visual_audio`` uses the product vector repository unchanged; ``audio`` keeps
only audio-segment vectors and exposes no visual captions. There is no
benchmark-only top-k reranker or neighbouring-ASR expansion.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "apps/api/src")]

from scripts.benchmarks.video_multimodal_retrieval_ab import DirectWeMM, MIGRATIONS
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.retrieval.ports import VectorHit
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

DEFAULT_SCOPE = "hippovlog-system-whisper"
DEFAULT_VIDEO_IDS = ("HLDPA3FTUJ4", "YF_uATQABhA", "NiKqiIYwVAg", "RS9IkVgOibU")
# Compatibility for the older audio-only and context-expansion evaluation helpers.
VIDEO_IDS = frozenset(DEFAULT_VIDEO_IDS)


class EmptyRetriever:
    def execute(self, *args: object, **kwargs: object) -> tuple[()]:
        return ()


class WithoutAudioVectors:
    """Read-only repository view for the visual-only control arm."""

    def __init__(self, inner: SqliteVectorRepository) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def search_exact(self, **kwargs: Any) -> list[Any]:
        return [
            hit
            for hit in self._inner.search_exact(**kwargs)
            if hit.source_type != "audio_segment"
        ]


class OnlyAudioVectors:
    """Read-only repository view for the pure-audio arm."""

    def __init__(self, inner: SqliteVectorRepository) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def search_exact(self, **kwargs: Any) -> list[Any]:
        return [
            hit
            for hit in self._inner.search_exact(**kwargs)
            if hit.source_type == "audio_segment"
        ]


class CachedEmbedder:
    """Reuse the identical query embedding across product retrieval channels."""

    def __init__(self, inner: DirectWeMM) -> None:
        self._inner = inner
        self.model_id = inner.model_id
        self.dimension = inner.dimension
        self._text = ""
        self._vector: list[float] = []

    def embed_query(self, text: str) -> list[float]:
        if text != self._text:
            self._text = text
            self._vector = self._inner.embed_query(text)
        return self._vector


class NumpyExactVectors:
    """Same SQLite exact-search data, decoded once and scored in memory.

    The product repository reparses every JSON vector on every channel call.
    That is useful as a low-volume fallback but unnecessarily dominates this
    160-query benchmark. This adapter preserves its filters, cosine metric and
    deterministic ordering while replacing only the implementation of the
    brute-force dot products.
    """

    def __init__(self, database: Database, inner: SqliteVectorRepository) -> None:
        self._database = database
        self._inner = inner
        self._cache: dict[tuple[str, str | None, str | None], tuple[Any, ...]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _load(
        self, *, space: str, scope_id: str | None, model_name: str | None
    ) -> tuple[Any, ...]:
        key = (space, scope_id, model_name)
        if key in self._cache:
            return self._cache[key]
        sql = (
            "SELECT source_id, scope_id, source_type, model_name, vector_json, metadata_json "
            "FROM memory_vectors WHERE space = ?"
        )
        parameters: list[Any] = [space]
        if scope_id:
            sql += " AND scope_id = ?"
            parameters.append(scope_id)
        if model_name:
            sql += " AND model_name = ?"
            parameters.append(model_name)
        with self._database.connect() as connection:
            rows = list(connection.execute(sql, tuple(parameters)).fetchall())
        matrix = np.asarray([json.loads(row["vector_json"]) for row in rows], dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1) if len(rows) else np.asarray([], dtype=np.float64)
        loaded = (rows, matrix, norms)
        self._cache[key] = loaded
        return loaded

    def search_exact(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> list[VectorHit]:
        if not vector or limit < 1:
            return []
        rows, matrix, norms = self._load(
            space=space, scope_id=scope_id, model_name=model_name
        )
        query = np.asarray(vector, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != query.shape[0]:
            return []
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            scores = np.zeros(len(rows), dtype=np.float64)
        else:
            denominator = norms * query_norm
            scores = np.divide(
                matrix @ query,
                denominator,
                out=np.zeros_like(denominator),
                where=denominator != 0,
            )
        hits = [
            VectorHit(
                source_id=row["source_id"],
                scope_id=row["scope_id"],
                source_type=row["source_type"],
                model_name=row["model_name"],
                score=float(score),
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row, score in zip(rows, scores, strict=True)
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.source_id))
        return hits[:limit]


def load_audio_segments(
    root: Path, video_ids: Sequence[str]
) -> dict[str, dict[tuple[int, int], str]]:
    result: dict[str, dict[tuple[int, int], str]] = {}
    for video_id in video_ids:
        payload = json.loads((root / video_id / "asr.json").read_text())
        result[video_id] = {
            (int(segment["start_ms"]), int(segment["end_ms"])): str(
                segment.get("normalized_text") or segment.get("raw_text") or ""
            ).strip()
            for segment in payload["segments"]
        }
    return result


def call_answer_model(
    *,
    url: str,
    model: str,
    question: str,
    options: Mapping[str, str],
    evidence: str,
) -> tuple[str, str]:
    choices = "\n".join(f"{key}. {value}" for key, value in options.items())
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Answer the multiple-choice question only from the retrieved Sentrix "
                    "evidence. Reply with exactly one letter: A, B, C, or D."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {question}\nChoices:\n{choices}\n\n"
                    f"Retrieved evidence:\n{evidence}\n\nAnswer:"
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 12,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = json.load(response)["choices"][0]["message"]["content"].strip()
            for character in raw.upper():
                if character in "ABCD":
                    return character, raw
            return "INVALID", raw
        except (TimeoutError, urllib.error.URLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"answer model failed after retries: {last_error}")


def build_retriever(
    *, embedder: DirectWeMM, vectors: Any, empty: EmptyRetriever
) -> RetrieveEvidence:
    return RetrieveEvidence(
        visual=RetrieveVisual(embedder=embedder, vectors=vectors, index=None),
        text=RetrieveTextAnn(embedder=embedder, vectors=vectors),
        lexical=empty,
        metadata=empty,
        entity=empty,
    )


def format_evidence(
    *,
    hits: Sequence[Any],
    observations: SqliteObservationRepository,
    audio_segments: Mapping[str, Mapping[tuple[int, int], str]],
    include_audio: bool,
    include_visual: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    texts: list[str] = []
    traces: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        observation_rows = observations.list_by_asset(hit.asset_id)
        caption = observation_rows[0].caption if observation_rows else ""
        video_id = hit.asset_id.split(":", 1)[0]
        audio_text = ""
        if include_audio and hit.audio_start_ms is not None and hit.audio_end_ms is not None:
            audio_text = audio_segments.get(video_id, {}).get(
                (int(hit.audio_start_ms), int(hit.audio_end_ms)), ""
            )
        parts: list[str] = []
        if include_visual:
            parts.append(f"[{rank}] visual observation: {caption}")
        if audio_text:
            parts.append(
                f"[{rank}] audio transcript ({hit.audio_start_ms / 1000:.2f}s-"
                f"{hit.audio_end_ms / 1000:.2f}s): {audio_text}"
            )
        if parts:
            texts.append("\n".join(parts))
        traces.append(
            {
                "rank": rank,
                "asset_id": hit.asset_id,
                "score": hit.score,
                "channels": dict(hit.channels),
                "audio_start_ms": hit.audio_start_ms,
                "audio_end_ms": hit.audio_end_ms,
                "audio_text": audio_text,
            }
        )
    return texts, traces


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--video-ids", default=",".join(DEFAULT_VIDEO_IDS))
    parser.add_argument(
        "--asr-root", type=Path, default=ROOT / "audio_eval/outputs/hippovlog_whisper"
    )
    parser.add_argument("--model", default="weights/WeMM-Embedding-2B")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    parser.add_argument(
        "--arms",
        default="visual,visual_audio",
        help="Comma-separated subset of: visual, visual_audio, audio",
    )
    args = parser.parse_args()
    video_ids = tuple(value.strip() for value in args.video_ids.split(",") if value.strip())
    if not video_ids:
        raise ValueError("--video-ids must not be empty")

    database = Database(args.database, MIGRATIONS)
    sqlite_vectors = SqliteVectorRepository(database)
    vectors = NumpyExactVectors(database, sqlite_vectors)
    embedder = CachedEmbedder(DirectWeMM(args.model, args.device))
    observations = SqliteObservationRepository(database)
    empty = EmptyRetriever()
    available_arms = {
        "visual": build_retriever(
            embedder=embedder, vectors=WithoutAudioVectors(vectors), empty=empty
        ),
        "visual_audio": build_retriever(embedder=embedder, vectors=vectors, empty=empty),
        "audio": build_retriever(
            embedder=embedder, vectors=OnlyAudioVectors(vectors), empty=empty
        ),
    }
    requested_arms = tuple(name.strip() for name in args.arms.split(",") if name.strip())
    unknown_arms = set(requested_arms) - set(available_arms)
    if not requested_arms or unknown_arms:
        raise ValueError(f"invalid --arms value; unknown={sorted(unknown_arms)}")
    arms = {name: available_arms[name] for name in requested_arms}
    audio_segments = load_audio_segments(args.asr_root, video_ids)
    cases = [
        row
        for line in args.questions.read_text().splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row["video_id"] in video_ids
    ]
    expected_cases = 40 * len(video_ids)
    if len(cases) != expected_cases:
        raise RuntimeError(f"expected {expected_cases} questions, found {len(cases)}")

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, case in enumerate(cases, start=1):
        row: dict[str, Any] = {
            "video_id": case["video_id"],
            "question": case["question_text"],
            "category": case["category"],
            "correct_answer": case["correct_answer"],
            "answers": {},
            "raw_answers": {},
            "hits": {},
        }
        for name, retriever in arms.items():
            retrieval = retriever.execute(
                EvidenceRequest(text=case["question_text"], scope_id=args.scope, limit=20)
            )
            evidence_rows, traces = format_evidence(
                hits=retrieval.hits,
                observations=observations,
                audio_segments=audio_segments,
                include_audio=name in {"visual_audio", "audio"},
                include_visual=name in {"visual", "visual_audio"},
            )
            answer, raw_answer = call_answer_model(
                url=args.vlm_url,
                model=args.vlm_model,
                question=case["question_text"],
                options=case["options"],
                evidence="\n\n".join(evidence_rows),
            )
            row["answers"][name] = answer
            row["raw_answers"][name] = raw_answer
            row["hits"][name] = traces
        rows.append(row)
        elapsed = time.monotonic() - started
        correct = {
            name: row["answers"][name] == row["correct_answer"] for name in arms
        }
        payload = {"completed": len(rows), "total": len(cases), "rows": rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(
            f"{index}/{len(cases)} video={case['video_id']} "
            f"answers={row['answers']} correct={correct} elapsed={elapsed:.1f}s",
            flush=True,
        )


if __name__ == "__main__":
    main()
