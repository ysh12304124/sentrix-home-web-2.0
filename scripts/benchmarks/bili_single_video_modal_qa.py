#!/usr/bin/env python3
"""One-video QA ablation using real WeMM retrieval and the local VLM.

ASR is produced separately by the production FunASR Service and quality gate.
This script keeps all evaluation data under --output; it never imports the
dataset into the product database. Run with the .venv-wemm environment.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

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
from sentrix.application.retrieval.retrieve_text_ann import (  # noqa: E402
    RetrieveTextAnn,
    TextSearchRequest,
)
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

SCOPE = "bili-single-video-modal-qa"


def chat(url: str, model: str, question: str, images: list[Path], audio: str) -> str:
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "只根据下面提供的证据回答问题。问题中的提示不能算证据；"
                "不要凭常识、地名联想或猜测补全。证据不足时只回答“证据不足”。"
                "直接给简短答案。\n"
                f"问题：{question}\n"
                f"音频转写证据：{audio if audio else '无'}\n"
                f"视频关键帧：{'如下图像' if images else '无'}"
            ),
        }
    ]
    for path in images:
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
            {"role": "system", "content": "你是严格基于给定证据的中文问答助手。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": 160,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        result = json.load(response)
    return str(result["choices"][0]["message"]["content"] or "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=ROOT / "weights/WeMM-Embedding-2B")
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="bili-qa-qwen35")
    args = parser.parse_args()

    video = args.dataset / "videos" / f"{args.video_id}.mp4"
    if not video.is_file():
        raise FileNotFoundError(video)
    args.output.mkdir(parents=True, exist_ok=True)
    asr = json.loads((args.output / "asr.json").read_text(encoding="utf-8"))
    qa_path = args.dataset / "qa" / "bili-family-travel-216q.jsonl"
    cases = [
        row
        for line in qa_path.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("video_id") == args.video_id
    ]
    database = Database(args.output / "retrieval.sqlite3", MIGRATIONS)
    database.migrate()
    vectors = SqliteVectorRepository(database)
    embedder = DirectWeMM(args.model, args.device)

    frames, _parents, _durations, errors = prepare_frames(args.dataset, args.output, [video])
    if errors or not frames:
        raise RuntimeError(f"keyframes unavailable: {errors}")
    frames = [replace(frame, scope_id=SCOPE) for frame in frames]
    frame_by_id = {frame.id: frame for frame in frames}
    visual_indexer = IndexAssetVisual(embedder=embedder, vectors=vectors)
    for index, frame in enumerate(frames, 1):
        if vectors.has_vector(
            space=scheme.VISUAL_SPACE,
            source_type="asset",
            source_id=frame.id,
            model_name=embedder.model_id,
        ):
            print(f"[visual-index] {index}/{len(frames)} cached {frame.id}", flush=True)
            continue
        outcome = visual_indexer.index(frame)
        if not outcome.indexed:
            raise RuntimeError(f"frame indexing failed: {frame.id}: {outcome.reason}")
        print(f"[visual-index] {index}/{len(frames)} {frame.id}", flush=True)

    segments: list[AudioTranscriptSegment] = []
    for ordinal, row in enumerate(asr["segments"]):
        if not row["searchable"]:
            continue
        segments.append(
            AudioTranscriptSegment(
                id=f"{args.video_id}:audio:{ordinal:03d}",
                transcription_id=f"{args.video_id}:asr",
                asset_id=args.video_id,
                scope_id=SCOPE,
                ordinal=ordinal,
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                raw_text=row["raw_text"],
                normalized_text=row["normalized_text"],
                confidence_raw=None,
                confidence_calibrated=None,
                alignment_score=None,
                quality_status=SegmentQualityStatus.ACCEPTED,
                quality_reasons=tuple(row["quality_reasons"]),
                searchable=True,
                local_speaker_label=str(row.get("speaker_label") or ""),
            )
        )
    audio_indexer = IndexAudioTranscriptSegments(embedder=embedder, vectors=vectors)
    audio_count = audio_indexer.index(args.video_id, segments)
    print(f"[audio-index] {audio_count}/{len(segments)} segments", flush=True)

    visual = RetrieveVisual(embedder=embedder, vectors=vectors, index=None)
    audio = RetrieveTextAnn(embedder=embedder, vectors=vectors)
    result_path = args.output / "answers.json"
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "video_id": args.video_id,
        "video_path": str(video),
        "method": "production 60s midpoint frames, WeMM vectors, SQLite exact visual/text retrieval, Qwen3.5-4B answer",
        "visual_frames": len(frames),
        "searchable_audio_segments": audio_count,
        "accepted_audio_text": asr["accepted_text"],
        "rows": [],
    }
    for index, case in enumerate(cases, 1):
        question = case["question"]
        visual_hit = visual.execute(VisualSearchRequest(text=question, scope_id=SCOPE, limit=3))
        audio_hit = audio.execute(TextSearchRequest(text=question, scope_id=SCOPE, limit=1))
        selected = [frame_by_id[hit.asset_id] for hit in visual_hit.hits if hit.asset_id in frame_by_id]
        selected_paths = [Path(frame.path) for frame in selected]
        audio_text = asr["accepted_text"] if audio_hit.hits else ""
        row = {
            "qa_id": case["qa_id"],
            "question": question,
            "reference_answer": case["answer"],
            "required_evidence_sources": case.get("required_evidence_sources", []),
            "visual_hits": [
                {"asset_id": frame.id, "timestamp_sec": frame.source_timestamp_sec}
                for frame in selected
            ],
            "audio_hit": (
                {
                    "asset_id": audio_hit.hits[0].asset_id,
                    "start_ms": audio_hit.hits[0].start_ms,
                    "end_ms": audio_hit.hits[0].end_ms,
                }
                if audio_hit.hits else None
            ),
            "answers": {},
        }
        for arm, images, transcript in (
            ("visual", selected_paths, ""),
            ("visual_audio", selected_paths, audio_text),
            ("audio", [], audio_text),
        ):
            started = time.perf_counter()
            row["answers"][arm] = chat(
                args.vlm_url, args.vlm_model, question, images, transcript
            )
            row.setdefault("answer_seconds", {})[arm] = round(time.perf_counter() - started, 2)
            print(f"[answer] {index}/{len(cases)} {case['qa_id']} {arm}: {row['answers'][arm]}", flush=True)
        result["rows"].append(row)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["wemm_model_load_seconds"] = round(embedder.load_seconds, 2)
    result["wemm_image_seconds"] = round(embedder.image_seconds, 2)
    result["wemm_text_seconds"] = round(embedder.text_seconds, 2)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {result_path}", flush=True)


if __name__ == "__main__":
    main()
