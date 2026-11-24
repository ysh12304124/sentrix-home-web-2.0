"""Accept the three saved context retries without submitting Agent or Judge work."""

from __future__ import annotations

import hashlib
import json
import math
import resource
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
PRIOR = BASE / "doubao-context-three-20261124-v1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(PRIOR))
import common as c  # noqa: E402
from inspect_reconnect import identity  # noqa: E402
from retry_three import snapshot as original_snapshot  # noqa: E402

from photobench.domain.evaluation.profiles import scoring_targets, valid_result  # noqa: E402
from photobench.infrastructure.results.split_store import SplitRunReader  # noqa: E402

APP, ROOT, BATCH, DB = c.APP, c.ROOT, c.BATCH, c.DB
SOURCE, SEED = c.SOURCE, c.SEED
RUN = "20261124-042008-2026.11.17-atm-sources-addresses-current-resume-84baa3"
api, fingerprint, manifest_sha = c.api, c.fingerprint, c.manifest_sha


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def content(item):
    return {k: v for k, v in item.items() if k != "_index"}


def snapshot():
    value = original_snapshot()
    pin = json.loads((HERE / "runtime-pin.json").read_text())
    value["published_service_pids_match"] = value["service_pids_match"]
    value["service_pids_match"] = value["service_pids"] == pin["service_pids"]
    value["runtime_pin_basis"] = pin["basis"]
    return value


def current():
    assert json.loads((BASE / "active-runs.json").read_text())["doubao"]["run_id"] == RUN
    reader = SplitRunReader(ROOT, ROOT / RUN)
    state = reader.state()
    assert state["status"] in {"completed", "completed_with_errors"}
    assert not state.get("fatal_error") and not state.get("current_phase")
    assert len(state["items"]) == state["qa_count"] == 1013
    batch = next(b for b in state["scoring_batches"] if b["batch_id"] == BATCH)
    source, _, _ = c.source()
    frozen = next(b for b in source["scoring_batches"] if b["batch_id"] == BATCH)
    assert fingerprint({k: v for k, v in batch.items() if k not in {"materials", "targets"}}) == (
        fingerprint({k: v for k, v in frozen.items() if k not in {"materials", "targets"}})
    )
    assert set(frozen["targets"]) <= set(batch["targets"])
    assert len(batch["targets"]) == len(set(batch["targets"]))
    assert frozen["materials"].keys() <= batch["materials"].keys()
    assert all(fingerprint(v) == fingerprint(batch["materials"][k]) for k, v in frozen["materials"].items())
    assert all(
        k.rsplit(":", 1)[0] in c.TARGETS for k in batch["materials"].keys() - frozen["materials"].keys()
    )
    assert all(
        state["scoring_results"][k]["qa_id"] in c.TARGETS
        for k in set(batch["targets"]) - set(frozen["targets"])
    )
    return reader, state, batch


