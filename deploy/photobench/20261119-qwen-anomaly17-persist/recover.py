"""Single-use Qwen recovery: activate verified reader, recover three turns, resume three QA."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from photobench.application.run_execution.service import RunExecution, RunRequest
from photobench.domain.runs.execution import index_assets_by_name
from photobench.domain.runs.frozen_trace import freeze_item_traces
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
ROOT = BASE / "cloud-results"
HERE = Path(__file__).resolve().parent
FAILED = "20261119-221456-2026.11.17-atm-sources-addresses-current-resume-170bf7"
ORIGINAL = "20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686"
SEED = "20261119-qwen-anomaly17-terminal-turn-seed-170bf7"
OLD = BASE / "qwen-live-list-8b6da36/evaluation"
NEW = BASE / "trace-viewer-ui-4df9302/evaluation"
UNIT = "sentrix-atm100-doubao-photobench.service"
PROTECTED = [
    "sentrix-atm100-photobench.service",
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix-atm100-model.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]
TURNS = {
    "atm-fcfcb760-665f-4d0d-9bdb-fabc49e1ae7c": "turn_20261119150105406938",
    "atm-ef651ed4-aeb5-4528-9003-ac8be603e263": "turn_20261119151115122891",
    "atm-41e4a473-65d8-4c5b-a73a-27e578157fac": "turn_20261119151242099701",
}
REMAINING = {
    "atm-4b3a611a-c372-4c64-b59b-1a994241784e",
    "atm-79ab440a-5da6-4cb8-900e-368234df9c36",
    "atm-8b4332f3-bc74-48fb-99c6-0cbd088493a6",
}


def api(path, data=None, port=8874):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300 if data is not None else 90) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(unit):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()


def command(process):
    return Path("/proc/" + process + "/cmdline").read_bytes().decode().split("\0")


def context():
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["qwen37"]["run_id"] == FAILED
    reader = SplitRunReader(ROOT, ROOT / FAILED)
    state = reader.state()
    assert state["status"] == "failed" and len(state["items"]) == 1007
    assert state["qa_count"] == 1013 and state["reasoning"] and state["graph_enabled"]
    assert state["runtime_snapshot"]["endpoint_model"] == "qwen3.7-plus"
    settings = state["runtime_snapshot"]["product_settings"]
    assert [
        settings[k]
        for k in (
            "reasoning_max_model_calls",
            "reasoning_max_tool_calls",
            "reasoning_max_search_calls",
            "reasoning_max_other_tool_calls",
        )
    ] == [100, 600, 100, 500]
    budgets = state["runtime_snapshot"]["execution_budgets"]
    assert budgets["qa_concurrency"] == 4 and budgets["turn_timeout"] == 3660
    assert not api("active-work")["work"]
    imports = api("imports/progress?scope_id=" + state["scope_id"], port=8294)
    assert imports["postprocess"]["state"] == "idle" and not imports["library"]["processing"]
    with sqlite3.connect(
        "file:/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db?mode=ro", uri=True
    ) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE status IN ('pending','running')").fetchall()
    return mapping, reader, state


def activate():
    assert not (HERE / "activate-intent.json").exists()
    _, _, state = context()
    profile = json.loads((HERE / "profile-response.json").read_text())
    assert profile["items"] == 1007 and profile["source_manifest_unchanged"] and profile["scores_unchanged"]
    source = json.loads((NEW.parent / "manifest.json").read_text())
    hashes = {p: d for p, d in source["payload_files"].items() if p.startswith("apps/") and "/src/" in p}
    assert len(hashes) == 4
    assert all(sha(NEW / p) == digest for p, digest in hashes.items())
    previous = pid(UNIT)
    argv = command(previous)
    assert argv[argv.index("--app-dir") + 1] == str(OLD / "apps/photobench/src")
    override = Path.home() / ".config/systemd/user" / (UNIT + ".d") / "zzzzzzzz-full-lazy.conf"
    old = override.read_text()
    assert old.count(str(OLD / "apps/photobench/src")) == 1
    new = old.replace(str(OLD / "apps/photobench/src"), str(NEW / "apps/photobench/src"))
    protected = {u: pid(u) for u in PROTECTED}
    save(
        "activate-intent.json",
        {
            "failed_run_id": FAILED,
            "scope_id": state["scope_id"],
            "old_pid": previous,
            "old_cmdline": argv,
            "override": str(override),
            "before": old,
            "after": new,
            "protected_pids": protected,
            "source_sha256": hashes,
        },
    )
    pending = override.with_suffix(".pending")
    pending.write_text(new)
    pending.replace(override)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            assert not api("active-work")["work"]
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    current = pid(UNIT)
    argv = command(current)
    assert current not in {previous, "0"}
    assert argv[argv.index("--app-dir") + 1] == str(NEW / "apps/photobench/src")
    assert {u: pid(u) for u in PROTECTED} == protected
    started = time.monotonic()
    overview = api(f"runs/{FAILED}?include_history=false")
    assert overview["status"] == "failed" and overview["qa_count"] == 1013
    save(
        "activate-response.json",
        {
            "pid": current,
            "actual_cmdline": argv,
            "actual_app_dir": str(NEW / "apps/photobench/src"),
            "source_sha256": hashes,
            "protected_pids_unchanged": True,
            "overview_seconds": time.monotonic() - started,
        },
    )
    print(json.dumps({"activated": 8874, "pid": current, "protected_pids_unchanged": True}), flush=True)


class ExistingTurn:
    def __init__(self, qa, fact, question, scope):
        self.qa, self.fact, self.question, self.scope = qa, fact, question, scope

    def start_conversation(self, *, title, scope_id):
        assert title == self.qa and scope_id == self.scope
        return {"id": self.fact["conversation_id"]}

    def start_turn(self, *, conversation_id, message, reasoning, include_debug, **kwargs):
        assert conversation_id == self.fact["conversation_id"] and message == self.question
        assert reasoning and include_debug and kwargs.get("graph_enabled", True)
        return {"turn_id": self.fact["turn_id"]}

    def get_turn(self, *, turn_id, **kwargs):
        assert turn_id == self.fact["turn_id"]
        body = api("assistant/turns/" + turn_id, port=8294)
        assert body["status"] == self.fact["status"] and body.get("result", {}).get("answer")
        return body


def recover():
    assert (HERE / "activate-response.json").exists()
    assert not (HERE / "recover-intent.json").exists() and not (ROOT / SEED).exists()
    _, reader, state = context()
    scope = state["scope_id"]
    rows = {r["qa_id"]: r for r in state["qa_snapshot"]["rows"]}
    existing = {i["qa_id"] for i in state["items"]}
    assert set(rows) - existing == set(TURNS) | REMAINING
    original_sha = sha(ROOT / ORIGINAL / "split-commit.json")
    failed_sha = sha(ROOT / FAILED / "split-commit.json")
    scores = reader.reader.fingerprint(state["scoring_results"])
    facts = {}
    with sqlite3.connect(
        "file:/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db?mode=ro", uri=True
    ) as db:
        for qa, turn in TURNS.items():
            row = db.execute(
                "SELECT t.conversation_id,c.title,t.scope_id,t.question,t.status,"
                "t.created_at,t.updated_at,t.answer,t.termination_reason "
                "FROM agent_turns t JOIN agent_conversations c ON c.id=t.conversation_id WHERE t.id=?",
                (turn,),
            ).fetchone()
            assert row and row[1] == qa and row[2] == scope and row[3] == rows[qa]["question"]
            assert row[4] in {"complete", "partial"} and row[7]
            facts[qa] = {
                "turn_id": turn,
                "conversation_id": row[0],
                "status": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "termination_reason": row[8],
                "answer_sha256": hashlib.sha256(row[7].encode()).hexdigest(),
            }
    save(
        "recover-intent.json",
        {
            "failed_run_id": FAILED,
            "seed_run_id": SEED,
            "failed_manifest_sha256": failed_sha,
            "original_manifest_sha256": original_sha,
            "scores_sha256": scores,
            "terminal_turns": facts,
            "product_http_methods": ["GET"],
        },
    )
    assets = []
    while True:
        page = api(
            f"assets?scope_id={scope}&limit=500&offset={len(assets)}&sort=created&order=asc", port=8294
        )
        assets.extend(page["items"])
        if len(assets) >= page["total"]:
            break
        assert page["items"]
    assert len(assets) == state["qa_assets"]
    indexed = index_assets_by_name(assets)
    restored = []
    for qa, fact in facts.items():
        client = ExistingTurn(qa, fact, rows[qa]["question"], scope)
        execution = RunExecution(
            client=client, albums=None, writer=None, lifecycle=None, inference=None, telemetry=None
        )
        request = RunRequest(
            album_id=state["album_id"],
            qa_set=state["qa_set"],
            model_profile=state["model_profile"],
            sentrix_url="http://127.0.0.1:8294",
            reasoning=True,
            graph_enabled=True,
            turn_timeout=3660,
        )
        item = execution._evaluate_one(rows[qa], scope, indexed, request)
        assert hashlib.sha256(item["answer"].encode()).hexdigest() == fact["answer_sha256"]
        assert item["termination_reason"] == fact["termination_reason"]
        item["wall_clock_ms"] = round(
            (
                datetime.fromisoformat(fact["updated_at"]) - datetime.fromisoformat(fact["created_at"])
            ).total_seconds()
            * 1000,
            1,
        )
        item["judge_status"] = "pending" if item["execution_status"] == "completed" else "skipped"
        item["product_turn_recovery"] = {
            **fact,
            "source_run_id": FAILED,
            "agent_rerun": False,
            "latency_source": "product_turn_created_to_updated",
        }
        state["items"].append(item)
        restored.append({"qa_id": qa, **fact, "execution_status": item["execution_status"]})
    order = {r["qa_id"]: n for n, r in enumerate(state["qa_snapshot"]["rows"])}
    state["items"].sort(key=lambda i: order[i["qa_id"]])
    state.update(
        run_id=SEED,
        status="failed",
        current_phase=None,
        resumed_from=FAILED,
        fatal_error=(
            "Three terminal product turns restored after persistence failure; "
            "original failure and attempts retained."
        ),
    )
    state["terminal_turn_recovery"] = {"source_run_id": FAILED, "turns": restored}
    freeze_item_traces(state["items"])
    result = SplitResultWriter(ROOT).write(ROOT / SEED, state)
    verified = SplitRunReader(ROOT, ROOT / SEED)
    assert len(verified.state()["items"]) == 1010
    assert verified.reader.fingerprint(verified.state()["scoring_results"]) == scores
    assert sha(ROOT / ORIGINAL / "split-commit.json") == original_sha
    assert sha(ROOT / FAILED / "split-commit.json") == failed_sha
    save(
        "recover-response.json",
        {
            "seed_run_id": SEED,
            "items": 1010,
            "remaining": sorted(REMAINING),
            "restored": restored,
            "original_and_failed_manifests_unchanged": True,
            "prior_scores_sha256": scores,
            "write": result,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    )
    print(json.dumps({"seed_run_id": SEED, "items": 1010, "remaining": 3, "write": result}), flush=True)


def resume(wait_id):
    uuid.UUID(wait_id)
    assert (HERE / "recover-response.json").exists() and not (HERE / "resume-intent.json").exists()
    mapping, _, state = context()
    plan = api(f"runs/{SEED}/recovery-plan")
    assert plan["can_resume"] and plan["inherited_qa_count"] == 1010 and plan["remaining_qa_count"] == 3
    assert not plan["product_version_changed"]
    assert plan["frozen_scoring_profiles"] == ["atm_qs_v1"] and not plan["judge_use_gt_images"]
    payload = {"repair_missing_judges": True}
    save(
        "resume-intent.json",
        {
            "wait_id": wait_id,
            "failed_run_id": FAILED,
            "seed_run_id": SEED,
            "recovery_plan": plan,
            "payload": payload,
            "allowed_remaining_qa_ids": sorted(REMAINING),
            "note": "Preserve all saved execution failures; no explicit retry_qa_ids.",
        },
    )
    result = api(f"runs/{SEED}/resume", payload)
    save("resume-response.json", result)
    assert result["resumed_from"] == SEED and result["resumed_items"] == 1010
    assert result["scope_id"] == state["scope_id"] and not result["retried_qa_ids"]
    assert json.loads((BASE / "active-runs.json").read_text()) == mapping
    mapping["qwen37"] = result
    pending = HERE / "active-runs.next.json"
    pending.write_text(json.dumps(mapping, indent=2))
    pending.replace(BASE / "active-runs.json")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["activate", "recover", "resume"])
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    {"activate": activate, "recover": recover, "resume": lambda: resume(args.wait_id)}[args.operation]()
