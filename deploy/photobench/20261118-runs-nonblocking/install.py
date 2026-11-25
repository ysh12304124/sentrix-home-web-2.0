"""Apply the committed list-only change once; restart only the idle 8772 evaluator."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
UNITS = {
    8772: "sentrix2-photobench.service",
    8872: "sentrix-atm100-photobench.service",
    8874: "sentrix-atm100-doubao-photobench.service",
    8191: "sentrix2-api.service",
    8291: "sentrix-atm100-api.service",
    8294: "sentrix-atm100-doubao-api.service",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=10) as response:
        return json.load(response)


def save(name, value):
    (BASE / name).write_text(json.dumps(value, indent=2) + "\n")


assert not (BASE / "install-intent.json").exists(), "One-use install already attempted; inspect receipt"
manifest = json.loads((BASE / "manifest.json").read_text())
for row in manifest["files"]:
    assert digest(BASE / row["bundle"]) == row["after_sha256"], row
    assert digest(Path(row["target"])) == row["before_sha256"], row
    compile((BASE / row["bundle"]).read_bytes(), row["target"], "exec")
assert not api(8772, "active-work")["work"], "8772 has active work"
progress = api(8191, "imports/progress")
assert not progress["library"].get("processing"), progress
assert progress["postprocess"].get("state") == "idle", progress
with sqlite3.connect(
    "file:/home/realmagic/deployment_outputs/sentrix_github_update_20261027/data/sentrix.db?mode=ro", uri=True
) as db:
    turns = dict(db.execute("select status,count(*) from agent_turns group by status"))
assert not any(turns.get(status) for status in ("running", "pending", "queued")), turns
pids_before = {str(port): pid(unit) for port, unit in UNITS.items()}
save(
    "install-intent.json",
    {
        "at": datetime.now().astimezone().isoformat(),
        "source_revision": manifest["source_revision"],
        "pids": pids_before,
        "product_turns": turns,
        "scope": "Three source copies; restart idle 8772 only",
    },
)
for index, row in enumerate(manifest["files"]):
    target = Path(row["target"])
    (BASE / f"backup-{index}.py").write_bytes(target.read_bytes())
    temporary = target.with_name(target.name + ".runs-nonblocking-new")
    temporary.write_bytes((BASE / row["bundle"]).read_bytes())
    temporary.chmod(target.stat().st_mode)
    os.replace(temporary, target)
    assert digest(target) == row["after_sha256"]
subprocess.run(["systemctl", "--user", "restart", UNITS[8772]], check=True)
deadline = time.monotonic() + 45
while True:
    try:
        runs = api(8772, "runs?page=1&page_size=20&group_tasks=true")
        break
    except (OSError, ValueError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(1)
pids_after = {str(port): pid(unit) for port, unit in UNITS.items()}
for port in (8872, 8874, 8191, 8291, 8294):
    assert pids_before[str(port)] == pids_after[str(port)], (port, pids_before, pids_after)
save(
    "install-response.json",
    {
        "at": datetime.now().astimezone().isoformat(),
        "source_revision": manifest["source_revision"],
        "pids_before": pids_before,
        "pids_after": pids_after,
        "source_sha_verified": True,
        "active_count_8772": runs["active_count"],
        "activated_ports": [8772],
        "pending_restart_ports": [8872, 8874],
    },
)
print((BASE / "install-response.json").read_text())
