#!/usr/bin/env python3
"""Replay saved Sentrix retrieval hits with adjacent Whisper context.

The retrieval ranking, fusion scores and audio anchor segments come from the
completed visual+audio system run.  This experiment changes only evidence
assembly: an audio anchor may include its immediately adjacent segments when
the gap is no greater than five seconds.  Neighbours never receive scores and
therefore cannot alter retrieval ranking.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

VIDEO_IDS = frozenset({"HLDPA3FTUJ4", "YF_uATQABhA", "NiKqiIYwVAg", "RS9IkVgOibU"})
MAX_ADJACENT_GAP_MS = 5_000


def call_answer_model(
    *, url: str, model: str, question: str, options: Mapping[str, str], evidence: str
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


def load_captions(root: Path) -> dict[str, str]:
    captions: dict[str, str] = {}
    for video_id in VIDEO_IDS:
        rows = json.loads((root / video_id / "observations.json").read_text())
        captions.update(
            {f'{video_id}:scene:{row["asset_id"]}': str(row.get("caption", "")) for row in rows}
        )
    return captions


def load_asr(root: Path) -> dict[str, tuple[list[dict[str, Any]], dict[tuple[int, int], int]]]:
    result: dict[str, tuple[list[dict[str, Any]], dict[tuple[int, int], int]]] = {}
    for video_id in VIDEO_IDS:
        segments = json.loads((root / video_id / "asr.json").read_text())["segments"]
        by_time = {
            (int(segment["start_ms"]), int(segment["end_ms"])): index
            for index, segment in enumerate(segments)
        }
        result[video_id] = segments, by_time
    return result


def expand_context(
    *,
    video_id: str,
    start_ms: int,
    end_ms: int,
    asr: Mapping[str, tuple[list[dict[str, Any]], dict[tuple[int, int], int]]],
) -> list[dict[str, Any]]:
    segments, by_time = asr[video_id]
    anchor_index = by_time.get((start_ms, end_ms))
    if anchor_index is None:
        return []
    selected = [anchor_index]
    if anchor_index > 0:
        previous = segments[anchor_index - 1]
        if start_ms - int(previous["end_ms"]) <= MAX_ADJACENT_GAP_MS:
            selected.insert(0, anchor_index - 1)
    if anchor_index + 1 < len(segments):
        following = segments[anchor_index + 1]
        if int(following["start_ms"]) - end_ms <= MAX_ADJACENT_GAP_MS:
            selected.append(anchor_index + 1)
    expanded: list[dict[str, Any]] = []
    for index in selected:
        segment = segments[index]
        expanded.append(
            {
                "relation": (
                    "anchor"
                    if index == anchor_index
                    else "previous"
                    if index < anchor_index
                    else "next"
                ),
                "start_ms": int(segment["start_ms"]),
                "end_ms": int(segment["end_ms"]),
                "text": str(segment.get("normalized_text") or segment.get("raw_text") or ""),
            }
        )
    return expanded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--observations-root", type=Path, required=True)
    parser.add_argument("--asr-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vlm-url", default="http://127.0.0.1:8300")
    parser.add_argument("--vlm-model", default="hippovlog-qwen35")
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text())["rows"]
    questions = [
        row
        for line in args.questions.read_text().splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row["video_id"] in VIDEO_IDS
    ]
    if len(baseline) != 160 or len(questions) != 160:
        raise RuntimeError("expected 160 aligned baseline rows and questions")
    captions = load_captions(args.observations_root)
    asr = load_asr(args.asr_root)

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, (base, question) in enumerate(zip(baseline, questions, strict=True), start=1):
        if base["question"] != question["question_text"]:
            raise RuntimeError(f"question mismatch at row {index}")
        evidence_rows: list[str] = []
        expanded_hits: list[dict[str, Any]] = []
        for hit in base["hits"]["visual_audio"]:
            rank = int(hit["rank"])
            parts = [f"[{rank}] visual observation: {captions.get(hit['asset_id'], '')}"]
            context: list[dict[str, Any]] = []
            if hit["audio_start_ms"] is not None and hit["audio_end_ms"] is not None:
                video_id = hit["asset_id"].split(":", 1)[0]
                context = expand_context(
                    video_id=video_id,
                    start_ms=int(hit["audio_start_ms"]),
                    end_ms=int(hit["audio_end_ms"]),
                    asr=asr,
                )
                for segment in context:
                    parts.append(
                        f"audio transcript [{segment['relation']}] "
                        f"({segment['start_ms'] / 1000:.2f}s-"
                        f"{segment['end_ms'] / 1000:.2f}s): {segment['text']}"
                    )
            evidence_rows.append("\n".join(parts))
            expanded_hits.append({**hit, "expanded_context": context})
        answer, raw_answer = call_answer_model(
            url=args.vlm_url,
            model=args.vlm_model,
            question=question["question_text"],
            options=question["options"],
            evidence="\n\n".join(evidence_rows),
        )
        row = {
            "video_id": base["video_id"],
            "question": base["question"],
            "category": base["category"],
            "correct_answer": base["correct_answer"],
            "baseline_answer": base["answers"]["visual_audio"],
            "expanded_answer": answer,
            "raw_answer": raw_answer,
            "hits": expanded_hits,
        }
        rows.append(row)
        payload = {"completed": len(rows), "total": len(baseline), "rows": rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(
            f"{index}/{len(baseline)} expanded={answer} "
            f"correct={answer == base['correct_answer']} "
            f"elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )


if __name__ == "__main__":
    main()
