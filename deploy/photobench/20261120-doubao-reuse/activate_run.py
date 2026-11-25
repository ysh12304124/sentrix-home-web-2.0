"""Switch two idle experimental services once, then launch a fresh reuse evaluation."""

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
from datetime import datetime
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
HERE = Path(__file__).resolve().parent
DATA = BASE / "doubao-reuse-49f347c/product-data"
SOURCE = Path("/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db")
ACCEPTANCE = BASE / "doubao-accept-285bb73/preparation-independent-acceptance.json"
SCOPE = "PhotoBench-20261112-174516-atm-bench-media-current"
ALBUM = "2026.11.17-atm-sources-addresses"
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117") / ALBUM
ENDPOINT = "http://127.0.0.1:18107/v1"
MODEL = "doubao-seed-2.1-lite"
PRODUCT = "sentrix-atm100-doubao-api.service"
EVALUATOR = "sentrix-atm100-doubao-photobench.service"
PRODUCT_APP = BASE / "conversation-delete-9363d4b-v2/product/apps/api/src"
EVALUATOR_APP = BASE / "wait-chat-950b2be/evaluation/apps/photobench/src"
PROTECTED = (
    "sentrix-atm100-api.service", "sentrix-atm100-photobench.service",
    "sentrix-atm100-model.service", "sentrix-atm100-wemm.service",
    "sentrix-atm100-qwen37-gateway.service", "sentrix-atm100-doubao-gateway.service",
    "sentrix2-api.service", "sentrix2-photobench.service",
)
QWEN = "20261119-235632-2026.11.17-atm-sources-addresses-current-resume-0bbacb"
LIMITS = {"reasoning_max_model_calls": 100, "reasoning_max_tool_calls": 600,
          "reasoning_max_search_calls": 100, "reasoning_max_other_tool_calls": 500}


def now():
    return datetime.now().astimezone().isoformat()


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(port, path, payload=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def pid(unit):
    return int(subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ))


def environment(process):
    return dict(p.split("=", 1) for p in Path(f"/proc/{process}/environ").read_bytes().decode().split("\0") if "=" in p)


def database_activity(database):
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        active = [dict(row) for row in db.execute(
            "select id,status from agent_turns where status in ('pending','running')"
        )]
        assert not active, active
        for table in ("import_jobs", "video_jobs"):
            counts = dict(db.execute(f"select status,count(*) from {table} group by status"))
            assert not any(counts.get(s) for s in ("accepted", "queued", "running", "processing", "indexing", "extracting")), counts
        assert db.execute("select count(*) from scope_postprocess where processing_generation is not null").fetchone()[0] == 0
        old = [dict(row) for row in db.execute(
            "select operation_id,scope_id,status,started_at from pipeline_receipts where status='running'"
        )]
        assert {row["operation_id"] for row in old} <= {"8dc3d339db5e429daf3bb2865e614f39"}, old
        if old:
            recovered = db.execute(
                "select status,started_at from pipeline_receipts where operation_id='141834a96b9c4c0989ececbf326c4981'"
            ).fetchone()
            assert recovered["status"] == "completed" and recovered["started_at"] > old[0]["started_at"]
        return {"active_turns": active, "historical_superseded_pipeline_receipts": old}


def idle(database):
    assert not api(8874, "active-work")["work"]
    imports = api(8294, "imports/progress")
    assert imports["library"]["processing"] == 0 and imports["postprocess"]["state"] == "idle", imports
    return {"imports": imports, **database_activity(database)}


