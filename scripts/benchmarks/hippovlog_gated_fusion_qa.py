#!/usr/bin/env python3
"""Evaluate question-routed multimodal fusion using existing HippoVlog evidence.

This is deliberately an answer-stage experiment.  It reads the baseline
``answers.json`` produced by ``hippovlog_single_video_modal_qa.py`` and reuses
its already-retrieved Top-3 frames and ASR snippets.  It never opens a video,
runs ASR, extracts frames, or writes to the product database.

For every question the model first sees *only the question and choices* and
routes it to ``visual``, ``audio``, or ``both``.  A second request receives
only the evidence allowed by that route and returns the multiple-choice answer.
The output is a separate ``answers_gated.json`` file so the baseline remains
unchanged and the experiment can be deleted or rerun safely.
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
ROUTE_RE = re.compile(r"\b(VISUAL|AUDIO|BOTH)\b", re.IGNORECASE)


def complete(*, url: str, model: str, content: list[dict[str, object]], max_tokens: int) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise evidence-based evaluator."},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return str(json.load(response)["choices"][0]["message"]["content"] or "").strip()


def choices(options: dict[str, str]) -> str:
    return "\n".join(f"{key}. {value}" for key, value in options.items())


def route_question(*, url: str, model: str, question: str, options: dict[str, str]) -> tuple[str, str]:
    prompt = f"""Classify what evidence is necessary to answer this video multiple-choice question.

Use VISUAL when it asks about visible color, appearance, clothing, objects, position, background, or action.
Use AUDIO when it asks about speech, narration, dialogue, a stated fact, plan, name, or sound.
Use BOTH only when it explicitly needs both a spoken moment and what is on screen then.
Do not answer the question and do not use outside knowledge. Reply with exactly one word: VISUAL, AUDIO, or BOTH.

Question: {question}
Choices:
{choices(options)}"""
    raw = complete(url=url, model=model, content=[{"type": "text", "text": prompt}], max_tokens=8)
    match = ROUTE_RE.search(raw)
    return (match.group(1).lower() if match else "both"), raw


def answer_question(
    *,
    url: str,
    model: str,
    question: str,
    options: dict[str, str],
    frames: list[Path],
    audio: str,
) -> tuple[str, str]:
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "Choose the one correct multiple-choice answer using only the supplied evidence. "
                "The question text and choices are not evidence. Reply with exactly one letter: A, B, C, or D.\n\n"
                f"Question: {question}\nChoices:\n{choices(options)}\n\n"
                f"Audio transcript evidence:\n{audio if audio else '[not provided for this question]'}\n\n"
                f"Visual evidence: {'the attached retrieved video frames' if frames else '[not provided for this question]'}"
            ),
        }
    ]
    for path in frames:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"},
            }
        )
    raw = complete(url=url, model=model, content=content, max_tokens=64)
    match = LETTER_RE.search(raw)
    return (match.group(1).upper() if match else f"INVALID:{raw}"), raw


def frame_paths(video_dir: Path, video_id: str, hits: list[dict[str, Any]]) -> list[Path]:
    scenes = video_dir / "media" / "derived" / "video" / video_id / "scenes"
    return [
        scenes / f"{int(str(hit['asset_id']).rsplit(':', 1)[1]):03d}" / "kf_0001.jpg"
        for hit in hits
    ]


def run_video(args: argparse.Namespace, video_id: str) -> dict[str, Any]:
    video_dir = args.input_root / video_id
    source = video_dir / "answers.json"
    if not source.exists():
        raise FileNotFoundError(f"Missing baseline evidence file: {source}")
    baseline = json.loads(source.read_text())
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment": "question-only route, then route-limited answer; reuses baseline retrieval evidence",
        "source_answers": str(source),
        "video_id": video_id,
        "questions": len(baseline["rows"]),
        "rows": [],
    }
    for position, row in enumerate(baseline["rows"], 1):
        route, route_raw = route_question(
            url=args.vlm_url, model=args.vlm_model, question=row["question"], options=row["options"]
        )
        all_frames = frame_paths(video_dir, video_id, row["visual_hits"])
        frames = all_frames if route in {"visual", "both"} else []
        audio = str(row["audio_evidence"]) if route in {"audio", "both"} else ""
        answer, answer_raw = answer_question(
            url=args.vlm_url,
            model=args.vlm_model,
            question=row["question"],
            options=row["options"],
            frames=frames,
            audio=audio,
        )
        result["rows"].append(
            {
                "question": row["question"],
                "category": row["category"],
                "options": row["options"],
                "correct_answer": row["correct_answer"],
                "route": route,
                "route_raw": route_raw,
                "answer": answer,
                "answer_raw": answer_raw,
                "baseline_answers": row["answers"],
                "visual_hits": row["visual_hits"],
                "audio_hits": row["audio_hits"],
                "audio_evidence": row["audio_evidence"],
            }
        )
        print(f"[{video_id}] {position}/{len(baseline['rows'])} route={route} answer={answer}", flush=True)
    target = video_dir / args.output_name
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--video-id", action="append", required=True)
    parser.add_argument("--output-name", default="answers_gated.json")
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    args = parser.parse_args()
    results = [run_video(args, video_id) for video_id in args.video_id]
    rows = [row for result in results for row in result["rows"]]
    correct = sum(row["answer"] == row["correct_answer"] for row in rows)
    routes = {route: sum(row["route"] == route for row in rows) for route in ("visual", "audio", "both")}
    print(f"[done] {correct}/{len(rows)} ({correct / len(rows):.1%}); routes={routes}")


if __name__ == "__main__":
    main()
