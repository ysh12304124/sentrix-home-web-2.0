"""Activate the verified Q4 candidate once, with its product and evaluator idle."""

import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
NEW = BASE / "q4-trace-reuse-release"
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
UNIT = "sentrix-atm100-photobench.service"
PROTECTED = [
    "sentrix-atm100-doubao-photobench.service",
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=15) as response:
        return json.load(response)


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def save(name, data):
    with (HERE / name).open("x") as handle:
        json.dump(data, handle, indent=2)


assert not (HERE / "activate-intent.json").exists()
bench = json.loads((HERE / "benchmark-result.json").read_text())
assert bench["items"] == 64 and bench["source_pointer_unchanged"]
assert bench["initial_freeze_seconds"] + bench["initial_write_seconds"] < 120
assert bench["repeat_copy_seconds"] + bench["repeat_write_seconds"] < 45
for name, row in json.loads((HERE / "manifest.json").read_text())["files"].items():
    assert hashlib.sha256((NEW / name).read_bytes()).hexdigest() == row["after"], name
mapping = json.loads((BASE / "active-runs.json").read_text())
run_id = mapping["q4km"]["run_id"]
assert run_id.endswith("7955c3")
directory = Path("/home/realmagic/xy/atm-bench-20261110/results") / run_id
pointer = (directory / "snapshot-commit.json").read_bytes()
meta = directory / ".snapshots" / json.loads(pointer)["generation"] / "metadata.json"
state = json.loads(meta.read_text())["overview"]
assert state["status"] == "failed" and state["item_count"] == 64
work = api(8872, "active-work")
assert not any(w.get("worker_alive") for w in work["work"]), work
with sqlite3.connect(
    "file:/home/realmagic/xy/atm-bench-20261110/product-data/sentrix.db?mode=ro", uri=True
) as db:
    turns = dict(db.execute("select status,count(*) from agent_turns group by status"))
    assert not any(turns.get(k) for k in ["running", "processing"])
    assert (
        db.execute(
            "select count(*) from agent_turns where scope_id=? and status='pending'", (state["scope_id"],)
        ).fetchone()[0]
        == 0
    )
    jobs = {}
    for table in ["import_jobs", "video_jobs"]:
        jobs[table] = dict(db.execute(f"select status,count(*) from {table} group by status"))
        assert not any(
            jobs[table].get(k) for k in ["running", "processing", "queued", "indexing", "extracting"]
        )
    assert (
        db.execute(
            "select count(*) from scope_postprocess where processing_generation is not null"
        ).fetchone()[0]
        == 0
    )
progress = api(8291, "imports/progress")
assert progress["library"]["processing"] == 0 and progress["postprocess"]["state"] == "idle"
assert api(8291, "health")["status"] == "ok"
protected = {u: pid(u) for u in PROTECTED}
before = pid(UNIT)
save(
    "activate-intent.json",
    {
        "at": time.time(),
        "before_pid": before,
        "protected_pids": protected,
        "pointer_sha256": hashlib.sha256(pointer).hexdigest(),
        "turns": turns,
        "jobs": jobs,
        "progress": progress,
        "run_id": run_id,
    },
)
version = HERE / "version.env"
version.write_text(
    "PHOTOBENCH_BUILD_REVISION=4d3d477+q4-read-trace-reuse\n"
    "PHOTOBENCH_COMPRESS_SNAPSHOTS=1\nPHOTOBENCH_REUSE_TRACE_CHECKPOINTS=1\n"
)
dropin = Path.home() / ".config/systemd/user" / (UNIT + ".d") / "zzzz-q4-trace-reuse.conf"
assert not dropin.exists()
dropin.write_text(
    f"[Service]\nEnvironmentFile={version}\nExecStart=\nExecStart={PYTHON} "
    "-m uvicorn --factory photobench.main:create_app --host 0.0.0.0 --port 8872 "
    f"--app-dir {NEW}/apps/photobench/src\n"
)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
end = time.monotonic() + 120
while True:
    try:
        assert not any(w.get("worker_alive") for w in api(8872, "active-work")["work"])
        break
    except OSError:
        if time.monotonic() >= end:
            raise
        time.sleep(2)
latencies = []
for path in ["active-work", "runs?page=1&page_size=20&group_tasks=true"] * 3:
    t = time.monotonic()
    api(8872, path)
    latencies.append({"path": path, "seconds": time.monotonic() - t})
assert {u: pid(u) for u in PROTECTED} == protected
assert (directory / "snapshot-commit.json").read_bytes() == pointer
save(
    "activate-response.json",
    {
        "pid": pid(UNIT),
        "before_pid": before,
        "protected_pids": protected,
        "release": str(NEW),
        "latencies": latencies,
        "source_pointer_unchanged": True,
    },
)
print(json.dumps({"activated": True, "pid": pid(UNIT), "latencies": latencies}))
