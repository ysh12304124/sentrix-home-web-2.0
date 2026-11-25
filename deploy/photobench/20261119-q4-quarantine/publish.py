"""Single-use publication and preserved continuation for the two authorized ATM runs."""

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
BASELINE = BASE / "oom-read-47523cd/evaluation"
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
RESULTS = {
    "q4km": Path("/home/realmagic/xy/atm-bench-20261110/results"),
    "qwen37": BASE / "cloud-results",
}
EXPECTED = {
    "q4km": "20261119-021603-2026.11.17-atm-sources-addresses-current-resume-62cc28",
    "qwen37": "20261119-013722-2026.11.17-atm-sources-addresses-current-resume-842598",
}
PROTECTED = [
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix-atm100-model.service",
    "sentrix-atm100-wemm.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(name: str, value: object) -> None:
    with (HERE / name).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def api(port: int, path: str, payload: object = None, timeout: int = 45) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def pid(unit: str) -> str:
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def metadata(stage: str) -> dict:
    source = json.loads((BASE / "active-runs.json").read_text())[stage]
    if source["run_id"] != EXPECTED[stage]:
        raise RuntimeError("active run mapping changed; inspect before proceeding")
    directory = RESULTS[stage] / source["run_id"]
    pointer = directory / "snapshot-commit.json"
    generation = json.loads(pointer.read_text())["generation"]
    overview = json.loads((directory / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    return {
        "run_id": source["run_id"],
        "scope_id": source["scope_id"],
        "pointer_sha256": sha(pointer),
        "generation": generation,
        "item_count": overview["item_count"],
        "status": overview["status"],
    }


def idle(stage: str) -> dict:
    port, product, _ = STAGES[stage]
    source = metadata(stage)
    if source["status"] not in {"failed", "interrupted"}:
        raise RuntimeError(f"source run is not terminal: {source}")
    work = api(port, "active-work")["work"]
    if work:
        raise RuntimeError(f"evaluator still has active work: {work}")
    with sqlite3.connect(f"file:{DATA[stage]}/sentrix.db?mode=ro", uri=True) as db:
        turns = list(
            db.execute(
                "select id,status from agent_turns where scope_id=? and status in ('pending','running')",
                (source["scope_id"],),
            )
        )
    if turns:
        raise RuntimeError(f"product still has active turns: {turns}")
    imports = api(product, "imports/progress?scope_id=" + source["scope_id"])
    if imports["library"].get("processing") or imports["postprocess"].get("state") != "idle":
        raise RuntimeError(f"product pipeline is still active: {imports}")
    return source


def stage_release() -> None:
    manifest = json.loads((HERE / "manifest.json").read_text())
    if RELEASE.exists():
        raise RuntimeError("candidate release already exists")
    sources = {stage: idle(stage) for stage in STAGES}
    for relative, record in manifest["files"].items():
        if record["before"] is not None and sha(BASELINE / relative) != record["before"]:
            raise RuntimeError(f"baseline file changed: {relative}")
        if sha(HERE / "payload" / relative) != record["after"]:
            raise RuntimeError(f"payload checksum mismatch: {relative}")
    save("stage-intent.json", {"sources": sources, "revision": manifest["revision"]})
    shutil.copytree(
        BASELINE, RELEASE, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    for relative in manifest["files"]:
        target = RELEASE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "payload" / relative, target)
    tests = [
        "apps/photobench/tests/test_suites.py",
        "apps/photobench/tests/test_resume_quarantine.py",
        "apps/photobench/tests/test_quarantine_stream_filter.py",
        "apps/photobench/tests/test_trace_checkpoint_reuse.py",
        "apps/photobench/tests/test_run_execution.py::test_resume_recovers_missing_judges_without_reexecuting_prior_agents",
    ]
    env = {**os.environ, "PYTHONPATH": str(RELEASE / "apps/photobench/src")}
    with (HERE / "candidate-tests.txt").open("x") as log:
        subprocess.run(
            [PYTHON, "-m", "pytest", "-o", "addopts=", "-q", *tests],
            cwd=RELEASE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    for relative, record in manifest["files"].items():
        if sha(RELEASE / relative) != record["after"]:
            raise RuntimeError(f"candidate checksum mismatch: {relative}")
    save(
        "stage-response.json",
        {"revision": manifest["revision"], "tests": (HERE / "candidate-tests.txt").read_text()[-2000:]},
    )


def activate() -> None:
    if not (HERE / "stage-response.json").is_file():
        raise RuntimeError("candidate tests have not passed")
    manifest = json.loads((HERE / "manifest.json").read_text())
    sources = {stage: idle(stage) for stage in STAGES}
    protected = {unit: pid(unit) for unit in PROTECTED}
    save("activate-intent.json", {"sources": sources, "protected_pids": protected})
    for stage, (port, _, unit) in STAGES.items():
        drop = Path.home() / ".config/systemd/user" / f"{unit}.d/zzzzzzz-trace-quarantine.conf"
        with drop.open("x") as handle:
            handle.write(
                "[Service]\nExecStart=\nExecStart="
                + PYTHON
                + " -m uvicorn --factory photobench.main:create_app --host 0.0.0.0 --port "
                + str(port)
                + " --app-dir "
                + str(RELEASE / "apps/photobench/src")
                + "\n"
                + "Environment=PHOTOBENCH_BUILD_REVISION="
                + manifest["revision"]
                + "+100-trace-quarantine\n"
            )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", unit], check=True)
        for _ in range(45):
            try:
                idle(stage)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError(f"evaluator did not become ready: {stage}")
        if metadata(stage) != sources[stage]:
            raise RuntimeError("source checkpoint changed during publication")
    if protected != {unit: pid(unit) for unit in PROTECTED}:
        raise RuntimeError("an out-of-scope service PID changed")
    save(
        "activate-response.json",
        {
            "revision": manifest["revision"],
            "sources_unchanged": True,
            "pids": {stage: pid(info[2]) for stage, info in STAGES.items()},
            "protected_pids": protected,
        },
    )


def resume(stage: str, wait_id: str) -> None:
    import uuid

    uuid.UUID(wait_id)
    source = idle(stage)
    port = STAGES[stage][0]
    plan = api(port, f"runs/{source['run_id']}/recovery-plan", timeout=300)
    save(f"{stage}-recovery-plan.json", plan)
    if not plan["can_resume"] or plan["scope_id"] != source["scope_id"]:
        raise RuntimeError("resume plan does not match the original scope")
    if plan["inherited_qa_count"] + plan["remaining_qa_count"] != 1013:
        raise RuntimeError("resume plan changed the 1013-question set")
    if plan["frozen_scoring_profiles"] != ["atm_qs_v1"] or plan["judge_use_gt_images"]:
        raise RuntimeError("frozen scoring protocol changed")
    payload = {"repair_missing_judges": True}
    save(
        f"{stage}-resume-intent.json",
        {"source": source, "wait_id": wait_id, "payload": payload, "plan": plan},
    )
    response = api(port, f"runs/{source['run_id']}/resume", payload, timeout=300)
    save(f"{stage}-resume-response.json", response)
    if response["resumed_items"] != plan["inherited_qa_count"] or response["scope_id"] != source["scope_id"]:
        raise RuntimeError("resume receipt does not match the preserved checkpoint")
    if stage == "q4km" and response.get("trace_quarantined_items") != 18:
        raise RuntimeError("Q4 resume did not inherit all 18 quarantine markers")
    mapping = json.loads((BASE / "active-runs.json").read_text())
    if mapping[stage]["run_id"] != EXPECTED[stage]:
        raise RuntimeError("active mapping changed before publishing the resume receipt")
    mapping[stage] = response
    temporary = BASE / f".active-runs-{stage}.json.tmp"
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / "active-runs.json")
    print(json.dumps(response))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("action", choices=["stage", "activate", "resume"])
    parser.add_argument("--stage", choices=list(STAGES))
    parser.add_argument("--wait-id")
    arguments = parser.parse_args()
    if arguments.action == "stage":
        stage_release()
    elif arguments.action == "activate":
        activate()
    elif arguments.stage and arguments.wait_id:
        resume(arguments.stage, arguments.wait_id)
    else:
        parser.error("resume requires --stage and --wait-id")
