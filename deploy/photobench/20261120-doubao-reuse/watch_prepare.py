"""Watch the exact independent-copy worker and queue compact evidence on terminal/error."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "20261118-atm1013-full"))
from monitor_event import emit_event


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--remote-script", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures = 0
    started = time.monotonic()
    while True:
        reason = ""
        try:
            result = subprocess.run(
                [
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
                    "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python",
                    args.remote_script,
                    "probe",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=True,
            )
            snapshot = json.loads(result.stdout)
            failures = 0
            if snapshot["status"] in {"completed", "failed"}:
                reason = "Independent historical Doubao memory preparation reached terminal status"
            elif not snapshot["worker_alive"] and time.monotonic() - started > 120:
                reason = "Exact independent-copy preparation worker disappeared"
            elif min(snapshot["disk_free"].values()) < 1536 * 1024**2:
                reason = "Preparation disk space below 1.5GiB"
            elif time.monotonic() - started > 24 * 3600:
                reason = "Preparation exceeded 24-hour review window"
        except Exception as error:
            failures += 1
            snapshot = {"status": "probe_error", "consecutive_failures": failures, "error": str(error)[:2000]}
            if failures >= 3:
                reason = "Three consecutive exact preparation probes failed"
        snapshot["stage"] = "doubao_memory_preparation"
        evidence = args.audit / "preparation-latest.json"
        evidence.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        if args.check:
            print(json.dumps({"snapshot": snapshot, "reason": reason}, ensure_ascii=False), flush=True)
            return 1 if failures else 0
        if reason:
            event = args.audit / ("preparation-event-" + str(int(time.time())) + ".json")
            event.write_text(
                json.dumps(
                    {
                        "status": "needs_review",
                        "reason": reason,
                        "snapshot": snapshot,
                        "evidence": str(evidence),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            emit_event(args.registration, event)
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
