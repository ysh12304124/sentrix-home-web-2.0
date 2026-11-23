"""Publish one committed context-compaction patch while both services are idle."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from common import (
    AFTER,
    APP,
    BEFORE,
    BUILD,
    DB,
    EVALUATOR,
    FIX_COMMIT,
    HERE,
    OLD,
    PRODUCT,
    RELATIVE,
    RELEASE,
    TERMINAL,
    api,
    command,
    environment,
    fingerprint,
    frozen_manifests,
    idle,
    pid,
    preserved,
    read,
    runtime,
    save,
    sha,
    source,
)

PROTECTED = (
    "sentrix-atm100-api.service",
    "sentrix-atm100-photobench.service",
    "sentrix-atm100-model.service",
    "sentrix-atm100-wemm.service",
    "sentrix-atm100-qwen37-gateway.service",
    "sentrix-atm100-doubao-gateway.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
)


def tree(path):
    return {
        str(p.relative_to(path)): sha(p)
        for p in path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def application_environment(process):
    return {
        k: v for k, v in environment(process).items() if k.startswith(("SENTRIX_", "PHOTOBENCH_", "BENCH_"))
    }


def prepare():
    assert not (HERE / "prepare-intent.json").exists() and not RELEASE.exists()
    source()
    activity = idle()
    diagnostics, config = runtime()
    assert {PRODUCT: pid(PRODUCT), EVALUATOR: pid(EVALUATOR)} == {PRODUCT: 11871, EVALUATOR: 1588}
    assert diagnostics["build_revision"] == config["runtime_config"]["product_build_revision"]
    assert sha(OLD / RELATIVE) == BEFORE
    candidate = HERE / "candidate-run_turn.py"
    validation = json.loads((TERMINAL / "candidate-validation.json").read_text())
    assert validation["accepted"] and validation["candidate_sha256"] == sha(candidate) == AFTER
    save(
        "prepare-intent.json",
        {
            "activity": activity,
            "source_manifests": frozen_manifests(),
            "old_source": str(OLD),
            "candidate_sha256": AFTER,
        },
    )
    shutil.copytree(OLD, RELEASE, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(candidate, RELEASE / RELATIVE)
    build = json.loads((RELEASE / "build-info.json").read_text())
    build.update(
        revision=BUILD,
        source_product_revision=diagnostics["build_revision"],
        patch_commit=FIX_COMMIT,
        patch_files=[RELATIVE],
    )
    (RELEASE / "build-info.json").write_text(json.dumps(build, indent=2) + "\n")
    old, new = tree(OLD), tree(RELEASE)
    assert old.keys() == new.keys()
    changed = sorted(k for k in old if old[k] != new[k])
    assert changed == sorted([RELATIVE, "build-info.json"]), changed
    save(
        "prepare-response.json",
        {
            "accepted": True,
            "changed_files": changed,
            "product_source_sha256": AFTER,
            "build_revision": BUILD,
            "old_source_tree": old,
            "candidate_tree": new,
            "source_manifests_unchanged": preserved(read("prepare-intent.json")["source_manifests"]),
        },
    )
    print(json.dumps({"prepared": True, "changed_files": changed, "business_deployment": False}), flush=True)


def wait(port, route):
    deadline = time.monotonic() + 120
    while True:
        try:
            return api(port, route)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def publish():
    assert not (HERE / "publish-intent.json").exists()
    prepared = read("prepare-response.json")
    assert prepared["accepted"] and tree(RELEASE) == prepared["candidate_tree"]
    assert tree(OLD) == prepared["old_source_tree"]
    source()
    activity = idle()
    diagnostics, config = runtime()
    before_pids = {u: pid(u) for u in (PRODUCT, EVALUATOR)}
    assert before_pids == {PRODUCT: 11871, EVALUATOR: 1588}
    args = command(before_pids[PRODUCT])
    assert args[args.index("--app-dir") + 1] == str(OLD / "apps/api/src")
    before_env = {u: application_environment(p) for u, p in before_pids.items()}
    protected = {u: pid(u) for u in PROTECTED}
    manifests = frozen_manifests()
    mapping_path = HERE.parent / "active-runs.json"
    mapping_sha = sha(mapping_path)
    folder = Path.home() / ".config/systemd/user" / (PRODUCT + ".d")
    override = folder / "zzzzzzzzzzzzzzzz-context-preview-20261124.conf"
    assert not override.exists() and override.name > max(p.name for p in folder.glob("*.conf"))
    cfg_path = Path(config["runtime_config_file"])
    before_cfg = json.loads(cfg_path.read_text())
    updated = {**before_cfg, "product_build_revision": BUILD}
    save("runtime-before.json", before_cfg)
    args[args.index("--app-dir") + 1] = str(RELEASE / "apps/api/src")
    save(
        "publish-intent.json",
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "activity": activity,
            "old_pids": before_pids,
            "protected_pids": protected,
            "source_manifests": manifests,
            "active_mapping_sha256": mapping_sha,
            "override": str(override),
            "command": args,
            "config_path": str(cfg_path),
            "old_diagnostics": diagnostics,
            "old_runtime_config": config["runtime_config"],
            "old_environment_fingerprints": {u: fingerprint(v) for u, v in before_env.items()},
            "restarted_ports": [8294, 8874],
            "evaluator_code_change": False,
            "reason_for_evaluator_restart": (
                "Load the new product build revision for explicit resume version validation."
            ),
        },
    )
    # The existing dataset, settings and evaluator code remain in their original directories.
    override.write_text("[Service]\nExecStart=\nExecStart=" + shlex.join(args) + "\n")
    temporary = HERE / "runtime-connection.next.json"
    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(cfg_path.stat().st_mode & 0o777)
    os.replace(temporary, cfg_path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for unit, port, route in [(PRODUCT, 8294, "health/live"), (EVALUATOR, 8874, "active-work")]:
        subprocess.run(["systemctl", "--user", "restart", unit], check=True, timeout=90)
        wait(port, route)
    new_pids = {u: pid(u) for u in (PRODUCT, EVALUATOR)}
    assert all(new_pids[u] != before_pids[u] for u in new_pids)
    assert {u: pid(u) for u in PROTECTED} == protected
    assert {u: fingerprint(application_environment(p)) for u, p in new_pids.items()} == {
        u: fingerprint(v) for u, v in before_env.items()
    }
    assert command(new_pids[PRODUCT])[command(new_pids[PRODUCT]).index("--app-dir") + 1] == str(
        RELEASE / "apps/api/src"
    )
    assert command(new_pids[EVALUATOR])[command(new_pids[EVALUATOR]).index("--app-dir") + 1] == str(APP)
    after_diagnostics, after_config = runtime()
    assert (
        after_diagnostics["build_revision"]
        == after_config["runtime_config"]["product_build_revision"]
        == BUILD
    )
    assert after_diagnostics["runtime_configuration"] == diagnostics["runtime_configuration"]
    assert after_config["runtime_config"] == {**config["runtime_config"], "product_build_revision": BUILD}
    assert json.loads(cfg_path.read_text()) == updated
    assert sha(RELEASE / RELATIVE) == AFTER and tree(OLD) == prepared["old_source_tree"]
    assert preserved(manifests) and sha(mapping_path) == mapping_sha
    after_activity = idle()
    save(
        "publish-response.json",
        {
            "accepted": True,
            "finished_at": datetime.now().astimezone().isoformat(),
            "pids": new_pids,
            "product_app_dir": str(RELEASE / "apps/api/src"),
            "evaluation_app_dir": str(APP),
            "product_source_sha256": AFTER,
            "build_revision": BUILD,
            "runtime_configuration_unchanged": True,
            "runtime_config_only_build_revision_changed": True,
            "source_manifests_unchanged": True,
            "old_product_tree_unchanged": True,
            "protected_pids_unchanged": True,
            "application_environments_unchanged": True,
            "database": str(DB),
            "activity": after_activity,
            "restarted_ports": [8294, 8874],
            "evaluator_code_change": False,
        },
    )
    print(json.dumps({"published": True, "pids": new_pids, "build_revision": BUILD}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["prepare", "publish"])
    args = parser.parse_args()
    {"prepare": prepare, "publish": publish}[args.operation]()
