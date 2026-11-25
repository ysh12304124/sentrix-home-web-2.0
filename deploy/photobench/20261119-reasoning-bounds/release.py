"""One-use publication and continuation of the two authorized ATM1013 runs."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
PRODUCTS = {
    "q4km": (
        8291,
        "sentrix-atm100-api.service",
        "/home/realmagic/xy/releases/psh-b2030e1-100-20261117-8291",
        "/home/realmagic/xy/atm-bench-20261110/product-data",
    ),
    "qwen37": (
        8294,
        "sentrix-atm100-doubao-api.service",
        "/home/realmagic/xy/releases/psh-b2030e1-100-20261117",
        "/var/tmp/sentrix-atm-doubao-20261112/product-data",
    ),
}
EVALUATORS = {
    "q4km": (8872, "sentrix-atm100-photobench.service", "/home/realmagic/xy/atm-bench-20261110/results"),
    "qwen37": (8874, "sentrix-atm100-doubao-photobench.service", str(BASE / "cloud-results")),
}
OLD_EVALUATION = BASE / "q4-trace-reuse-release"
NEW = HERE / "release"
PROTECTED = ["sentrix2-api.service", "sentrix2-photobench.service", "sentrix-atm100-model.service"]
LIMITS = {
    "reasoning_max_model_calls": 100,
    "reasoning_max_tool_calls": 600,
    "reasoning_max_search_calls": 100,
    "reasoning_max_other_tool_calls": 500,
}


def call(*args):
    return subprocess.check_output(args, text=True).strip()


def pid(unit):
    return call("systemctl", "--user", "show", unit, "-p", "MainPID", "--value")


def environment(unit):
    values = dict(
        row.decode().split("=", 1)
        for row in Path(f"/proc/{pid(unit)}/environ").read_bytes().split(b"\0")
        if b"=" in row
    )
    return {k: values.get(k) for k in ["PHOTOBENCH_CONFIG_ROOT", "SENTRIX_VLM_REASONING_CONTEXT_TOKENS"]}


def api(port, path, payload=None, timeout=15):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open("x") as handle:
        json.dump(value, handle, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def source_state(stage):
    mapping = json.loads((BASE / "active-runs.json").read_text())
    directory = Path(EVALUATORS[stage][2]) / mapping[stage]["run_id"]
    pointer = directory / "snapshot-commit.json"
    generation = json.loads(pointer.read_text())["generation"]
    state = json.loads((directory / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    return {
        "run_id": mapping[stage]["run_id"],
        "pointer_sha256": sha(pointer),
        "overview": {
            k: state.get(k)
            for k in [
                "scope_id",
                "status",
                "item_count",
                "qa_count",
                "reasoning",
                "graph_enabled",
                "execution_budgets",
            ]
        },
    }


def inspect():
    manifest = json.loads((HERE / "manifest.json").read_text())
    result = {
        "at": time.time(),
        "protected": {u: pid(u) for u in PROTECTED},
        "products": {},
        "evaluators": {},
        "source_runs": {s: source_state(s) for s in PRODUCTS},
    }
    for stage, (port, unit, root, data) in PRODUCTS.items():
        with sqlite3.connect(f"file:{data}/sentrix.db?mode=ro", uri=True) as db:
            db.execute("pragma query_only=on")
            turns = list(
                db.execute(
                    "select id,status,cancel_requested from agent_turns "
                    "where status in ('pending','running','processing')"
                )
            )
            jobs = {
                t: list(db.execute(f"select status,count(*) from {t} group by status"))
                for t in ["import_jobs", "video_jobs"]
            }
            postprocess = db.execute(
                "select count(*) from scope_postprocess where processing_generation is not null"
            ).fetchone()[0]
        result["products"][stage] = {
            "pid": pid(unit),
            "environment": environment(unit),
            "turns": turns,
            "jobs": jobs,
            "postprocess": postprocess,
            "imports": api(port, "imports/progress"),
            "source": {f: sha(Path(root) / f) for f in manifest["product_files"]},
        }
    for stage, (port, unit, _) in EVALUATORS.items():
        result["evaluators"][stage] = {
            "pid": pid(unit),
            "environment": environment(unit),
            "work": api(port, "active-work"),
        }
    return result


def require_idle(before):
    for stage, product in before["products"].items():
        # Ten pre-existing cancelled Nov10 smoke turns remain pending in the database.
        assert all(
            status == "pending" and cancelled and turn.startswith("turn_20261110")
            for turn, status, cancelled in product["turns"]
        ), (stage, product["turns"])
        assert product["postprocess"] == 0
        assert product["imports"]["library"]["processing"] == 0
        assert product["imports"]["postprocess"]["state"] == "idle"
        for table, statuses in product["jobs"].items():
            assert not any(
                count
                and status
                in {
                    "running",
                    "processing",
                    "queued",
                    "indexing",
                    "extracting",
                    "retry_scheduled",
                    "waiting_dependency",
                }
                for status, count in statuses
            ), (stage, table, statuses)
    for stage, evaluator in before["evaluators"].items():
        assert not any(w.get("worker_alive") for w in evaluator["work"]["work"]), stage
    assert all(s["overview"]["status"] == "cancelled" for s in before["source_runs"].values())


def stage_release():
    assert not NEW.exists()
    manifest = json.loads((HERE / "manifest.json").read_text())
    before = inspect()
    require_idle(before)
    for product in before["products"].values():
        assert product["source"] == {f: v["before"] for f, v in manifest["product_files"].items()}
    assert {f: sha(OLD_EVALUATION / f) for f in manifest["evaluation_files"]} == {
        f: v["before"] for f, v in manifest["evaluation_files"].items()
    }
    save("stage-intent.json", before)
    ignore = shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", ".pytest_cache")
    for stage, (_, _, root, _) in PRODUCTS.items():
        target = NEW / stage
        shutil.copytree(root, target, symlinks=True, ignore=ignore)
        for f, values in manifest["product_files"].items():
            source = HERE / "payload" / f
            assert sha(source) == values["after"]
            (target / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / f)
        info = json.loads((target / "build-info.json").read_text())
        info["revision"] = manifest["deployed_version"]
        (target / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
    target = NEW / "evaluation"
    shutil.copytree(OLD_EVALUATION, target, symlinks=True, ignore=ignore)
    for f, values in manifest["evaluation_files"].items():
        source = HERE / "payload" / f
        assert sha(source) == values["after"]
        shutil.copy2(source, target / f)
    save("stage-response.json", {"release": str(NEW), "source_revision": manifest["source_revision"]})


def wait(port):
    for _ in range(120):
        try:
            api(port, "health/live" if port < 8700 else "active-work", timeout=2)
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"{port}: startup deadline exceeded")


def activate():
    assert (HERE / "remote-tests-passed.json").exists()
    before = inspect()
    require_idle(before)
    save("activation-intent.json", before)
    manifest = json.loads((HERE / "manifest.json").read_text())
    # Stop both schedulers first; no new questions can race product publication.
    for _, unit, _ in EVALUATORS.values():
        subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=80)
    for _, unit, _, _ in PRODUCTS.values():
        subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=80)
    version = HERE / "version.env"
    version.write_text(
        f"PHOTOBENCH_BUILD_REVISION={manifest['deployed_version']}\n"
        "PHOTOBENCH_COMPRESS_SNAPSHOTS=1\nPHOTOBENCH_REUSE_TRACE_CHECKPOINTS=1\n"
    )
    for stage, (port, unit, _, _) in PRODUCTS.items():
        env = HERE / f"limits-{stage}.env"
        env.write_text("".join(f"SENTRIX_{k.upper()}={v}\n" for k, v in LIMITS.items()))
        drop = Path.home() / ".config/systemd/user" / (unit + ".d") / "zzzzz-reasoning-bounds.conf"
        with drop.open("x") as handle:
            handle.write(
                f"[Service]\nWorkingDirectory={NEW / stage}\nEnvironmentFile={env}\nExecStart=\n"
                f"ExecStart={PYTHON} -m uvicorn --factory sentrix.main:create_app --host 0.0.0.0 "
                f"--port {port} --app-dir {NEW / stage / 'apps/api/src'}\n"
            )
    for stage, (port, unit, _) in EVALUATORS.items():
        cfg = (
            Path(before["evaluators"][stage]["environment"]["PHOTOBENCH_CONFIG_ROOT"])
            / "runtime_connection.json"
        )
        old = json.loads(cfg.read_text())
        save(f"runtime-{stage}-before.json", old)
        current = {
            **old,
            "product_build_revision": manifest["deployed_version"],
            "product_settings": {**old.get("product_settings", {}), **LIMITS},
        }
        temporary = cfg.with_name("runtime_connection.reasoning-bounds.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, cfg)
        drop = Path.home() / ".config/systemd/user" / (unit + ".d") / "zzzzz-reasoning-bounds.conf"
        with drop.open("x") as handle:
            handle.write(
                f"[Service]\nEnvironmentFile={version}\nExecStart=\nExecStart={PYTHON} "
                "-m uvicorn --factory photobench.main:create_app --host 0.0.0.0 "
                f"--port {port} --app-dir {NEW / 'evaluation/apps/photobench/src'}\n"
            )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for port, unit, _, _ in PRODUCTS.values():
        subprocess.run(["systemctl", "--user", "start", unit], check=True)
        wait(port)
    for port, unit, _ in EVALUATORS.values():
        subprocess.run(["systemctl", "--user", "start", unit], check=True)
        wait(port)
    verify()


def verify():
    manifest = json.loads((HERE / "manifest.json").read_text())
    before = json.loads((HERE / "activation-intent.json").read_text())
    result = {"products": {}, "evaluators": {}, "protected": {u: pid(u) for u in PROTECTED}}
    assert result["protected"] == before["protected"]
    for stage, (port, unit, _, _) in PRODUCTS.items():
        observed = api(port, "diagnostics")
        effective = observed["runtime_configuration"]
        assert {k: effective[k] for k in LIMITS} == LIMITS
        assert {f: sha(NEW / stage / f) for f in manifest["product_files"]} == {
            f: v["after"] for f, v in manifest["product_files"].items()
        }
        result["products"][stage] = {"pid": pid(unit), "limits": LIMITS, "runtime": effective}
    for stage, (port, unit, _) in EVALUATORS.items():
        assert source_state(stage) == before["source_runs"][stage]
        latencies = []
        for path in ["active-work", "runs?page=1&page_size=20&group_tasks=true"] * 3:
            started = time.monotonic()
            api(port, path)
            latencies.append({"path": path, "seconds": time.monotonic() - started})
        result["evaluators"][stage] = {"pid": pid(unit), "latencies": latencies}
    save("activation-response.json", result)
    print(json.dumps(result), flush=True)


def resume(stage, wait_id):
    uuid.UUID(wait_id)
    assert (HERE / "activation-response.json").exists()
    manifest = json.loads((HERE / "manifest.json").read_text())
    original = source_state(stage)
    run_id = original["run_id"]
    port = EVALUATORS[stage][0]
    plan = api(port, f"runs/{run_id}/recovery-plan", timeout=300)
    assert plan["can_resume"] and plan["scope_id"] == original["overview"]["scope_id"]
    assert plan["inherited_qa_count"] + plan["remaining_qa_count"] == 1013
    assert plan["frozen_scoring_profiles"] == ["atm_qs_v1"] and not plan["judge_use_gt_images"]
    payload = {"repair_missing_judges": True, "product_build_revision": manifest["deployed_version"]}
    save(
        f"resume-{stage}-intent.json",
        {
            "wait_id": wait_id,
            "source": original,
            "plan": plan,
            "payload": payload,
            "budget_version": "reasoning-bounded-v1",
            "reason": "user-authorized finite limits",
        },
    )
    result = api(port, f"runs/{run_id}/resume", payload, timeout=900)
    save(f"resume-{stage}-response.json", result)
    assert result["resumed_from"] == run_id and result["resumed_items"] == plan["inherited_qa_count"]
    assert result["scope_id"] == original["overview"]["scope_id"] and not result["retried_qa_ids"]
    assert source_state(stage) == original
    mapping_path = BASE / "active-runs.json"
    mapping = json.loads(mapping_path.read_text())
    mapping[stage] = result
    temporary = mapping_path.with_suffix(".reasoning-bounds.tmp")
    temporary.write_text(json.dumps(mapping, indent=2) + "\n")
    os.replace(temporary, mapping_path)
    print(json.dumps({"wait_id": wait_id, **result}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("action", choices=["inspect", "stage", "activate", "verify", "resume"])
    parser.add_argument("--stage", choices=list(PRODUCTS))
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.action == "inspect":
        print(json.dumps(inspect()))
    elif args.action == "stage":
        stage_release()
    elif args.action == "activate":
        activate()
    elif args.action == "verify":
        verify()
    else:
        resume(args.stage, args.wait_id)
