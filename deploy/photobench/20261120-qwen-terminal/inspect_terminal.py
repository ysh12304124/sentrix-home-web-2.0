"""Read-only acceptance of Qwen's authorized 17-question attempt and inherited results."""

from __future__ import annotations

import hashlib
import json
import resource
import shutil
import sqlite3
import subprocess
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from photobench.domain.evaluation.profiles import scoring_targets, valid_result
from photobench.infrastructure.results.split_json import STORED_TYPES
from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
ROOT = BASE / "cloud-results"
SOURCE = "20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686"
FAILED = "20261119-221456-2026.11.17-atm-sources-addresses-current-resume-170bf7"
SEED = "20261119-qwen-anomaly17-terminal-turn-seed-170bf7"
RUN = "20261119-235632-2026.11.17-atm-sources-addresses-current-resume-0bbacb"
BATCH = "score-dd0413c8aa869b1c84626de7"
KEEP_FAILED = "atm-995f8ba1-43ad-423b-82fc-c89d9cb073c9"
PRIOR = BASE / "qwen-anomaly17-persist-04471e4"


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=60) as response:
        return json.load(response)


def identity(value):
    if isinstance(value, STORED_TYPES):
        return {"immutable_object_ref": value.ref}
    if isinstance(value, dict):
        return {k: identity(v) for k, v in value.items()}
    if isinstance(value, list):
        return [identity(v) for v in value]
    return value


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(item, state):
    scores = [v for v in state["scoring_results"].values() if v["qa_id"] == item["qa_id"]]
    return {
        **{
            k: item.get(k)
            for k in (
                "qa_id",
                "execution_status",
                "agent_status",
                "termination_reason",
                "judge_status",
                "error",
            )
        },
        "answer_sha256": hashlib.sha256((item.get("answer") or "").encode()).hexdigest(),
        "answer_length": len(item.get("answer") or ""),
        "turn_ids": [t.get("turn_id") for t in item.get("runtime_turns") or []],
        "scores": [
            {k: v.get(k) for k in ("status", "score", "reason", "batch_id", "answer_fingerprint")}
            for v in scores
        ],
    }


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["qwen37"]["run_id"] == RUN
    readers = {run: SplitRunReader(ROOT, ROOT / run) for run in (SOURCE, FAILED, SEED, RUN)}
    states = {run: reader.state() for run, reader in readers.items()}
    current = states[RUN]
    items = {run: {i["qa_id"]: i for i in state["items"]} for run, state in states.items()}
    actual, original, seed = items[RUN], items[SOURCE], items[SEED]
    assert current["status"] == "completed_with_errors" and not current.get("fatal_error")
    assert len(actual) == len(current["items"]) == current["qa_count"] == 1013
    qa_rows = {q["qa_id"]: q for q in current["qa_snapshot"]["rows"]}
    assert actual.keys() == original.keys() == qa_rows.keys()
    targets = json.loads((BASE / "qwen-live-list-8b6da36/anomaly17-plan.json").read_text())["qa_ids"]
    assert len(targets) == len(set(targets)) == 17
    inherited = original.keys() - set(targets)
    assert len(inherited) == 996 and len(seed) == 1010
    fields = (
        "question",
        "reference_answer",
        "answer",
        "execution_status",
        "termination_reason",
        "attachments",
        "predicted_media",
        "scope_id",
        "model_call_metrics",
        "tool_trace",
    )
    for before in (original, seed):
        selected = inherited if before is original else seed.keys()
        for qa in selected:
            for field in fields:
                assert identity(before[qa].get(field)) == identity(actual[qa].get(field)), (qa, field)
            old_turns, new_turns = (
                before[qa].get("runtime_turns") or [],
                actual[qa].get("runtime_turns") or [],
            )
            assert len(old_turns) == len(new_turns), qa
            for old, new in zip(old_turns, new_turns, strict=True):
                for field in (
                    "answer",
                    "turn_id",
                    "conversation_id",
                    "status",
                    "termination_reason",
                    "attachments",
                    "predicted_media",
                    "trace",
                    "debug_trace",
                    "trace_bundle",
                    "agent2_trace",
                ):
                    assert identity(old.get(field)) == identity(new.get(field)), (qa, field)
    for run, allowed in ((SOURCE, inherited), (SEED, seed.keys())):
        previous = {k: v for k, v in states[run]["scoring_results"].items() if v["qa_id"] in allowed}
        copied = {k: current["scoring_results"][k] for k in previous}
        assert readers[run].reader.fingerprint(previous) == readers[RUN].reader.fingerprint(copied)
    intent = json.loads((PRIOR / "recover-intent.json").read_text())
    assert sha(ROOT / SOURCE / "split-commit.json") == intent["original_manifest_sha256"]
    assert sha(ROOT / FAILED / "split-commit.json") == intent["failed_manifest_sha256"]
    assert actual[KEEP_FAILED]["execution_status"] == "failed"
    assert actual[KEEP_FAILED]["termination_reason"] == "model_error"
    batch = next(b for b in current["scoring_batches"] if b["batch_id"] == BATCH)
    frozen = json.loads((BASE / "scoring-frozen.json").read_text())
    assert batch["config"] == frozen["scoring_configs"]["atm_qs_v1"]
    assert batch["rule_version"] == "scoring-2026.11.12-v5"
    assert batch["judge"]["model"] == "doubao-seed-2.1-lite"
    assert batch["qa_definition_sha256"] == states[SOURCE]["scoring_batches"][0]["qa_definition_sha256"]
    assert current["scope_id"] == states[SOURCE]["scope_id"]
    assert current["reasoning"] and current["graph_enabled"]
    budgets = current["runtime_snapshot"]["execution_budgets"]
    assert budgets["qa_concurrency"] == 4 and budgets["turn_timeout"] == 3660
    settings = current["runtime_snapshot"]["product_settings"]
    assert [settings["reasoning_max_" + k + "_calls"] for k in ("model", "tool", "search", "other_tool")] == [
        100,
        600,
        100,
        500,
    ]
    work = api(8874, "active-work")["work"]
    assert not work
    database = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
    with sqlite3.connect("file:" + database + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        assert not db.execute("SELECT id FROM agent_turns WHERE status IN ('pending','running')").fetchall()
        turns = [
            dict(r)
            for r in db.execute(
                "SELECT t.id,c.title AS qa_id,t.status,t.termination_reason FROM agent_turns t "
                "JOIN agent_conversations c ON c.id=t.conversation_id "
                "WHERE t.scope_id=? AND julianday(t.created_at)>=julianday(?)",
                (current["scope_id"], states[FAILED]["started_at"]),
            )
        ]
        assert len(turns) == 17 and {t["qa_id"] for t in turns} == set(targets)
        for turn in turns:
            events = db.execute(
                "SELECT payload_json FROM agent_turn_events_v2 WHERE turn_id=? ORDER BY event_seq DESC",
                (turn["id"],),
            )
            budget = next(
                e["payload"] for (raw,) in events if (e := json.loads(raw)).get("kind") == "request_budget"
            )
            assert [
                budget[k]
                for k in ("max_model_calls", "max_tool_calls", "max_search_calls", "max_other_tool_calls")
            ] == [100, 600, 100, 500]
            assert budget["model_attempts"] <= 100 and budget["tool_admitted"] <= 600
            assert budget["search_admitted"] <= 100 and budget["other_admitted"] <= 500
            turn["budget"] = budget
    imports = api(8294, "imports/progress?scope_id=" + current["scope_id"])
    assert not imports["library"]["processing"] and imports["postprocess"]["state"] == "idle"
    with urllib.request.urlopen("http://127.0.0.1:18108/v1/models", timeout=15) as response:
        model = json.load(response)["data"][0]["id"]
    assert model == current["runtime_snapshot"]["endpoint_model"] == "qwen3.7-plus"
    missing = scoring_targets(current, batch, "missing")
    overview = readers[RUN].overview()
    q4_root = Path("/home/realmagic/xy/atm-bench-20261110/results")
    q4 = json.loads((q4_root / mapping["q4km"]["run_id"] / "split-commit.json").read_text())["monitor"]
    result = {
        "observed_at": datetime.now().astimezone().isoformat(),
        "run_id": RUN,
        "read_revision": readers[RUN].revision,
        "scope_id": current["scope_id"],
        "status": current["status"],
        "items": len(actual),
        "qa_count": 1013,
        "qa_definition_sha256": batch["qa_definition_sha256"],
        "execution_status": dict(Counter(i["execution_status"] for i in actual.values())),
        "termination_reasons": dict(Counter(i.get("termination_reason") for i in actual.values())),
        "valid_atm_scores": sum(valid_result(v) for v in current["scoring_results"].values()),
        "scoring_summary": overview["summary"]["scoring"]["profiles"]["atm_qs_v1"],
        "missing_judge_targets": missing,
        "inherited_original_answers_verified": len(inherited),
        "inherited_seed_answers_verified": len(seed),
        "inherited_seed_scores_verified": len(states[SEED]["scoring_results"]),
        "source_manifests_unchanged": True,
        "source_valid_scores": sum(valid_result(v) for v in states[SOURCE]["scoring_results"].values()),
        "attempts": [
            {"qa_id": q, "old": summary(original[q], states[SOURCE]), "new": summary(actual[q], current)}
            for q in targets
        ],
        "failed_items": [
            summary(i, current) for i in actual.values() if i["execution_status"] != "completed"
        ],
        "product_turns": turns,
        "active_work": work,
        "product_imports": imports,
        "model": model,
        "q4": {k: q4.get(k) for k in ("run_id", "status", "item_count", "qa_count", "model_name")},
        "q4_active_work": api(8872, "active-work")["work"],
        "disk_free": {p: shutil.disk_usage(p).free for p in ("/home", "/var/tmp")},
        "memory": subprocess.check_output(["free", "-m"], text=True),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
