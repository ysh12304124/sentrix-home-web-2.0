#!/usr/bin/env python3
"""Replay an identical evidence set on each platform without model calls or raw writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path
from typing import Any

from photobench.infrastructure.error_analysis.backend import FileAnalysisBackend


def replay(root: Path, output: Path) -> None:
    backend = FileAnalysisBackend(root)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*/results.jsonl")}
    backend.start(lambda: False)
    runs: dict[str, Any] = {}
    try:
        for directory in sorted(root.iterdir()):
            if not (directory / "run.json").is_file():
                continue
            job = backend.submit(directory.name, "rules", {})
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                job = backend.result(job["job_id"])
                if job["status"] not in {"queued", "running", "paused"}:
                    break
                time.sleep(0.05)
            if job["status"] != "completed":
                raise RuntimeError(str(job))
            report = job["report"]
            for format in ("html", "json", "csv"):
                (backend.artifacts / job["job_id"] / ("export." + format)).write_text(
                    backend.export(job["job_id"], format, {}), encoding="utf-8"
                )
            cases = backend.cases(job["job_id"], {}, 1, 100)["items"]
            if "153" in directory.name:
                contexts = [e for c in cases for e in c["events"] if e["code"].startswith("context_")]
                assert len(contexts) == 11 and all(e["recovery"] == "recovered" for e in contexts)
            if "118" in directory.name:
                failed = next(c for c in cases if c["qa_id"] == "validation-album3-024-q04")
                assert failed["execution"] == "failed" and failed["tool_json_total"] == 6
                assert failed["tool_json_invalid"] == 0
                assert any(e["code"] == "model_output_format" for e in failed["events"])
            facts = {
                k: report[k]
                for k in (
                    "counts",
                    "denominators",
                    "errors",
                    "groups",
                    "behavior_matrix",
                    "heatmap",
                    "gt_fingerprint",
                    "result_fingerprint",
                )
            }
            runs[directory.name] = {"job_id": job["job_id"], "facts": facts}
    finally:
        backend.close()
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*/results.jsonl")}
    assert before == after
    normalized = json.dumps(
        {name: data["facts"] for name, data in runs.items()}, sort_keys=True, ensure_ascii=False
    )
    result = {
        "host": platform.node(),
        "platform": platform.platform(),
        "time": time.time(),
        "passed": True,
        "raw_unchanged": True,
        "fact_signature": hashlib.sha256(normalized.encode()).hexdigest(),
        "runs": runs,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("host", "passed", "fact_signature")}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay(args.root, args.output)


if __name__ == "__main__":
    main()
