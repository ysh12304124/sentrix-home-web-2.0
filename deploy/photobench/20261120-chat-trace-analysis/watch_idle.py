"""Wake the owning chat for one candidate release; never restart a service."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", type=Path, required=True)
    args = parser.parse_args()
    intent = json.loads(args.intent.read_text())
    directory = args.intent.parent
    snapshot_path = directory / f"idle-{intent['port']}-latest.json"
    event_path = directory / f"idle-{intent['port']}-event.json"
    failures = 0
    ready = 0
    started = time.monotonic()
    while True:
        try:
            remote = shlex.join(["python3", intent["remote_probe"], "--idle-port", str(intent["port"])])
            response = subprocess.run(
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
                    intent["host"],
                    remote,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            observed = json.loads(response.stdout)
            failures = 0
            ready = ready + 1 if observed["idle"] else 0
            changed = observed["service_pids"] != intent["service_pids"]
            unexpected = any(work["id"] != intent["run_id"] for work in observed["work"])
            snapshot = {"observed": observed, "ready_probes": ready, "consecutive_failures": failures}
            reason = (
                "Candidate service became idle; recheck before activation"
                if ready >= 3
                else "Service or work identity changed; inspect before deployment"
                if changed or unexpected
                else None
            )
        except Exception as error:
            failures += 1
            ready = 0
            snapshot = {"error": str(error), "consecutive_failures": failures, "ready_probes": ready}
            reason = "Three consecutive idle probes failed" if failures >= 3 else None
        snapshot.update(port=intent["port"], candidate=intent["candidate"], observed_at=time.time())
        save(snapshot_path, snapshot)
        if not reason and time.monotonic() - started > 72 * 3600:
            reason = "Idle deployment watcher reached its 72 hour deadline"
        if reason:
            save(
                event_path,
                {
                    "status": "needs_review",
                    "reason": reason,
                    "snapshot": snapshot,
                    "evidence": str(snapshot_path),
                },
            )
            subprocess.run(
                [
                    sys.executable,
                    intent["wake_script"],
                    "--state",
                    intent["wake_state"],
                    "emit",
                    intent["wait_id"],
                    "--file",
                    str(event_path),
                ],
                check=True,
            )
            return
        time.sleep(45)


if __name__ == "__main__":
    main()