def inspect():
    assert not (HERE / "terminal-acceptance.json").exists()
    probe = snapshot()
    for key in (
        "exact_run_matches",
        "service_pids_match",
        "evaluation_app_matches",
        "writer_source_matches",
        "source_manifest_unchanged",
        "prior_source_manifest_unchanged",
        "prior_manifests_unchanged",
        "seed_manifest_unchanged",
        "other_stage_mapping_unchanged",
        "product_app_matches",
        "product_sources_match",
        "previous_supplement_manifests_unchanged",
        "all_source_manifests_unchanged",
        "product_build_revision_matches",
        "model_matches",
        "product_limits_match",
        "product_database_match",
    ):
        assert probe[key], key
    assert not probe["active"] and not probe["live_turns"]
    assert probe["reasoning"] and probe["graph_enabled"] and probe["reasoning_context_tokens"] == 16384
    assert probe["execution_budgets"]["qa_concurrency"] == 4
    assert probe["execution_budgets"]["turn_timeout"] == 3660
    idle = c.idle()
    reader, state, batch = current()
    plan = c.read("plan.json")
    original, original_valid, _ = c.source()
    items = {i["qa_id"]: i for i in state["items"]}
    old_items = {i["qa_id"]: i for i in original["items"]}
    assert len(items) == 1013 and set(items) == {r["qa_id"] for r in state["qa_snapshot"]["rows"]}
    retained = set(items) - c.TARGETS
    assert len(retained) == 1010 and retained == set(plan["retained_item_fingerprints"])
    removed_index = []
    for qa in retained:
        assert fingerprint(old_items[qa]) == plan["retained_item_fingerprints"][qa]
        assert fingerprint(content(items[qa])) == fingerprint(content(old_items[qa])), qa
        if fingerprint(items[qa]) != fingerprint(old_items[qa]):
            assert "_index" in old_items[qa] and "_index" not in items[qa], qa
            removed_index.append(qa)
    assert fingerprint(original_valid) == plan["source_valid_scores_sha256"]
    for key, digest in plan["source_valid_receipt_fingerprints"].items():
        assert fingerprint(state["scoring_results"][key]) == digest, key
    seed = SplitRunReader(ROOT, ROOT / SEED).state()
    assert len(seed["items"]) == 1010
    assert all(fingerprint(content(i)) == fingerprint(content(items[i["qa_id"]])) for i in seed["items"])
    assert all(
        fingerprint(v) == fingerprint(state["scoring_results"][k]) for k, v in seed["scoring_results"].items()
    )
    turns, errors = [], []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute(
            "select t.id,t.status,t.termination_reason,t.attempt_no,t.failure_count,"
            "t.execution_state,t.waiting_reason,t.answer,t.created_at,t.updated_at,c.title as qa_id "
            "from agent_turns t join agent_conversations c on c.id=t.conversation_id "
            "where t.scope_id=? and julianday(t.created_at)>=julianday(?) order by t.created_at",
            (state["scope_id"], state["started_at"]),
        ):
            turn = dict(row)
            answer = turn.pop("answer") or ""
            qa = turn["qa_id"]
            budget_row = db.execute(
                "select payload_json from agent_turn_events_v2 where turn_id=? "
                "and json_extract(payload_json,'$.kind')='request_budget' order by event_seq desc limit 1",
                (turn["id"],),
            ).fetchone()
            events = [
                json.loads(r[0])
                for r in db.execute(
                    "select payload_json from agent_turn_events_v2 where turn_id=? "
                    "and json_extract(payload_json,'$.kind') in ('error','done','termination','model_error') "
                    "order by event_seq",
                    (turn["id"],),
                )
            ]
            generations = [
                dict(r)
                for r in db.execute(
                    "select attempt_no,count(*) as events,min(event_seq) as first_seq,"
                    "max(event_seq) as last_seq "
                    "from agent_turn_events_v2 where turn_id=? group by attempt_no",
                    (turn["id"],),
                )
            ]
            turn.update(
                answer_chars=len(answer),
                answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
                item_answer_matches=answer == (items[qa].get("answer") or ""),
                recorded_turn_ids=[t.get("turn_id") for t in items[qa].get("runtime_turns") or []],
                budget=json.loads(budget_row[0])["payload"] if budget_row else None,
                event_attempt_counts=generations,
            )
            turns.append(turn)
            errors.append({"qa_id": qa, "turn_id": turn["id"], "events": events})
    counts = Counter(t["qa_id"] for t in turns)
    turn_ids = {t["id"] for t in turns}
    valid = {
        k: v for k, v in state["scoring_results"].items() if v.get("batch_id") == BATCH and valid_result(v)
    }
    failures = {
        qa: {k: i.get(k) for k in ("execution_status", "termination_reason", "error", "error_type")}
        for qa, i in items.items()
        if i["execution_status"] != "completed"
    }
    missing = [t for t in scoring_targets(state, batch, "missing") if not t.get("diagnostics_only")]
    checks = {
        "exactly_three_new_turns": len(turns) == len(turn_ids) == 3,
        "one_turn_per_authorized_qa": set(counts) == c.TARGETS and set(counts.values()) == {1},
        "original_turns_not_reused": not turn_ids & {t["id"] for t in plan["prior_product_turns"]},
        "all_product_turns_terminal": all(
            t["status"] in {"complete", "partial", "blocked", "failed", "cancelled"} for t in turns
        ),
        "completed_answers_match_product": all(
            t["item_answer_matches"] and t["id"] in t["recorded_turn_ids"]
            for t in turns
            if items[t["qa_id"]]["execution_status"] == "completed"
        ),
        "all_turn_budgets_match": all(
            t["budget"]
            and [
                t["budget"][k]
                for k in ("max_model_calls", "max_tool_calls", "max_search_calls", "max_other_tool_calls")
            ]
            == [100, 600, 100, 500]
            for t in turns
        ),
        "all_failures_keep_null_scores": not set(failures) & {v["qa_id"] for v in valid.values()},
        "sixteen_original_failures_preserved": set(plan["retained_failed_qa_ids"]) <= set(failures),
    }
    total = math.fsum(v["score"] for v in valid.values())
    result = {
        "observed_at": datetime.now().astimezone().isoformat(),
        "run_id": RUN,
        "source_run_id": SOURCE,
        "seed_run_id": SEED,
        "read_revision": reader.revision,
        "manifest_sha256": manifest_sha(RUN),
        "qa_count": 1013,
        "item_count": len(items),
        "execution_counts": dict(Counter(i["execution_status"] for i in items.values())),
        "new_three_execution_counts": dict(Counter(items[q]["execution_status"] for q in c.TARGETS)),
        "new_three_product_status_counts": dict(Counter(t["status"] for t in turns)),
        "new_three_product_termination_counts": dict(Counter(str(t["termination_reason"]) for t in turns)),
        "product_attempt_no_counts": dict(Counter(t["attempt_no"] for t in turns)),
        "valid_atm_scores": len(valid),
        "new_valid_scores": len(valid) - 994,
        "valid_mean": total / len(valid),
        "coverage": len(valid) / 1013,
        "all_1013_missing_as_zero_mean": total / 1013,
        "valid_score_keys": list(valid),
        "valid_receipts_reference_sha256": fingerprint(valid),
        "all_items_reference_sha256": fingerprint(state["items"]),
        "all_scoring_receipts_sha256": fingerprint(state["scoring_results"]),
        "execution_failures": failures,
        "missing_targets": missing,
        "judge_failures": {
            k: identity(v)
            for k, v in state["scoring_results"].items()
            if v.get("batch_id") == BATCH and v.get("status") == "failed"
        },
        "new_attempt_comparison": [
            {
                "qa_id": qa,
                "old": {
                    k: old_items[qa].get(k)
                    for k in ("execution_status", "termination_reason", "error", "error_type")
                },
                "new": failures.get(qa),
                "product": next(t for t in turns if t["qa_id"] == qa),
            }
            for qa in sorted(c.TARGETS)
        ],
        "all_new_turns": turns,
        "failed_product_events": errors,
        "original_994_answer_content_unchanged": True,
        "original_994_valid_receipts_unchanged": True,
        "source_all_items_and_all_scores_unchanged": True,
        "seed_items_and_all_scores_unchanged": True,
        "source_manifests_unchanged": True,
        "checks": checks,
        "accepted": all(checks.values()),
        "removed_internal_index_qa_ids": sorted(removed_index),
        "item_comparison_excluded_fields": ["_index"],
        "probe": probe,
        "idle": idle,
        "agent_calls_in_acceptance": 0,
        "judge_calls_in_acceptance": 0,
    }
    save("terminal-acceptance.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "run_id",
                    "read_revision",
                    "execution_counts",
                    "new_three_product_status_counts",
                    "new_three_product_termination_counts",
                    "valid_atm_scores",
                    "new_valid_scores",
                    "checks",
                    "accepted",
                )
            }
            | {"missing_judge_count": len(missing)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    assert result["accepted"], checks


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    inspect()
