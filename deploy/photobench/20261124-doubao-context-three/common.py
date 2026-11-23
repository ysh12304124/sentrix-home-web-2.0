"""Frozen source and runtime checks for the three authorized context retries."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
HERE = Path(__file__).resolve().parent
TERMINAL = BASE / "doubao-supplement49-terminal-20261124-v1"
sys.path.insert(0, str(TERMINAL))
import inspect_terminal as terminal  # noqa: E402
from inspect_reconnect import command as command  # noqa: E402
from inspect_reconnect import environment, pid, sha  # noqa: E402

from photobench.domain.evaluation.profiles import valid_result  # noqa: E402
from photobench.infrastructure.results.split_store import SplitRunReader  # noqa: E402

APP, ROOT, BATCH, DB = terminal.APP, terminal.ROOT, terminal.BATCH, terminal.DB
SOURCE = terminal.RUN
PRODUCT = "sentrix-atm100-doubao-api.service"
EVALUATOR = "sentrix-atm100-doubao-photobench.service"
OLD = BASE / "conversation-delete-9363d4b-v2/product"
RELEASE = HERE / "product"
SEED = "20261124-doubao-context3-seed-fc11be"
RELATIVE = "apps/api/src/sentrix/application/assistant/run_turn.py"
BEFORE = "647db108f9348df9feced5d6d98e97ae338d9ec51d437018856cb206e3a0ec24"
AFTER = "9d8dff727f28837ca598cdd3f8eed300401791b4aa008862cd21f1435d84d4dc"
SUITES_SHA = "faf02cac36cca3e0eacf74d31aa851e60fc7ef92435964a1c7e98cb7c7c6e9ba"
FIX_COMMIT = "d714c62ac9fcf7d697562df6c52e7474a354ff38"
BUILD = "ffec2be63ed31c0e3f77268c030a77b8029cf43f+100-reasoning-bounded-v1+context-preview-d714c62a"
TARGETS = {
    "atm-1549c95e-6cfe-484a-96e2-535835bc38b1",
    "atm-230bf920-cc9f-46c9-a77a-f0ce976e5bb7",
    "atm-5703b8c1-2e4f-4a5e-9385-44643f5a33c5",
}
api, fingerprint = terminal.api, terminal.fingerprint
prior = sys.modules["supplement"]


def read(name):
    return json.loads((HERE / name).read_text())


def save(name, data):
    with (HERE / name).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def manifest_sha(run):
    return sha(ROOT / run / "split-commit.json")


def source():
    accepted = json.loads((TERMINAL / "terminal-acceptance.json").read_text())
    exported = json.loads((TERMINAL / "exports-acceptance.json").read_text())
    assert accepted["accepted"] and all(accepted["checks"].values())
    assert exported["read_revision"] == accepted["read_revision"] == "e98645d1e4a5465cba59747e1514ecf7"
    assert exported["valid_scores"] == accepted["valid_atm_scores"] == 994
    assert exported["contract_checked_items"] == 1013 and exported["retained_failed_item_count"] == 19
    assert exported["answers_attempts_and_all_score_receipts_unchanged"]
    assert exported["source_manifests_and_product_runtime_unchanged"]
    assert all(v["verified"] and v["sha256"] == v["expected_sha256"] for v in exported["exports"].values())
    assert manifest_sha(SOURCE) == accepted["manifest_sha256"]
    reader = SplitRunReader(ROOT, ROOT / SOURCE)
    assert reader.revision == accepted["read_revision"]
    state = reader.state()
    assert len(state["items"]) == state["qa_count"] == 1013
    assert fingerprint(state["items"]) == accepted["all_items_reference_sha256"]
    assert fingerprint(state["scoring_results"]) == accepted["all_scoring_receipts_sha256"]
    valid = {
        k: v for k, v in state["scoring_results"].items() if v.get("batch_id") == BATCH and valid_result(v)
    }
    assert len(valid) == 994 and not TARGETS & {v["qa_id"] for v in valid.values()}
    plan = read("authorized-plan.json")
    assert set(plan["authorized_context_retry_qa_ids"]) == TARGETS
    assert len(plan["unmodified_timeout_qa_ids_counted_as_incorrect"]) == 16
    assert set(accepted["execution_failures"]) == TARGETS | set(
        plan["unmodified_timeout_qa_ids_counted_as_incorrect"]
    )
    assert state["reasoning"] and state["graph_enabled"]
    budget = state["runtime_snapshot"]["execution_budgets"]
    assert budget["qa_concurrency"] == 4 and budget["turn_timeout"] == 3660
    return state, valid, accepted


def database_activity():
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        turns = db.execute(
            "select count(*) from agent_turns where status in ('pending','running')"
        ).fetchone()[0]
        assert not turns, turns
        for table in ("import_jobs", "video_jobs"):
            counts = dict(db.execute(f"select status,count(*) from {table} group by status"))
            assert not any(
                counts.get(s)
                for s in ("accepted", "queued", "running", "processing", "indexing", "extracting")
            ), counts
        assert (
            db.execute(
                "select count(*) from scope_postprocess where processing_generation is not null"
            ).fetchone()[0]
            == 0
        )
        running = [
            dict(r)
            for r in db.execute(
                "select operation_id,scope_id,status,started_at from pipeline_receipts where status='running'"
            )
        ]
        assert {r["operation_id"] for r in running} <= {"8dc3d339db5e429daf3bb2865e614f39"}
        for row in running:
            newer = db.execute(
                "select scope_id,status,started_at from pipeline_receipts where operation_id=?",
                ("141834a96b9c4c0989ececbf326c4981",),
            ).fetchone()
            assert (
                newer["scope_id"] == row["scope_id"]
                and newer["status"] == "completed"
                and newer["started_at"] > row["started_at"]
            )
    return {
        "all_database_turns_idle": True,
        "imports_idle": True,
        "postprocess_idle": True,
        "historical_running_pipeline_superseded": running,
    }


def idle():
    assert not api(8874, "active-work")["work"]
    return database_activity()


def frozen_manifests():
    return {p.parent.name: sha(p) for p in ROOT.glob("*/split-commit.json")}


def preserved(manifests):
    return all(manifest_sha(run) == expected for run, expected in manifests.items())


def runtime():
    diagnostics = api(8294, "diagnostics")
    config = api(8874, "config")
    settings = diagnostics["runtime_configuration"]
    assert settings["vlm_base_url"] == "http://127.0.0.1:18107/v1"
    assert settings["vlm_context_tokens"] == settings["vlm_reasoning_context_tokens"] == 16384
    assert {
        k: settings["reasoning_max_" + k + "_calls"] for k in ("model", "tool", "search", "other_tool")
    } == {"model": 100, "tool": 600, "search": 100, "other_tool": 500}
    env = environment(pid(PRODUCT))
    assert Path(env["SENTRIX_DATA_DIR"]) / "sentrix.db" == DB
    assert config["runtime_config"]["endpoint_model"] == "doubao-seed-2.1-lite"
    assert config["runtime_config"]["model_base_url"] == settings["vlm_base_url"]
    assert sha(APP / "photobench/application/runs/suites.py") == SUITES_SHA
    return diagnostics, config