def wait_http(port, path):
    deadline = time.monotonic() + 120
    while True:
        try:
            return api(port, path)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def activate():
    assert not (HERE / "activate-intent.json").exists(), "Consumed activation: inspect receipts"
    acceptance = json.loads(ACCEPTANCE.read_text())
    assert acceptance["status"] == "accepted" and acceptance["emails"] == 6742
    before_activity = idle(SOURCE)
    assert Path(environment(pid(PRODUCT))["SENTRIX_DATA_DIR"]) / "sentrix.db" == SOURCE
    for unit, app in ((PRODUCT, PRODUCT_APP), (EVALUATOR, EVALUATOR_APP)):
        args = Path(f"/proc/{pid(unit)}/cmdline").read_bytes().decode().split("\0")
        assert args[args.index("--app-dir") + 1] == str(app)
    database_activity(DATA / "sentrix.db")
    with urllib.request.urlopen(ENDPOINT + "/models", timeout=30) as response:
        assert {m["id"] for m in json.load(response)["data"]} == {MODEL}
    protected = {unit: pid(unit) for unit in PROTECTED}
    assert all(protected.values())
    result_manifest = BASE / "cloud-results" / QWEN / "split-commit.json"
    result_sha = sha(result_manifest)
    config = api(8874, "config")
    config_path = Path(config["runtime_config_file"])
    before = json.loads(config_path.read_text())
    product_settings = {**before["product_settings"], **LIMITS, "vlm_context_tokens": 16384}
    updated = {**before, "model_base_url": ENDPOINT, "endpoint_model": MODEL,
               "sentrix_url": "http://127.0.0.1:8294", "vllm_manager_url": "",
               "product_settings": product_settings}
    startup = HERE / "startup-config"
    startup.mkdir()
    public = {**config["runtime_config"], "model_base_url": ENDPOINT, "endpoint_model": MODEL,
              "product_settings": product_settings}
    (startup / "runtime_connection.json").write_text(json.dumps(public, ensure_ascii=False, indent=2))
    old_pids = {PRODUCT: pid(PRODUCT), EVALUATOR: pid(EVALUATOR)}
    save("activate-intent.json", {
        "created_at": now(), "old_pids": old_pids, "protected_pids": protected,
        "acceptance_sha256": sha(ACCEPTANCE), "qwen_manifest_sha256": result_sha,
        "config_path": str(config_path), "config_before_sha256": sha(config_path),
        "old_endpoint": config["runtime_config"]["model_base_url"],
        "target_endpoint": ENDPOINT, "target_database": str(DATA / "sentrix.db"),
        "before_activity": before_activity,
    })
    product_values = {
        "SENTRIX_DATA_DIR": str(DATA), "SENTRIX_VLM_BASE_URL": ENDPOINT,
        "SENTRIX_VLM_CONTEXT_TOKENS": "16384", "SENTRIX_VLM_REASONING_CONTEXT_TOKENS": "16384",
        "PHOTOBENCH_CONFIG_ROOT": str(startup),
        **{"SENTRIX_" + k.upper(): str(v) for k, v in LIMITS.items()},
    }
    evaluator_values = {"BENCH_SENTRIX_URL": "http://127.0.0.1:8294",
                        "BENCH_VLLM_BASE_URL": ENDPOINT, "BENCH_VLLM_API_URL": ""}
    for unit, values, name in ((PRODUCT, product_values, "product.env"),
                                (EVALUATOR, evaluator_values, "evaluation.env")):
        env_file = HERE / name
        with env_file.open("x") as stream:
            stream.write("".join(f'{k}="{v}"\n' for k, v in values.items()))
        drop = Path.home() / ".config/systemd/user" / (unit + ".d/zzzzzzzzzzzz-doubao-reuse.conf")
        with drop.open("x") as stream:
            stream.write("[Service]\n" + "".join(f'Environment="{k}={v}"\n' for k, v in values.items()) + f"EnvironmentFile={env_file}\n")
    idle(SOURCE)
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert sha(result_manifest) == result_sha
    temporary = HERE / "runtime_connection.new.json"
    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(config_path.stat().st_mode & 0o777)
    os.replace(temporary, config_path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for unit, port, route in ((PRODUCT, 8294, "health/live"), (EVALUATOR, 8874, "config")):
        subprocess.run(["systemctl", "--user", "restart", unit], check=True)
        wait_http(port, route)
    accept_activation()


def accept_activation():
    """Finish read-only verification after activation; never restart or change configuration."""
    assert not (HERE / "activate-response.json").exists()
    intent = json.loads((HERE / "activate-intent.json").read_text())
    config_path = Path(intent["config_path"])
    updated = json.loads(config_path.read_text())
    product_settings = updated["product_settings"]
    protected = intent["protected_pids"]
    result_manifest = BASE / "cloud-results" / QWEN / "split-commit.json"
    result_sha = intent["qwen_manifest_sha256"]
    actual_env = environment(pid(PRODUCT))
    assert actual_env["SENTRIX_DATA_DIR"] == str(DATA)
    assert actual_env["SENTRIX_VLM_BASE_URL"] == ENDPOINT
    effective = api(8294, "diagnostics")
    settings = effective["runtime_configuration"]
    assert settings["vlm_base_url"] == ENDPOINT
    assert settings["vlm_context_tokens"] == settings["vlm_reasoning_context_tokens"] == 16384
    assert all(settings[k] == v for k, v in product_settings.items())
    assert effective["build_revision"] == updated["product_build_revision"]
    current = api(8874, "config")["runtime_config"]
    assert current["model_base_url"] == ENDPOINT and current["endpoint_model"] == MODEL
    assert current["product_settings"] == product_settings
    assert environment(pid(EVALUATOR))["PHOTOBENCH_PERSIST_TIMEOUT_SECONDS"] == "300"
    online_emails = api(8294, f"assets?scope_id={SCOPE}&media_type=text&limit=1")
    assert online_emails["total"] == 6742 and len(online_emails["items"]) == 1
    with sqlite3.connect("file:" + str(DATA / "sentrix.db") + "?mode=ro", uri=True) as db:
        assert db.execute("select media_type from assets where id=?", (online_emails["items"][0]["id"],)).fetchone()[0] == "text"
    with sqlite3.connect("file:" + str(SOURCE) + "?mode=ro", uri=True) as db:
        assert db.execute("select count(*) from assets where scope_id=? and media_type='text'", (SCOPE,)).fetchone()[0] == 0
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert sha(result_manifest) == result_sha
    after_activity = idle(DATA / "sentrix.db")
    save("activate-response.json", {
        "status": "accepted", "finished_at": now(), "pids": {PRODUCT: pid(PRODUCT), EVALUATOR: pid(EVALUATOR)},
        "protected_pids_unchanged": True, "qwen_manifest_unchanged": True,
        "database": str(DATA / "sentrix.db"), "online_email_count_verified": 6742,
        "database_verification": "actual process environment and online unique copied-email read",
        "runtime_config_sha256": sha(config_path), "runtime_config": current,
        "effective_product": effective, "activity": after_activity,
        "ports_restarted": [8294, 8874], "persist_timeout_seconds": 300,
    })
    print(json.dumps({"activated": True, "pids": {PRODUCT: pid(PRODUCT), EVALUATOR: pid(EVALUATOR)}}), flush=True)


def launch(wait_id):
    assert not (HERE / "launch-intent.json").exists(), "Consumed launch: inspect exact response and run"
    activation = json.loads((HERE / "activate-response.json").read_text())
    assert activation["status"] == "accepted"
    idle(DATA / "sentrix.db")
    assert all(pid(unit) == process for unit, process in activation["pids"].items())
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert "doubao" not in mapping
    assert sha(PACKAGE / "qa/atm-bench.jsonl") == "13d07b33d8e9c522347bf485539a9a71d8ce569385d1ecf26620218b7e3383c3"
    assert len((PACKAGE / "qa/atm-bench.jsonl").read_text().splitlines()) == 1013
    config = api(8874, "config")
    assert config["default_judge_provider_id"] == "ark-doubao-seed-2.1-lite"
    assert config["runtime_config"]["endpoint_model"] == MODEL
    assert min(shutil.disk_usage(p).free for p in ("/home", "/var/tmp")) > 3 * 1024**3
    frozen = json.loads((BASE / "scoring-frozen.json").read_text())
    assert frozen["scoring_profiles"] == ["atm_qs_v1"]
    assert frozen["judge_model"] == MODEL
    payload = {**frozen, "album_id": ALBUM, "qa_set": "atm-bench", "models": ["__current__"],
               "mode": "reuse", "existing_scope_id": SCOPE, "model_base_url": ENDPOINT,
               "endpoint_model": MODEL, "vllm_manager_url": "", "sentrix_url": "http://127.0.0.1:8294",
               "runtime_framework": "generic", "reasoning": True, "graph_enabled": True,
               "qa_concurrency": 4, "judge_backpressure": True, "turn_timeout": 3660,
               "pipeline_timeout": 172800, "import_timeout": 172800}
    save("launch-intent.json", {"created_at": now(), "wait_id": wait_id, "payload": payload,
                                "activation_sha256": sha(HERE / "activate-response.json"),
                                "scoring_sha256": sha(BASE / "scoring-frozen.json"),
                                "inherited_answers": 0, "label": "历史豆包媒体记忆复用+新版邮件增量"})
    response = api(8874, "runs", payload)
    save("launch-response.json", {"wait_id": wait_id, "received_at": now(), **response})
    assert response["mode"] == "reuse" and len(response["run_ids"]) == 1
    entry = {"run_id": response["run_ids"][0], "mode": "reuse", "scope_id": SCOPE,
             "inherited_answers": 0, "label": "历史豆包媒体记忆复用+新版邮件增量",
             "wait_id": wait_id, "launch_directory": str(HERE)}
    latest = json.loads((BASE / "active-runs.json").read_text())
    assert latest == mapping
    temporary = HERE / "active-runs.new.json"
    temporary.write_text(json.dumps({**mapping, "doubao": entry}, ensure_ascii=False, indent=2))
    os.replace(temporary, BASE / "active-runs.json")
    save("active-mapping.json", entry)
    print(json.dumps(entry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["activate", "accept-activation", "launch"])
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.operation == "activate":
        activate()
    elif args.operation == "accept-activation":
        accept_activation()
    else:
        assert args.wait_id
        launch(args.wait_id)
