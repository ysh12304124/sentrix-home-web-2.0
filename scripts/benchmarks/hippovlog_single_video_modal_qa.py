#!/usr/bin/env python3
"""Evaluate one HippoVlog video under visual, visual+audio, and audio-only evidence.

All derived media, SQLite vectors, and answer records stay below --output.  The
video and ASR JSON must already be present.  This intentionally tests the
current Sentrix evidence chain, rather than HippoVlog's original full-video
model setting: video frames use the product's 60-second scene policy, and audio
uses the product's Paraformer transcript after its quality gate.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api" / "src"
for entry in (str(ROOT), str(API_SRC)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmarks.video_multimodal_retrieval_ab import (  # noqa: E402
    DirectWeMM,
    MIGRATIONS,
    prepare_frames,
)
from sentrix.application.retrieval.index_audio_transcript import (  # noqa: E402
    IndexAudioTranscriptSegments,
)
from sentrix.application.retrieval.index_visual import IndexAssetVisual  # noqa: E402
from sentrix.application.retrieval.retrieve_visual import (  # noqa: E402
    RetrieveVisual,
    VisualSearchRequest,
)
from sentrix.domain.media.transcription import (  # noqa: E402
    AudioTranscriptSegment,
    SegmentQualityStatus,
)
from sentrix.domain.retrieval import scheme  # noqa: E402
from sentrix.infrastructure.persistence.database import Database  # noqa: E402
from sentrix.infrastructure.persistence.repositories.vectors import (  # noqa: E402
    SqliteVectorRepository,
)

SCOPE = "hippovlog-single-video-ab"
LETTER_RE = re.compile(r"\b([ABCD])\b", re.IGNORECASE)


def select_answer(
    *,
    url: str,
    model: str,
    question: str,
    options: dict[str, str],
    frames: list[Path],
    audio: str,
) -> str:
    choices = "\n".join(f"{key}. {value}" for key, value in options.items())
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "Choose the one correct multiple-choice answer using only the supplied evidence. "
                "The question text and choices are not evidence. Do not use outside knowledge. "
                "Reply with exactly one letter: A, B, C, or D.\n\n"
                f"Question: {question}\nChoices:\n{choices}\n\n"
                f"Audio transcript evidence:\n{audio if audio else '[none]'}\n\n"
                f"Visual evidence: {'the attached retrieved video frames' if frames else '[none]'}"
            ),
        }
    ]
    for path in frames:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise evidence-based evaluator."},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": 12,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = str(json.load(response)["choices"][0]["message"]["content"] or "").strip()
    match = LETTER_RE.search(raw)
    return match.group(1).upper() if match else f"INVALID:{raw}"


def top_audio(
    vectors: SqliteVectorRepository, query: list[float], model: str, limit: int
) -> list[dict[str, Any]]:
    candidates = vectors.search_exact(
        space=scheme.SEMANTIC_SPACE,
        vector=query,
        limit=100,
        scope_id=SCOPE,
        model_name=model,
    )
    return [
        {
            "segment_id": item.source_id,
            "score": round(float(item.score), 6),
            "start_ms": item.metadata.get("start_ms"),
            "end_ms": item.metadata.get("end_ms"),
        }
        for item in candidates
        if item.source_type == "audio_segment"
    ][:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=ROOT / "weights/WeMM-Embedding-2B")
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    if not args.video.is_file():
        raise FileNotFoundError(args.video)
    asr = json.loads((args.output / "asr.json").read_text(encoding="utf-8"))
    cases = [
        row
        for line in args.questions.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("video_id") == args.video_id
    ]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise RuntimeError(f"no questions for {args.video_id}")

    database = Database(args.output / "retrieval.sqlite3", MIGRATIONS)
    database.migrate()
    vectors = SqliteVectorRepository(database)
    embedder = DirectWeMM(args.model, args.device)
    frames, _parents, _durations, errors = prepare_frames(args.output, args.output, [args.video])
    if errors or not frames:
        raise RuntimeError(f"keyframe extraction failed: {errors}")
    frames = [replace(frame, scope_id=SCOPE) for frame in frames]
    by_frame = {frame.id: frame for frame in frames}
    visual_indexer = IndexAssetVisual(embedder=embedder, vectors=vectors)
    for position, frame in enumerate(frames, 1):
        if not vectors.has_vector(
            space=scheme.VISUAL_SPACE,
            source_type="asset",
            source_id=frame.id,
            model_name=embedder.model_id,
        ):
            outcome = visual_indexer.index(frame)
            if not outcome.indexed:
                raise RuntimeError(f"visual indexing failed for {frame.id}: {outcome.reason}")
        print(f"[visual-index] {position}/{len(frames)}", flush=True)

    segments: list[AudioTranscriptSegment] = []
    text_by_segment: dict[str, str] = {}
    for ordinal, source in enumerate(asr["segments"]):
        if not source["searchable"]:
            continue
        segment_id = f"{args.video_id}:audio:{ordinal:04d}"
        text_by_segment[segment_id] = source["normalized_text"]
        segments.append(
            AudioTranscriptSegment(
                id=segment_id,
                transcription_id=f"{args.video_id}:asr",
                asset_id=args.video_id,
                scope_id=SCOPE,
                ordinal=ordinal,
                start_ms=int(source["start_ms"]),
                end_ms=int(source["end_ms"]),
                raw_text=source["raw_text"],
                normalized_text=source["normalized_text"],
                confidence_raw=None,
                confidence_calibrated=None,
                alignment_score=None,
                quality_status=SegmentQualityStatus.ACCEPTED,
                quality_reasons=tuple(source["quality_reasons"]),
                searchable=True,
                local_speaker_label=str(source.get("speaker_label") or ""),
            )
        )
    audio_count = IndexAudioTranscriptSegments(embedder=embedder, vectors=vectors).index(
        args.video_id, segments
    )
    visual = RetrieveVisual(embedder=embedder, vectors=vectors, index=None)
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "video_id": args.video_id,
        "video": str(args.video),
        "questions": len(cases),
        "keyframes": len(frames),
        "audio_segments_after_gate": audio_count,
        "method": "Sentrix 60s midpoint frames + Paraformer gate + WeMM exact retrieval + Qwen3.5 multiple-choice answer",
        "rows": [],
    }
    target = args.output / "answers.json"
    for position, case in enumerate(cases, 1):
        question = str(case["question_text"])
        query = embedder.embed_query(question)
        visual_hits = visual.execute(VisualSearchRequest(text=question, scope_id=SCOPE, limit=3)).hits
        chosen_frames = [by_frame[hit.asset_id] for hit in visual_hits if hit.asset_id in by_frame]
        audio_hits = top_audio(vectors, query, embedder.model_id, 3) if query else []
        audio_text = "\n".join(
            f"[{hit['start_ms']}–{hit['end_ms']} ms] {text_by_segment.get(hit['segment_id'], '')}"
            for hit in audio_hits
        )
        row: dict[str, Any] = {
            "question": question,
            "category": case["category"],
            "options": case["options"],
            "correct_answer": case["correct_answer"],
            "explanation": case["explanation"],
            "visual_hits": [
                {"timestamp_sec": frame.source_timestamp_sec, "asset_id": frame.id}
                for frame in chosen_frames
            ],
            "audio_hits": audio_hits,
            "audio_evidence": audio_text,
            "answers": {},
        }
        for arm, arm_frames, arm_audio in (
            ("visual", [Path(frame.path) for frame in chosen_frames], ""),
            ("visual_audio", [Path(frame.path) for frame in chosen_frames], audio_text),
            ("audio", [], audio_text),
        ):
            started = time.perf_counter()
            answer = select_answer(
                url=args.vlm_url,
                model=args.vlm_model,
                question=question,
                options=case["options"],
                frames=arm_frames,
                audio=arm_audio,
            )
            row["answers"][arm] = answer
            row.setdefault("answer_seconds", {})[arm] = round(time.perf_counter() - started, 2)
        result["rows"].append(row)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[answer] {position}/{len(cases)} {row['category']} {row['answers']}", flush=True)
    result["wemm_model_load_seconds"] = round(embedder.load_seconds, 3)
    result["wemm_image_seconds"] = round(embedder.image_seconds, 3)
    result["wemm_text_seconds"] = round(embedder.text_seconds, 3)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {target}", flush=True)


if __name__ == "__main__":
    main()
