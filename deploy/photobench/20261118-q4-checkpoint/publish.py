"""Stage a Q4-only release; activate only after the isolated checkpoint benchmark."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
OLD = BASE / "resume-release"
NEW = BASE / "q4-checkpoint-release"
RESULTS = Path("/home/realmagic/xy/atm-bench-20261110/results")
PYTHON = Path("/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python")
UNIT = "sentrix-atm100-photobench.service"
PROTECTED = [
    "sentrix-atm100-doubao-photobench.service",
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def api(port, path, timeout=15):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=timeout) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open("x") as handle:
        json.dump(value, handle, indent=2)


def stage():
    manifest = json.loads((HERE / "manifest.json").read_text())
    assert not NEW.exists()
    for name, row in manifest["files"].items():
        original = OLD / name
        assert (sha(original) if original.exists() else None) == row["before"], name
        assert sha(HERE / "overlay" / name) == row["after"], name
    save("stage-intent.json", {"revision": manifest["revision"], "at": time.time()})
    shutil.copytree(OLD, NEW, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name, row in manifest["files"].items():
        destination = NEW / name
        shutil.copy2(HERE / "overlay" / name, destination)
        assert sha(destination) == row["after"]
    save("stage-response.json", {"release": str(NEW), "revision": manifest["revision"], "sha_verified": True})
    print(json.dumps({"release": str(NEW), "staged": True}))


def activate(revision):
    assert (HERE / "stage-response.json").exists()
    bench = json.loads((HERE / "benchmark-result.json").read_text())
    assert bench["items"] == 51 and bench["source_pointer_unchanged"]
    assert bench["copy_seconds"] + bench["write_seconds"] < 90, bench
    mapping = json.loads((BASE / "active-runs.json").read_text())
    directory = RESULTS / mapping["q4km"]["run_id"]
    pointer = (directory / "snapshot-commit.json").read_bytes()
    generation = json.loads(pointer)["generation"]
    state = json.loads((directory / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    assert state["status"] == "failed" and state["item_count"] == 51
    assert state["failed_phase"] == "qa_eval" and state["scope_id"] == mapping["q4km"]["scope_id"]
    with sqlite3.connect(
        "file:/home/realmagic/xy/atm-bench-20261110/product-data/sentrix.db?mode=ro", uri=True, timeout=10
    ) as db:
        turns = dict(db.execute("select status,count(*) from agent_turns group by status"))
        assert not any(turns.get(s) for s in ["running", "processing"]), turns
        assert (
            db.execute(
                "select count(*) from scope_postprocess where processing_generation is not null"
            ).fetchone()[0]
            == 0
        )
    progress = api(8291, "imports/progress?scope_id=" + state["scope_id"])
    assert progress["library"]["processing"] == 0
    assert progress["postprocess"]["state"] == "idle"
    assert api(8291, "health")["status"] == "ok"
    protected = {unit: pid(unit) for unit in PROTECTED}
    before = pid(UNIT)
    save(
        "activate-intent.json",
        {
            "revision": revision,
            "run_id": state["run_id"],
            "pointer_sha256": hashlib.sha256(pointer).hexdigest(),
            "turns": turns,
            "protected_pids": protected,
            "before_pid": before,
            "at": time.time(),
        },
    )
    version = HERE / "version.env"
    version.write_text(
        f"PHOTOBENCH_BUILD_REVISION={revision}+q4-lossless-checkpoint\nPHOTOBENCH_COMPRESS_SNAPSHOTS=1\n"
    )
    dropin = Path.home() / ".config/systemd/user" / (UNIT + ".d") / "zzz-q4-checkpoint.conf"
    assert not dropin.exists()
    dropin.write_text(
        f"[Service]\nEnvironmentFile={version}\nExecStart=\nExecStart={PYTHON} -m uvicorn "
        "--factory photobench.main:create_app --host 0.0.0.0 --port 8872 "
        f"--app-dir {NEW}/apps/photobench/src\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    deadline = time.monotonic() + 120
    while True:
        try:
            work = api(8872, "active-work")
            assert not any(row.get("worker_alive") for row in work["work"]), work
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert (directory / "snapshot-commit.json").read_bytes() == pointer
    result = {
        "revision": revision,
        "release": str(NEW),
        "before_pid": before,
        "pid": pid(UNIT),
        "protected_pids": protected,
        "original_pointer_unchanged": True,
    }
    save("activate-response.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    if sys.argv[1] == "stage":
        stage()
    elif sys.argv[1] == "activate":
        activate(sys.argv[2])
    else:
        raise ValueError("Unknown operation")
