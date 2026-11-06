#!/usr/bin/env python3
"""Convert local MLX Whisper JSON into the isolated HippoVlog ASR input schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    args = parser.parse_args()
    source = json.loads(args.transcript.read_text())
    segments = []
    for item in source.get("segments", []):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "start_ms": round(float(item["start"]) * 1000),
            "end_ms": round(float(item["end"]) * 1000),
            "raw_text": text,
            "normalized_text": text,
            "quality_status": "accepted",
            "quality_reasons": [],
            "searchable": True,
            "speaker_label": "",
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "source": str(args.transcript),
        "duration_s": args.duration_s,
        "model_name": "mlx-community/whisper-large-v3-turbo-8bit",
        "model_version": "local-8bit",
        "config_hash": "whisper-turbo-8bit-isolated-eval",
        "raw_text": str(source.get("text") or ""),
        "accepted_text": " ".join(segment["normalized_text"] for segment in segments),
        "segments": segments,
        "elapsed_seconds": None,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.video_id}: {len(segments)} searchable segments -> {args.output}")


if __name__ == "__main__":
    main()
