"""Read-only latency, exact run metadata, source hashes and service PIDs."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXPERIMENT = Path("/home/realmagic/xy/experiments/atm1013-20261118")


def request(port, path):
    start = time.perf_counter()
    result = {"port": port, "path": path}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=8) as response:
            data = json.load(response)
        result.update(
            ok=True, active_count=data.get("active_count"), work=data.get("work"), scan=data.get("scan")
        )
    except (OSError, ValueError) as exc:
        result.update(ok=False, error=str(exc))
    result["seconds"] = time.perf_counter() - start
    return result


result = {"at": datetime.now().astimezone().isoformat(), "pids": {}, "sources": [], "runs": {}}
for port, unit in [
    (8772, "sentrix2-photobench.service"),
    (8872, "sentrix-atm100-photobench.service"),
    (8874, "sentrix-atm100-doubao-photobench.service"),
    (8191, "sentrix2-api.service"),
    (8291, "sentrix-atm100-api.service"),
    (8294, "sentrix-atm100-doubao-api.service"),
]:
    result["pids"][str(port)] = subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()
for row in json.loads((BASE / "manifest.json").read_text())["files"]:
    path = Path(row["target"])
    result["sources"].append({"target": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
for stage, mapping in json.loads((EXPERIMENT / "active-runs.json").read_text()).items():
    root = (
        Path("/home/realmagic/xy/atm-bench-20261110/results")
        if stage == "q4km"
        else EXPERIMENT / "cloud-results"
    )
    run = root / mapping["run_id"]
    generation = json.loads((run / "snapshot-commit.json").read_text())["generation"]
    state = json.loads((run / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    phase = state.get("current_phase")
    result["runs"][stage] = {
        key: state.get(key) for key in ["run_id", "status", "item_count", "qa_count", "current_phase"]
    }
    result["runs"][stage]["progress"] = (state.get("phases") or {}).get(phase) or {}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    tasks = [
        pool.submit(request, p, path)
        for p in [8772, 8872, 8874]
        for path in ["runs?page=1&page_size=20&group_tasks=true", "active-work"]
    ]
    result["http"] = [task.result() for task in tasks]
print(json.dumps(result, indent=2))
