"""Single-use deployment of conversation deletion to idle 8294/8874 only."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
OLD = {
    "product": BASE / "reasoning-bounds-ffec2be/release/qwen37",
    "evaluation": BASE / "trace-viewer-ui-4df9302/evaluation",
}
UNITS = {
    "product": "sentrix-atm100-doubao-api.service",
    "evaluation": "sentrix-atm100-doubao-photobench.service",
}
PROTECTED = [
    "sentrix-atm100-api.service",
    "sentrix-atm100-photobench.service",
    "sentrix-atm100-model.service",
    "sentrix-atm100-wemm.service",
    "sentrix-atm100-qwen37-gateway.service",
    "sentrix-atm100-doubao-gateway.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]
DATABASE = Path("/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db")
RESULTS = Path("/var/tmp/sentrix-atm-doubao-20261112/results")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, data):
    with (HERE / name).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def app_dir(unit):
    args = Path("/proc/" + pid(unit) + "/cmdline").read_bytes().decode().split("\0")
    return args[args.index("--app-dir") + 1]


def api(port, path, payload=None, method="GET"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def idle():
    assert not api(8874, "active-work")["work"]
    progress = api(8294, "imports/progress")
    assert not progress["library"]["processing"] and progress["postprocess"]["state"] == "idle"
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE status IN ('pending','running')").fetchall()


def result_hashes():
    mapping = json.loads((BASE / "active-runs.json").read_text())
    run_id = mapping["qwen37"]["run_id"]
    assert run_id.endswith("0bbacb"), run_id
    return {str(RESULTS / run_id / "split-commit.json"): sha(RESULTS / run_id / "split-commit.json")}


def stage():
    manifest = json.loads((HERE / "manifest.json").read_text())
    assert not (HERE / "stage-intent.json").exists()
    for kind, root in OLD.items():
        suffix = "apps/api/src" if kind == "product" else "apps/photobench/src"
        assert app_dir(UNITS[kind]) == str(root / suffix)
    for name, spec in manifest["files"].items():
        assert sha(HERE / "payload" / name) == spec["sha256"]
        if spec["baseline_sha256"] is not None:
            assert sha(OLD[spec["kind"]] / name) == spec["baseline_sha256"], name
    save(
        "stage-intent.json",
        {"source_revision": manifest["revision"], "old": {k: str(v) for k, v in OLD.items()}},
    )
    for kind, root in OLD.items():
        shutil.copytree(root, HERE / kind, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name, spec in manifest["files"].items():
        target = HERE / spec["kind"] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / "payload" / name, target)
        assert sha(target) == spec["sha256"]
    save("stage-response.json", {"revision": manifest["revision"], "files": len(manifest["files"])})


def activate():
    assert (HERE / "stage-response.json").exists()
    assert not (HERE / "activate-intent.json").exists()
    idle()
    manifests = result_hashes()
    protected = {unit: pid(unit) for unit in PROTECTED}
    edits = {}
    home = Path.home() / ".config/systemd/user"
    for kind, unit in UNITS.items():
        suffix = "apps/api/src" if kind == "product" else "apps/photobench/src"
        original = str(OLD[kind] / suffix)
        assert app_dir(unit) == original
        matches = [p for p in (home / (unit + ".d")).glob("*.conf") if original in p.read_text()]
        assert len(matches) == 1, matches
        path = matches[0]
        edits[path] = path.read_text().replace(original, str(HERE / kind / suffix))
    env_paths = {
        "product": home / (UNITS["product"] + ".d/zzzzz-reasoning-bounds.conf"),
        "evaluation": home / (UNITS["evaluation"] + ".d/zzzzzzzzz-full-lazy-envfile.conf"),
    }
    for kind, path in env_paths.items():
        variable = "SENTRIX_WEB_DIST_DIR" if kind == "product" else "PHOTOBENCH_WEB_DIST_ROOT"
        web = "apps/web/dist" if kind == "product" else "apps/photobench/web/dist"
        env = HERE / (kind + "-web.env")
        env.write_text(f"{variable}={HERE / kind / web}\n")
        edits[path] = edits.get(path, path.read_text()).rstrip() + f"\nEnvironmentFile={env}\n"
    before = {str(p): sha(p) for p in edits}
    for path in edits:
        shutil.copy2(path, HERE / (path.parent.name + "-" + path.name + ".before"))
    save(
        "activate-intent.json",
        {
            "protected": protected,
            "results": manifests,
            "overrides": before,
            "previous_pids": {k: pid(u) for k, u in UNITS.items()},
        },
    )
    idle()
    for path, value in edits.items():
        assert sha(path) == before[str(path)]
        path.write_text(value)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for unit in UNITS.values():
        subprocess.run(["systemctl", "--user", "restart", unit], check=True)
    for port in [8294, 8874]:
        for attempt in range(60):
            try:
                api(port, "diagnostics" if port == 8294 else "active-work")
                break
            except (OSError, urllib.error.URLError):
                if attempt == 59:
                    raise
                time.sleep(1)
    for kind, unit in UNITS.items():
        suffix = "apps/api/src" if kind == "product" else "apps/photobench/src"
        assert app_dir(unit) == str(HERE / kind / suffix)
    assert {u: pid(u) for u in PROTECTED} == protected
    assert result_hashes() == manifests
    idle()
    save(
        "activate-response.json",
        {
            "ports": [8294, 8874],
            "pids": {k: pid(u) for k, u in UNITS.items()},
            "protected_pids_unchanged": True,
            "result_manifest_unchanged": True,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["stage", "activate"])
    args = parser.parse_args()
    {"stage": stage, "activate": activate}[args.operation]()
