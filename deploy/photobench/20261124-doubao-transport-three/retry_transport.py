"""Retry only three transport failures; inherit 994 scores and 16 timeout records."""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
HERE = Path(__file__).resolve().parent
PREVIOUS = BASE / "doubao-context-terminal-20261124-v1"
sys.path.insert(0, str(PREVIOUS))
import accept_context as previous  # noqa: E402

c = previous.c
APP, ROOT, DB, BATCH = c.APP, c.ROOT, c.DB, c.BATCH
TARGETS = c.TARGETS
SOURCE = previous.RUN
SEED = "20261124-doubao-transport3-seed-84baa3"
REVISION = "dc64f4aa2d3d410c866d9e171cf7804b"
api, fingerprint, sha = c.api, c.fingerprint, c.sha
from photobench.domain.evaluation.profiles import summarize_batches, valid_result  # noqa: E402
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader  # noqa: E402


def now():
    return datetime.now().astimezone().isoformat()


def read(name):
    return json.loads((HERE / name).read_text())


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def manifest(run):
    return sha(ROOT / run / "split-commit.json")


def source():
    accepted = json.loads((PREVIOUS / "terminal-acceptance.json").read_text())
    exported = json.loads((PREVIOUS / "exports-acceptance.json").read_text())
    assert accepted["accepted"] and all(accepted["checks"].values())
    assert exported["read_revision"] == accepted["read_revision"] == REVISION
    assert all(v["verified"] for v in exported["exports"].values())
    assert manifest(SOURCE) == accepted["manifest_sha256"]
    reader = SplitRunReader(ROOT, ROOT / SOURCE)
    assert reader.revision == REVISION
    state = reader.state()
    assert fingerprint(state["items"]) == accepted["all_items_reference_sha256"]
    assert fingerprint(state["scoring_results"]) == accepted["all_scoring_receipts_sha256"]
    valid = {
        k: v for k, v in state["scoring_results"].items() if v.get("batch_id") == BATCH and valid_result(v)
    }
    assert len(state["items"]) == state["qa_count"] == 1013 and len(valid) == 994
    assert not TARGETS & {v["qa_id"] for v in valid.values()}
    return state, valid


def retained(state):
    items = [i for i in state["items"] if i["qa_id"] not in TARGETS]
    failures = {i["qa_id"] for i in items if i["execution_status"] in {"failed", "timeout"}}
    expected = c.read("authorized-plan.json")["unmodified_timeout_qa_ids_counted_as_incorrect"]
    assert len(items) == 1010 and failures == set(expected) and len(failures) == 16
    assert all(i["execution_status"] not in {"scheduled", "running"} for i in items)
    return items


def runtime_idle():
    p = previous.snapshot()
    for key in (
        "service_pids_match",
        "evaluation_app_matches",
        "writer_source_matches",
        "product_app_matches",
        "product_sources_match",
        "product_build_revision_matches",
        "model_matches",
        "product_limits_match",
        "product_database_match",
    ):
        assert p[key], key
    assert not p["active"] and not p["live_turns"]
    assert p["reasoning"] and p["graph_enabled"] and p["reasoning_context_tokens"] == 16384
    assert p["execution_budgets"]["qa_concurrency"] == 4
    assert p["execution_budgets"]["turn_timeout"] == 3660
    c.idle()
    return p


def plan():
    assert not (HERE / "plan.json").exists()
    p = runtime_idle()
    state, valid = source()
    kept = retained(state)
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["doubao"]["run_id"] == SOURCE
    turns = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for qa in sorted(TARGETS):
            turns.extend(
                dict(r) | {"qa_id": qa}
                for r in db.execute(
                    "select t.id,t.status,t.termination_reason,t.attempt_no,t.created_at "
                    "from agent_turns t join agent_conversations c on c.id=t.conversation_id "
                    "where t.scope_id=? and c.title=? order by t.created_at",
                    (state["scope_id"], qa),
                )
            )
    connectivity = c.prior.connectivity_probe()
    save(
        "plan.json",
        {
            "created_at": now(),
            "authorization": "User explicitly authorized three transport retries "
            "on 2026-11-24 and read-only answerability review of 16 timeout QA. One new product turn "
            "per transport QA; no automatic retries of the other 16.",
            "source_run_id": SOURCE,
            "source_read_revision": REVISION,
            "source_manifest_sha256": manifest(SOURCE),
            "seed_run_id": SEED,
            "source_all_items_sha256": fingerprint(state["items"]),
            "source_all_scores_sha256": fingerprint(state["scoring_results"]),
            "valid_score_keys": sorted(valid),
            "valid_scores_sha256": fingerprint(valid),
            "retained_items_sha256": fingerprint(kept),
            "retained_item_fingerprints": {i["qa_id"]: fingerprint(i) for i in kept},
            "valid_receipt_fingerprints": {k: fingerprint(v) for k, v in valid.items()},
            "source_manifests": c.frozen_manifests(),
            "other_stage_mapping": {k: v for k, v in mapping.items() if k != "doubao"},
            "targets": sorted(TARGETS),
            "prior_product_turns": turns,
            "scope_id": state["scope_id"],
            "runtime": p,
            "connectivity_probe": connectivity,
            "product_build_revision": c.BUILD,
            "label": (
                "历史豆包媒体记忆复用+新版邮件增量；161/49题补做；"
                "context局部修复后3题因传输失败再次授权重测"
            ),
            "resume_policy": {"retry_qa_ids": [], "repair_missing_judges": False},
        },
    )
    print(
        json.dumps(
            {
                "planned_targets": sorted(TARGETS),
                "retained_items": 1010,
                "retained_scores": 994,
                "connectivity_probe": connectivity,
            }
        ),
        flush=True,
    )


