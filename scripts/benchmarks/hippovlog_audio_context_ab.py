#!/usr/bin/env python3
"""A/B-test expanded ASR context without rerunning media processing or retrieval.

The control arm sends exactly the baseline Top-3 retrieved audio segments and
Top-3 retrieved frames to the VLM.  The expanded arm keeps the same anchors and
frames, but adds neighboring ASR segments around every audio anchor.  The two
arms use an identical prompt and decoding configuration.  Existing answers,
vectors, ASR JSON, and media outputs are read-only inputs.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LETTER_RE = re.compile(r"\b([ABCD])\b", re.IGNORECASE)
FINAL_RE = re.compile(r"\bfinal\s*:\s*([ABCD])\b", re.IGNORECASE)


def complete(*, url: str, model: str, content: list[dict[str, object]]) -> str:
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a precise evidence-based evaluator."},
                    {"role": "user", "content": content},
                ],
                "temperature": 0,
                "max_tokens": 64,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return str(json.load(response)["choices"][0]["message"]["content"] or "").strip()


def answer(
    *, url: str, model: str, question: str, options: dict[str, str], frames: list[Path], audio: str
) -> tuple[str, str]:
    choices = "\n".join(f"{key}. {value}" for key, value in options.items())
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "Choose the one correct multiple-choice answer using only the supplied evidence. "
                "The question text and choices are not evidence. Analyze silently, then reply exactly "
                "in this format: Final: X, where X is A, B, C, or D.\n\n"
                f"Question: {question}\nChoices:\n{choices}\n\n"
                f"Audio transcript evidence:\n{audio if audio else '[none]'}\n\n"
                "Visual evidence: the attached retrieved video frames"
            ),
        }
    ]
    for path in frames:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"}}
        )
    raw = complete(url=url, model=model, content=content)
    final = FINAL_RE.findall(raw)
    letters = LETTER_RE.findall(raw)
    answer = final[-1] if final else (letters[-1] if letters else "")
    return (answer.upper() if answer else f"INVALID:{raw}"), raw


def frame_paths(video_dir: Path, video_id: str, hits: list[dict[str, Any]]) -> list[Path]:
    root = video_dir / "media" / "derived" / "video" / video_id / "scenes"
    return [root / f"{int(str(hit['asset_id']).rsplit(':', 1)[1]):03d}" / "kf_0001.jpg" for hit in hits]


def anchor_index(hit: dict[str, Any]) -> int:
    return int(str(hit["segment_id"]).rsplit(":", 1)[1])


def render_segments(segments: list[dict[str, Any]], indexes: list[int]) -> str:
    return "\n".join(
        f"[{segments[index]['start_ms']}–{segments[index]['end_ms']} ms] {segments[index]['normalized_text']}"
        for index in indexes
        if segments[index].get("normalized_text")
    )


def expanded_indexes(
    *, segments: list[dict[str, Any]], anchors: list[int], radius: int, max_window_ms: int
) -> list[int]:
    """Return the union of short ordinal-neighbor windows around retrieved anchors."""
    selected: set[int] = set()
    for anchor in anchors:
        if not 0 <= anchor < len(segments):
            continue
        start = max(0, anchor - radius)
        end = min(len(segments), anchor + radius + 1)
        # Keep an actual local speech window, rather than accidentally absorbing a
        # far-away VAD island solely because it is next in ordinal order.
        for index in range(start, end):
            if segments[index]["end_ms"] - segments[anchor]["start_ms"] > max_window_ms:
                continue
            if segments[anchor]["end_ms"] - segments[index]["start_ms"] > max_window_ms:
                continue
            selected.add(index)
    return sorted(selected)


def run_video(args: argparse.Namespace, video_id: str) -> dict[str, Any]:
    video_dir = args.input_root / video_id
    baseline = json.loads((video_dir / "answers.json").read_text())
    segments = json.loads((video_dir / "asr.json").read_text())["segments"]
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment": "same retrieved frames/ASR anchors; control atomic segments vs ordinal-neighbor context",
        "video_id": video_id,
        "radius": args.radius,
        "max_window_ms": args.max_window_ms,
        "rows": [],
    }
    for position, row in enumerate(baseline["rows"], 1):
        anchors = [anchor_index(hit) for hit in row["audio_hits"]]
        control_text = render_segments(segments, anchors)
        context_indexes = expanded_indexes(
            segments=segments, anchors=anchors, radius=args.radius, max_window_ms=args.max_window_ms
        )
        context_text = render_segments(segments, context_indexes)
        frames = frame_paths(video_dir, video_id, row["visual_hits"])
        control_answer, control_raw = answer(
            url=args.vlm_url, model=args.vlm_model, question=row["question"], options=row["options"], frames=frames, audio=control_text
        )
        expanded_answer, expanded_raw = answer(
            url=args.vlm_url, model=args.vlm_model, question=row["question"], options=row["options"], frames=frames, audio=context_text
        )
        result["rows"].append(
            {
                "question": row["question"],
                "category": row["category"],
                "options": row["options"],
                "correct_answer": row["correct_answer"],
                "visual_hits": row["visual_hits"],
                "audio_hits": row["audio_hits"],
                "anchor_indexes": anchors,
                "control_audio_evidence": control_text,
                "expanded_segment_indexes": context_indexes,
                "expanded_audio_evidence": context_text,
                "control_answer": control_answer,
                "expanded_answer": expanded_answer,
                "control_raw": control_raw,
                "expanded_raw": expanded_raw,
                "original_baseline_answer": row["answers"]["visual_audio"],
            }
        )
        print(f"[{video_id}] {position}/{len(baseline['rows'])} control={control_answer} expanded={expanded_answer}", flush=True)
    target = video_dir / args.output_name
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--video-id", action="append", required=True)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--max-window-ms", type=int, default=8000)
    parser.add_argument("--output-name", default="answers_audio_context.json")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    args = parser.parse_args()
    results = [run_video(args, video_id) for video_id in args.video_id]
    rows = [row for result in results for row in result["rows"]]
    control = sum(row["control_answer"] == row["correct_answer"] for row in rows)
    expanded = sum(row["expanded_answer"] == row["correct_answer"] for row in rows)
    print(f"[done] control={control}/{len(rows)} ({control / len(rows):.1%}); expanded={expanded}/{len(rows)} ({expanded / len(rows):.1%})")


if __name__ == "__main__":
    main()
