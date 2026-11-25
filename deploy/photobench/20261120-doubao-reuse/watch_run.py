"""Wait without model polling; wake on the exact run's terminal or abnormal state."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "20261118-atm1013-full"))
from monitor_event import emit_event, terminal_event


def review_reason(snapshot):
    active = any(r.get("id") == snapshot["run_id"] and r.get("worker_alive") for r in snapshot["active"])
    if snapshot["status"] not in {"pending", "running", "cancelling"}:
        return "Exact Doubao run reached terminal state; inspect all answers, scoring and exports"
    if not active:
        return "Exact Doubao run worker disappeared"
    if not snapshot["model_matches"]:
        return "Actual Doubao model identity changed"
    if not all(snapshot[k] for k in ("service_pids_match", "product_limits_match", "product_database_match")):
        return "Doubao service process, database or request budgets changed"
    if snapshot["reasoning_context_tokens"] != 16384:
        return "Doubao reasoning context changed from 16K"
    if min(snapshot["disk_free"].values()) < 1536 * 1024**2:
        return "Disk free space below 1.5 GiB"
    if snapshot["memory_available_kib"] < 4 * 1024**2:
        return "Server available memory below 4 GiB"
    if max(snapshot["process_rss_kib"].values()) > 24 * 1024**2:
        return "A Doubao service RSS exceeded 24 GiB"
    return ""


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--remote-script", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures = 0
    started = changed = time.monotonic()
    prior = None
    while True:
        reason = ""
        try:
            result = subprocess.run([
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "192.168.0.100",
                "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python", args.remote_script,
            ], capture_output=True, text=True, timeout=90, check=True)
            snapshot = json.loads(result.stdout)
            failures = 0
            if snapshot["run_id"] != args.run_id:
                reason = "Active mapping changed from the exact watched Doubao run"
            else:
                reason = review_reason(snapshot)
            fingerprint = json.dumps([snapshot["current_phase"], snapshot["item_count"], snapshot["scoring"], snapshot["live_turns"]], sort_keys=True)
            if fingerprint != prior:
                changed, prior = time.monotonic(), fingerprint
            if not reason and time.monotonic() - changed > 7200:
                reason = "No recorded Doubao QA or turn progress for two hours"
            if not reason and time.monotonic() - started > 7 * 86400:
                reason = "Doubao watch reached seven-day review window"
        except Exception as error:
            failures += 1
            snapshot = {"stage": "doubao", "run_id": args.run_id, "consecutive_failures": failures, "probe_error": str(error)[:2000]}
            if isinstance(error, subprocess.CalledProcessError):
                snapshot["probe_stderr"] = (error.stderr or "")[-3000:]
            if failures >= 3:
                reason = "Three consecutive exact Doubao probes failed"
        evidence = args.audit / "doubao-latest.json"
        evidence.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        if args.check:
            print(json.dumps({"run_id": args.run_id, "status": snapshot.get("status"), "reason": reason, "failures": failures}), flush=True)
            return 1 if reason or failures else 0
        if reason:
            event = args.audit / ("doubao-event-" + str(int(time.time())) + ".json")
            event.write_text(json.dumps(terminal_event(reason, snapshot, evidence), ensure_ascii=False, indent=2))
            emit_event(args.registration, event)
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