def seed():
    assert not (HERE / "seed-intent.json").exists() and not (ROOT / SEED).exists()
    p = read("plan.json")
    runtime_idle()
    state, valid = source()
    state["items"] = retained(state)
    assert fingerprint(state["items"]) == p["retained_items_sha256"]
    assert fingerprint(valid) == p["valid_scores_sha256"]
    assert all(v.get("qa_id") not in TARGETS for v in state["scoring_results"].values())
    state.pop("rejudge", None)
    state["scoring_summaries"] = summarize_batches(state)
    state.update(
        run_id=SEED,
        status="failed",
        current_phase=None,
        resumed_from=SOURCE,
        fatal_error="Independent seed for three authorized transport retries; 16 failures retained.",
        authorized_transport_retries={"targets": sorted(TARGETS), "source_run_id": SOURCE},
    )
    save(
        "seed-intent.json",
        {
            "created_at": now(),
            "source_run_id": SOURCE,
            "seed_run_id": SEED,
            "inherited_items": 1010,
            "valid_scores": 994,
            "new_targets": 3,
        },
    )
    receipt = SplitResultWriter(ROOT).write(ROOT / SEED, state)
    checked = SplitRunReader(ROOT, ROOT / SEED).state()
    assert fingerprint(checked["items"]) == p["retained_items_sha256"]
    assert fingerprint(checked["scoring_results"]) == p["source_all_scores_sha256"]
    assert c.preserved(p["source_manifests"])
    save(
        "seed-response.json",
        {
            "seed_run_id": SEED,
            "seed_manifest_sha256": manifest(SEED),
            "inherited_items": 1010,
            "valid_scores": 994,
            "receipt": receipt,
        },
    )
    print(json.dumps(read("seed-response.json")), flush=True)


def resume(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / "resume-intent.json").exists()
    p = read("plan.json")
    runtime_idle()
    source()
    assert c.preserved(p["source_manifests"])
    assert manifest(SEED) == read("seed-response.json")["seed_manifest_sha256"]
    recovery = api(8874, f"runs/{SEED}/recovery-plan")
    assert recovery["can_resume"] and recovery["inherited_qa_count"] == 1010
    assert recovery["remaining_qa_count"] == 3 and not recovery["product_version_changed"]
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["doubao"]["run_id"] == SOURCE
    assert {k: v for k, v in mapping.items() if k != "doubao"} == p["other_stage_mapping"]
    payload = {"retry_qa_ids": [], "repair_missing_judges": False, "product_build_revision": c.BUILD}
    save(
        "resume-intent.json",
        {
            "created_at": now(),
            "wait_id": wait_id,
            "source_run_id": SOURCE,
            "seed_run_id": SEED,
            "payload": payload,
            "recovery_plan": recovery,
        },
    )
    result = api(8874, f"runs/{SEED}/resume", payload)
    save("resume-response.json", result)
    assert result["resumed_from"] == SEED and result["resumed_items"] == 1010
    assert not result["retried_qa_ids"] and result["repair_missing_judges"] is False
    assert result["scope_id"] == p["scope_id"]
    assert json.loads((BASE / "active-runs.json").read_text()) == mapping
    mapping["doubao"] = result
    temporary = HERE / "active-runs.next.json"
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / "active-runs.json")
    print(json.dumps(result), flush=True)


def snapshot():
    p, response = read("plan.json"), read("resume-response.json")
    result = previous.snapshot()
    result.update(
        exact_run_matches=result["run_id"] == response["run_id"],
        source_manifest_unchanged=manifest(SOURCE) == p["source_manifest_sha256"],
        all_source_manifests_unchanged=c.preserved(p["source_manifests"]),
        seed_manifest_unchanged=manifest(SEED) == read("seed-response.json")["seed_manifest_sha256"],
        service_pids_match=result["service_pids"] == p["runtime"]["service_pids"],
        other_stage_mapping_unchanged={
            k: v for k, v in json.loads((BASE / "active-runs.json").read_text()).items() if k != "doubao"
        }
        == p["other_stage_mapping"],
        requested_transport_retry_qa_ids=sorted(TARGETS),
        retained_failure_count=16,
    )
    return result


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=("plan", "seed", "resume", "probe"))
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.operation == "probe":
        print(json.dumps(snapshot(), ensure_ascii=False), flush=True)
    elif args.operation == "resume":
        resume(args.wait_id)
    else:
        {"plan": plan, "seed": seed}[args.operation]()
