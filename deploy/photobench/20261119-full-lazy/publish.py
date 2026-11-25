"""Single-use release of indexed PhotoBench results on the two authorized evaluators."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
BASELINE = BASE / "trace-quarantine-fcb32bd/evaluation"
RELEASE = HERE / "evaluation"
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
STAGES = {
    "q4km": (8872, 8291, "sentrix-atm100-photobench.service"),
    "qwen37": (8874, 8294, "sentrix-atm100-doubao-photobench.service"),
}
DATA = {
    "q4km": Path("/home/realmagic/xy/atm-bench-20261110/product-data"),
    "qwen37": Path("/var/tmp/sentrix-atm-doubao-20261112/product-data"),
}
RESULTS = {"q4km": Path("/home/realmagic/xy/atm-bench-20261110/results"), "qwen37": BASE / "cloud-results"}
PROTECTED = [
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix-atm100-model.service",
    "sentrix-atm100-wemm.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save(name, value):
    with (HERE / name).open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def api(port, path, payload=None, timeout=45):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def manifest():
    return json.loads((HERE / "manifest.json").read_text())


def mapped(stage):
    row = json.loads((BASE / "active-runs.json").read_text())[stage]
    if row["run_id"] != manifest()["expected"][stage]:
        raise RuntimeError("active run mapping changed")
    return row


def metadata(stage):
    row = mapped(stage)
    directory = RESULTS[stage] / row["run_id"]
    pointer = directory / "snapshot-commit.json"
    generation = json.loads(pointer.read_text())["generation"]
    overview = json.loads((directory / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    return {
        "run_id": row["run_id"],
        "scope_id": row["scope_id"],
        "pointer_sha256": sha(pointer),
        "generation": generation,
        "item_count": overview["item_count"],
        "status": overview["status"],
    }


def idle(stage):
    source = metadata(stage)
    port, product, _ = STAGES[stage]
    if source["status"] not in {"failed", "interrupted", "cancelled", "completed_with_errors", "completed"}:
        raise RuntimeError("source run is still active")
    if api(port, "active-work")["work"]:
        raise RuntimeError("evaluator worker is active")
    with sqlite3.connect(f"file:{DATA[stage]}/sentrix.db?mode=ro", uri=True) as db:
        turns = list(
            db.execute(
                "select id,status from agent_turns where scope_id=? and status in ('pending','running')",
                (source["scope_id"],),
            )
        )
    if turns:
        raise RuntimeError(f"product turns are active: {turns}")
    imports = api(product, "imports/progress?scope_id=" + source["scope_id"])
    if imports["library"].get("processing") or imports["postprocess"].get("state") != "idle":
        raise RuntimeError("product pipeline is active")
    return source


def stage():
    info = manifest()
    if RELEASE.exists():
        raise RuntimeError("candidate directory already exists")
    for key in STAGES:
        mapped(key)
    for name, record in info["files"].items():
        old = BASELINE / name
        actual = sha(old) if old.is_file() else None
        if actual != record["before"]:
            raise RuntimeError("baseline file changed: " + name)
        if sha(HERE / "payload" / name) != record["after"]:
            raise RuntimeError("payload hash mismatch: " + name)
    save("stage-intent.json", {"revision": info["revision"], "expected": info["expected"]})
    shutil.copytree(
        BASELINE, RELEASE, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    for name in info["files"]:
        dest = RELEASE / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "payload" / name, dest)
    env = {**os.environ, "PYTHONPATH": str(RELEASE / "apps/photobench/src")}
    tests = [
        "test_split_results.py",
        "test_run_reports.py",
        "test_run_read.py",
        "test_resume_quarantine.py",
        "test_trace_checkpoint_reuse.py",
        "test_run_execution.py",
        "test_process_diagnostics.py",
        "test_suites.py",
    ]
    with (HERE / "candidate-tests.log").open("x") as log:
        subprocess.run(
            [PYTHON, "-m", "pytest", "-o", "addopts=", "-q", *[f"apps/photobench/tests/{t}" for t in tests]],
            cwd=RELEASE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    save(
        "stage-response.json",
        {"revision": info["revision"], "tests": (HERE / "candidate-tests.log").read_text()[-1500:]},
    )


def cancel():
    if not (HERE / "stage-response.json").is_file():
        raise RuntimeError("candidate is unverified")
    sources = {s: metadata(s) for s in STAGES}
    save("cancel-intent.json", sources)
    for s, (port, _, _) in STAGES.items():
        if sources[s]["status"] in {"pending", "running", "cancelling"}:
            response = api(port, f"runs/{sources[s]['run_id']}/cancel", {}, timeout=300)
            save(s + "-cancel-response.json", response)
    deadline = time.monotonic() + 300
    pending = set(STAGES)
    while pending and time.monotonic() < deadline:
        for s in list(pending):
            try:
                idle(s)
                pending.remove(s)
            except (RuntimeError, OSError):
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise RuntimeError("cancellation did not become idle: " + str(pending))
    save("cancel-response.json", {s: idle(s) for s in STAGES})


def migrate():
    from photobench.infrastructure.results.index_legacy import build_index
    from photobench.infrastructure.results.split_store import SplitRunReader

    sources = {s: idle(s) for s in STAGES}
    save("migrate-intent.json", sources)
    for s, source in sources.items():
        result = build_index(RESULTS[s], source["run_id"])
        reader = SplitRunReader(RESULTS[s], RESULTS[s] / source["run_id"])
        assert reader.overview()["item_count"] == source["item_count"]
        assert reader.overview()["qa_count"] == 1013
        assert metadata(s) == source
        save(s + "-migration-response.json", result)
    save("migrate-response.json", {"sources": sources})


def activate():
    if not (HERE / "migrate-response.json").is_file():
        raise RuntimeError("source migration is incomplete")
    sources = {s: idle(s) for s in STAGES}
    protected = {u: pid(u) for u in PROTECTED}
    save("activate-intent.json", {"sources": sources, "protected_pids": protected})
    version = HERE / "full-lazy-version.env"
    with version.open("x") as f:
        f.write(
            "PHOTOBENCH_BUILD_REVISION=" + manifest()["revision"]
            + "+100-full-lazy\nPHOTOBENCH_SPLIT_RESULTS=1\nPHOTOBENCH_WEB_DIST_ROOT="
            + str(HERE / "web-dist") + "\n"
        )
    for s, (port, _, unit) in STAGES.items():
        drop = Path.home() / ".config/systemd/user" / f"{unit}.d/zzzzzzzz-full-lazy.conf"
        with drop.open("x") as f:
            f.write(
                "[Service]\nExecStart=\nExecStart="
                + PYTHON
                + " -m uvicorn --factory photobench.main:create_app --host 0.0.0.0 --port "
                + str(port)
                + " --app-dir "
                + str(RELEASE / "apps/photobench/src")
                + "\nEnvironmentFile="
                + str(version)
                + "\n"
            )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", unit], check=True)
        for _ in range(60):
            try:
                idle(s)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("evaluator did not become ready")
        overview = api(port, f"runs/{sources[s]['run_id']}?include_history=false")
        if overview.get("storage_format") != 3:
            raise RuntimeError("split read path is not active")
        if metadata(s) != sources[s]:
            raise RuntimeError("original checkpoint was changed")
    if {u: pid(u) for u in PROTECTED} != protected:
        raise RuntimeError("out-of-scope PID changed")
    save(
        "activate-response.json",
        {"pids": {s: pid(v[2]) for s, v in STAGES.items()}, "protected_pids": protected, "sources": sources},
    )


def resume(stage, wait_id):
    import uuid

    uuid.UUID(wait_id)
    if not (HERE / "activate-response.json").is_file():
        raise RuntimeError("release is not active")
    source = idle(stage)
    port = STAGES[stage][0]
    plan = api(port, f"runs/{source['run_id']}/recovery-plan", timeout=300)
    if not plan["can_resume"] or plan["inherited_qa_count"] + plan["remaining_qa_count"] != 1013:
        raise RuntimeError("invalid preserved resume plan")
    if (
        plan["scope_id"] != source["scope_id"]
        or plan["frozen_scoring_profiles"] != ["atm_qs_v1"]
        or plan["judge_use_gt_images"]
    ):
        raise RuntimeError("frozen evaluation protocol changed")
    payload = {"repair_missing_judges": True}
    save(
        stage + "-resume-intent.json",
        {"source": source, "plan": plan, "wait_id": wait_id, "payload": payload},
    )
    response = api(port, f"runs/{source['run_id']}/resume", payload, timeout=300)
    save(stage + "-resume-response.json", response)
    if response["resumed_items"] != plan["inherited_qa_count"] or response["scope_id"] != source["scope_id"]:
        raise RuntimeError("resume receipt mismatch")
    if stage == "q4km" and response.get("trace_quarantined_items") != 18:
        raise RuntimeError("18 quarantine markers were lost")
    mapping = json.loads((BASE / "active-runs.json").read_text())
    mapped(stage)
    mapping[stage] = response
    temporary = BASE / f".active-runs-full-lazy-{stage}.tmp"
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / "active-runs.json")
    print(json.dumps(response), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("action", choices=["stage", "cancel", "migrate", "activate", "resume"])
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.action == "resume":
        resume(args.stage, args.wait_id)
    else:
        globals()[args.action]()
