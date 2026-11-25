"""Monitor the exact four-answer Judge activity and emit a compact durable wake."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "20261118-atm1013-full"))
from monitor_event import emit_event

parser = argparse.ArgumentParser(__doc__)
parser.add_argument("--task-id", required=True)
parser.add_argument("--registration", type=Path, required=True)
parser.add_argument("--audit", type=Path, required=True)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
failures = 0
started = time.monotonic()
while True:
    reason = ""
    try:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=2",
            "192.168.0.100",
            "env",
            "PYTHONPATH=/home/realmagic/xy/experiments/atm1013-20261118/"
            "conversation-delete-9363d4b-v2/evaluation/apps/photobench/src",
            "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python",
            "/home/realmagic/xy/experiments/atm1013-20261118/qwen-terminal-00db192/score_four.py",
            "probe",
            "--task-id",
            args.task_id,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=True)
        snapshot = json.loads(result.stdout)
        failures = 0
        if snapshot["status"] != "running" and not snapshot["worker_alive"]:
            reason = "Four saved-answer Judge results reached terminal state; accept receipts and exports"
        elif not snapshot["worker_alive"] and time.monotonic() - started > 120:
            reason = "Exact four-answer Judge worker is absent"
        elif min(snapshot["disk_free"].values()) < 1536 * 1024**2:
            reason = "Disk free space below 1.5 GiB"
        elif time.monotonic() - started > 6 * 3600:
            reason = "Judge auxiliary exceeded six-hour monitoring window"
    except Exception as error:
        failures += 1
        snapshot = {"rejudge_id": args.task_id, "consecutive_failures": failures, "error": str(error)[:2000]}
        if failures >= 3:
            reason = "Three consecutive exact four-answer Judge probes failed"
    evidence = args.audit / "scoring-latest.json"
    evidence.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    if args.check:
        print(json.dumps({"snapshot": snapshot, "reason": reason}))
        sys.exit(1 if failures else 0)
    if reason:
        event = args.audit / ("scoring-event-" + str(int(time.time())) + ".json")
        compact = {
            k: snapshot.get(k)
            for k in (
                "run_id",
                "rejudge_id",
                "status",
                "completed",
                "total",
                "failed",
                "error",
                "worker_alive",
                "consecutive_failures",
                "disk_free",
            )
            if k in snapshot
        }
        event.write_text(
            json.dumps(
                {"status": "needs_review", "reason": reason, "snapshot": compact, "evidence": str(evidence)},
                ensure_ascii=False,
                indent=2,
            )
        )
        emit_event(args.registration, event)
        break
    time.sleep(60)
