"""Deploy the committed wait/chat changes once, only to idle PhotoBench 8874."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
OLD = BASE / "conversation-delete-9363d4b-v2/evaluation"
UNIT = "sentrix-atm100-doubao-photobench.service"
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
RELATIVE = "apps/photobench/src/photobench/application/run_execution/state.py"
DATABASE = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
PROTECTED = [
    "sentrix-atm100-api.service",
    "sentrix-atm100-photobench.service",
    "sentrix-atm100-model.service",
    "sentrix-atm100-wemm.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix-atm100-qwen37-gateway.service",
    "sentrix-atm100-doubao-gateway.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def pid(unit):
    return int(
        subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True)
    )


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=30) as response:
        return json.load(response)


def idle():
    assert not api(8874, "active-work")["work"]
    progress = api(8294, "imports/progress")
    assert progress["library"]["processing"] == 0 and progress["postprocess"]["state"] == "idle"
    with sqlite3.connect("file:" + DATABASE + "?mode=ro", uri=True) as db:
        assert not db.execute("select id from agent_turns where status in ('pending','running')").fetchall()


def environment(process):
    return dict(
        part.split("=", 1)
        for part in Path(f"/proc/{process}/environ").read_bytes().decode().split("\0")
        if "=" in part
    )


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    assert not (HERE / "deploy-intent.json").exists(), "Already consumed; inspect receipts."
    idle()
    old_pid = pid(UNIT)
    args = Path(f"/proc/{old_pid}/cmdline").read_bytes().decode().split("\0")
    assert args[args.index("--app-dir") + 1] == str(OLD / "apps/photobench/src")
    assert sha(OLD / RELATIVE) == manifest["baseline_state_sha256"]
    assert sha(HERE / "payload/state.py") == manifest["state_sha256"]
    for name, digest in manifest["static"].items():
        assert sha(HERE / "payload/dist" / name) == digest
    protected = {unit: pid(unit) for unit in PROTECTED}
    result_root = BASE / "cloud-results"
    run = json.loads((BASE / "active-runs.json").read_text())["qwen37"]["run_id"]
    result_manifest = result_root / run / "split-commit.json"
    result_sha = sha(result_manifest)
    folder = Path.home() / ".config/systemd/user" / (UNIT + ".d")
    matches = [p for p in folder.glob("*.conf") if str(OLD / "apps/photobench/src") in p.read_text()]
    assert len(matches) == 1, matches
    startup = matches[0]
    env_conf = folder / "zzzzzzzzz-full-lazy-envfile.conf"
    before = {str(p): p.read_text() for p in (startup, env_conf)}
    old_timeout = environment(old_pid).get("PHOTOBENCH_PERSIST_TIMEOUT_SECONDS")
    save(
        "deploy-intent.json",
        {
            "revision": manifest["revision"],
            "old_pid": old_pid,
            "protected": protected,
            "result_manifest_sha256": result_sha,
            "old_timeout_override": old_timeout,
            "overrides": before,
        },
    )
    shutil.copytree(OLD, HERE / "evaluation", symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(HERE / "payload/state.py", HERE / "evaluation" / RELATIVE)
    target_dist = HERE / "evaluation/apps/photobench/web/dist"
    shutil.rmtree(target_dist)
    shutil.copytree(HERE / "payload/dist", target_dist)
    env_file = HERE / "evaluation.env"
    env_file.write_text(f"PHOTOBENCH_WEB_DIST_ROOT={target_dist}\nPHOTOBENCH_PERSIST_TIMEOUT_SECONDS=300\n")
    idle()
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert pid(UNIT) == old_pid
    assert sha(result_manifest) == result_sha
    for path in (startup, env_conf):
        assert path.read_text() == before[str(path)]
    startup.write_text(
        before[str(startup)].replace(
            str(OLD / "apps/photobench/src"), str(HERE / "evaluation/apps/photobench/src")
        )
    )
    env_conf.write_text(before[str(env_conf)].rstrip() + f"\nEnvironmentFile={env_file}\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    for attempt in range(60):
        try:
            api(8874, "active-work")
            break
        except OSError:
            if attempt == 59:
                raise
            time.sleep(1)
    current_pid = pid(UNIT)
    assert current_pid != old_pid
    actual_env = environment(current_pid)
    actual_env["PYTHONPATH"] = str(HERE / "evaluation/apps/photobench/src")
    measured = json.loads(
        subprocess.check_output(
            [
                PYTHON,
                "-c",
                "import json; "
                "from photobench.application.run_execution.state import PERSIST_TIMEOUT_SECONDS; "
                'print(json.dumps({"seconds":PERSIST_TIMEOUT_SECONDS}))',
            ],
            env=actual_env,
            text=True,
        )
    )
    assert measured["seconds"] == 300
    args = Path(f"/proc/{current_pid}/cmdline").read_bytes().decode().split("\0")
    assert args[args.index("--app-dir") + 1] == actual_env["PYTHONPATH"]
    assert actual_env["PHOTOBENCH_WEB_DIST_ROOT"] == str(target_dist)
    assert sha(HERE / "evaluation" / RELATIVE) == manifest["state_sha256"]
    for name, digest in manifest["static"].items():
        with urllib.request.urlopen("http://127.0.0.1:8874/" + name, timeout=30) as response:
            assert hashlib.sha256(response.read()).hexdigest() == digest, name
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert sha(result_manifest) == result_sha
    idle()
    save(
        "deploy-response.json",
        {
            "revision": manifest["revision"],
            "pid": current_pid,
            "app_dir": actual_env["PYTHONPATH"],
            "persist_timeout_seconds": 300,
            "static_files_verified": len(manifest["static"]),
            "protected_pids_unchanged": True,
            "result_manifest_unchanged": True,
            "ports_restarted": [8874],
        },
    )


if __name__ == "__main__":
    main()
