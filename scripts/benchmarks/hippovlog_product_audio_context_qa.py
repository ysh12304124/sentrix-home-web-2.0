#!/usr/bin/env python3
"""Replay four-video HippoVlog QA through the current product audio retrieval path.

This local-only benchmark reuses persisted visual vectors, captions and Whisper
segments.  It seeds the authoritative transcription tables, then exercises the
current RetrieveTextAnn gate, RetrieveEvidence fusion and ExpandAudioContext.
"""

# ruff: noqa: E402 -- repository source roots are injected before product imports.

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "apps/api/src")]

from scripts.benchmarks.hippovlog_system_retrieval_qa import (
    SCOPE,
    VIDEO_IDS,
    CachedEmbedder,
    DirectWeMM,
    EmptyRetriever,
    NumpyExactVectors,
    call_answer_model,
)
from scripts.benchmarks.video_multimodal_retrieval_ab import MIGRATIONS
from sentrix.application.retrieval.expand_audio_context import ExpandAudioContext
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.media.transcription import (
    AudioTranscription,
    AudioTranscriptSegment,
    SegmentQualityStatus,
    TranscriptionStatus,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository
from sentrix.infrastructure.persistence.repositories.audio_transcriptions import (
    SqliteAudioTranscriptionRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository


def seed_transcriptions(
    *,
    repository: SqliteAudioTranscriptionRepository,
    asr_root: Path,
    observations_root: Path,
) -> None:
    for video_id in sorted(VIDEO_IDS):
        payload = json.loads((asr_root / video_id / "asr.json").read_text())
        observations = json.loads((observations_root / video_id / "observations.json").read_text())
        asset_id = f"{video_id}:scene:{observations[0]['asset_id']}"
        transcription_id = f"{video_id}:whisper"
        rows = tuple(
            AudioTranscriptSegment(
                id=f"{video_id}:whisper:{index:04d}",
                transcription_id=transcription_id,
                asset_id=asset_id,
                scope_id=SCOPE,
                ordinal=index,
                start_ms=int(segment["start_ms"]),
                end_ms=int(segment["end_ms"]),
                raw_text=str(segment.get("raw_text") or segment.get("normalized_text") or ""),
                normalized_text=str(segment.get("normalized_text") or segment.get("raw_text") or ""),
                confidence_raw=segment.get("confidence_raw"),
                confidence_calibrated=segment.get("confidence_calibrated"),
                alignment_score=segment.get("alignment_score"),
                quality_status=SegmentQualityStatus(
                    segment.get("quality_status") or SegmentQualityStatus.ACCEPTED
                ),
                quality_reasons=tuple(segment.get("quality_reasons") or ()),
                searchable=bool(segment.get("searchable", True)),
                local_speaker_label=str(segment.get("speaker_label") or ""),
            )
            for index, segment in enumerate(payload["segments"])
        )
        repository.save(
            AudioTranscription(
                id=transcription_id,
                asset_id=asset_id,
                scope_id=SCOPE,
                status=TranscriptionStatus.COMPLETED,
                model_name=str(payload.get("model_name") or "mlx-whisper-large-v3-turbo-8bit"),
                model_version=str(payload.get("model_version") or "local"),
                config_hash=str(payload.get("config_hash") or "hippovlog-replay"),
                language=str(payload.get("language") or "en"),
                raw_text=str(payload.get("raw_text") or ""),
                accepted_text=str(payload.get("accepted_text") or ""),
            ),
            rows,
        )


def format_product_evidence(
    *, hits: list[Any], observations: SqliteObservationRepository
) -> tuple[str, list[dict[str, Any]]]:
    evidence_rows: list[str] = []
    traces: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        observation_rows = observations.list_by_asset(hit.asset_id)
        caption = observation_rows[0].caption if observation_rows else ""
        parts = [f"[{rank}] visual observation: {caption}"]
        context = []
        for segment in hit.audio_context:
            relation = "anchor" if segment.is_anchor else "context"
            parts.append(
                f"audio transcript [{relation}] ({segment.start_ms / 1000:.2f}s-"
                f"{segment.end_ms / 1000:.2f}s): {segment.text}"
            )
            context.append(
                {
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "is_anchor": segment.is_anchor,
                }
            )
        evidence_rows.append("\n".join(parts))
        traces.append(
            {
                "rank": rank,
                "asset_id": hit.asset_id,
                "score": hit.score,
                "channels": dict(hit.channels),
                "audio_start_ms": hit.audio_start_ms,
                "audio_end_ms": hit.audio_end_ms,
                "audio_context": context,
            }
        )
    return "\n\n".join(evidence_rows), traces


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--observations-root", type=Path, required=True)
    parser.add_argument("--asr-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="weights/WeMM-Embedding-2B")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    args = parser.parse_args()

    database = Database(args.database, MIGRATIONS)
    database.migrate()
    transcription_repository = SqliteAudioTranscriptionRepository(database)
    seed_transcriptions(
        repository=transcription_repository,
        asr_root=args.asr_root,
        observations_root=args.observations_root,
    )
    sqlite_vectors = SqliteVectorRepository(database)
    vectors = NumpyExactVectors(database, sqlite_vectors)
    embedder = CachedEmbedder(DirectWeMM(args.model, args.device))
    observations = SqliteObservationRepository(database)
    empty = EmptyRetriever()
    retrieval = RetrieveEvidence(
        visual=RetrieveVisual(embedder=embedder, vectors=vectors, index=None),
        text=RetrieveTextAnn(embedder=embedder, vectors=vectors),
        lexical=empty,
        metadata=empty,
        entity=empty,
        audio_context=ExpandAudioContext(transcription_repository),
        max_audio_context_hits=2,
    )
    cases = [
        row
        for line in args.questions.read_text().splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row["video_id"] in VIDEO_IDS
    ]
    baseline_rows = json.loads(args.baseline.read_text())["rows"]
    if len(cases) != 160 or len(baseline_rows) != 160:
        raise RuntimeError("expected 160 questions and 160 baseline rows")

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, (case, baseline) in enumerate(zip(cases, baseline_rows, strict=True), start=1):
        if case["question_text"] != baseline["question"]:
            raise RuntimeError(f"question mismatch at {index}")
        result = retrieval.execute(EvidenceRequest(text=case["question_text"], scope_id=SCOPE, limit=20))
        evidence, traces = format_product_evidence(hits=list(result.hits), observations=observations)
        answer, raw_answer = call_answer_model(
            url=args.vlm_url,
            model=args.vlm_model,
            question=case["question_text"],
            options=case["options"],
            evidence=evidence,
        )
        row = {
            "video_id": case["video_id"],
            "question": case["question_text"],
            "category": case["category"],
            "correct_answer": case["correct_answer"],
            "visual_answer": baseline["answers"]["visual"],
            "previous_visual_audio_answer": baseline["answers"]["visual_audio"],
            "current_answer": answer,
            "raw_answer": raw_answer,
            "hits": traces,
        }
        rows.append(row)
        payload = {
            "completed": len(rows),
            "total": len(cases),
            "started_at": datetime.now(UTC).isoformat(),
            "rows": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(
            f"{index}/160 video={case['video_id']} current={answer} "
            f"correct={answer == case['correct_answer']} "
            f"audio_hits={sum(bool(hit.audio_context) for hit in result.hits)} "
            f"elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )


if __name__ == "__main__":
    main()
