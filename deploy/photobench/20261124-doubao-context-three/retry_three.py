"""Keep 1010 original items and submit only three context-failure QA once."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sqlite3
import uuid
from datetime import datetime

import supplement as prior  # noqa: E402
from common import (
    AFTER,
    APP,
    BASE,
    BUILD,
    DB,
    EVALUATOR,
    HERE,
    PRODUCT,
    RELATIVE,
    RELEASE,
    ROOT,
    SEED,
    SOURCE,
    TARGETS,
    api,
    command,
    fingerprint,
    idle,
    manifest_sha,
    pid,
    preserved,
    read,
    runtime,
    save,
    sha,
    source,
)

from photobench.domain.evaluation.profiles import summarize_batches  # noqa: E402
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader  # noqa: E402


def retained_items(state):
    kept = [i for i in state["items"] if i["qa_id"] not in TARGETS]
    assert len(kept) == 1010 and len({i["qa_id"] for i in kept}) == 1010
    assert all(i.get("execution_status") not in {"scheduled", "running"} for i in kept)
    failures = {i["qa_id"] for i in kept if i["execution_status"] in {"failed", "timeout"}}
    assert failures == set(read("authorized-plan.json")["unmodified_timeout_qa_ids_counted_as_incorrect"])
    return kept


def published_runtime():
    publication = read("publish-response.json")
    assert publication["accepted"]
    assert {u: pid(u) for u in (PRODUCT, EVALUATOR)} == publication["pids"]
    assert command(pid(PRODUCT))[command(pid(PRODUCT)).index("--app-dir") + 1] == str(
        RELEASE / "apps/api/src"
    )
    assert command(pid(EVALUATOR))[command(pid(EVALUATOR)).index("--app-dir") + 1] == str(APP)
    assert sha(RELEASE / RELATIVE) == AFTER
    diagnostics, config = runtime()
    assert diagnostics["build_revision"] == config["runtime_config"]["product_build_revision"] == BUILD
    assert preserved(read("publish-intent.json")["source_manifests"])
    return publication


def plan():
    assert not (HERE / "plan.json").exists()
    publication = published_runtime()
    activity = idle()
    state, valid, accepted = source()
    kept = retained_items(state)
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["doubao"]["run_id"] == SOURCE
    turns = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for qa in sorted(TARGETS):
            for row in db.execute(
                "select t.id,t.status,t.termination_reason,t.attempt_no,t.created_at,t.answer "
                "from agent_turns t join agent_conversations c on c.id=t.conversation_id "
                "where t.scope_id=? and c.title=? order by t.created_at",
                (state["scope_id"], qa),
            ):
                value = dict(row)
                answer = value.pop("answer") or ""
                turns.append(
                    {"qa_id": qa, **value, "answer_sha256": hashlib.sha256(answer.encode()).hexdigest()}
                )
    connectivity = prior.connectivity_probe()
    save(
        "plan.json",
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "authorization": (
                "User authorized a simple fix and useful retries on 2026-11-24; "
                "one new product turn for each of three verified context failures only. "
                "Count the other 16 failures as incorrect."
            ),
            "source_run_id": SOURCE,
            "seed_run_id": SEED,
            "source_read_revision": accepted["read_revision"],
            "source_manifest_sha256": manifest_sha(SOURCE),
            "source_all_items_sha256": fingerprint(state["items"]),
            "source_all_scoring_receipts_sha256": fingerprint(state["scoring_results"]),
            "source_valid_score_keys": sorted(valid),
            "source_valid_scores_sha256": fingerprint(valid),
            "source_valid_receipt_fingerprints": {k: fingerprint(v) for k, v in valid.items()},
            "retained_items_sha256": fingerprint(kept),
            "retained_item_fingerprints": {i["qa_id"]: fingerprint(i) for i in kept},
            "retained_failed_qa_ids": read("authorized-plan.json")[
                "unmodified_timeout_qa_ids_counted_as_incorrect"
            ],
            "agent_qa_ids": sorted(TARGETS),
            "prior_product_turns": turns,
            "scope_id": state["scope_id"],
            "publication": publication,
            "other_stage_mapping": {k: v for k, v in mapping.items() if k != "doubao"},
            "activity": activity,
            "connectivity_probe": connectivity,
            "resume_code_sha256": sha(APP / "photobench/application/runs/suites.py"),
            "resume_policy": (
                "retry_qa_ids is explicitly empty; all 1010 non-scheduled/non-running items inherit, "
                "including 16 old failures. repair_missing_judges=false."
            ),
            "label": "历史豆包媒体记忆复用+新版邮件增量；161/49题授权补做；并行工具压缩修复后仅补3上下文题",
        },
    )
    print(
        json.dumps(
            {
                "planned_new_turns": 3,
                "retained_items": 1010,
                "retained_valid_scores": 994,
                "retained_failures": 16,
                "product_build_revision": BUILD,
            }
        ),
        flush=True,
    )


def seed():
    assert not (HERE / "seed-intent.json").exists() and not (ROOT / SEED).exists()
    plan = read("plan.json")
    published_runtime()
    idle()
    state, valid, _ = source()
    state["items"] = retained_items(state)
    assert fingerprint(state["items"]) == plan["retained_items_sha256"]
    assert fingerprint(valid) == plan["source_valid_scores_sha256"]
    state["scoring_results"] = {
        k: v for k, v in state["scoring_results"].items() if v.get("qa_id") not in TARGETS
    }
    state["diagnostic_results"] = {
        k: v for k, v in (state.get("diagnostic_results") or {}).items() if v.get("qa_id") not in TARGETS
    }
    state.pop("rejudge", None)
    state["scoring_summaries"] = summarize_batches(state)
    state.update(
        run_id=SEED,
        status="failed",
        current_phase=None,
        resumed_from=SOURCE,
        fatal_error=(
            "Independent seed for three authorized context retries; "
            "16 original failures are inherited unchanged."
        ),
        authorized_context_retries={
            "source_run_id": SOURCE,
            "targets": sorted(TARGETS),
            "plan_directory": str(HERE),
            "patch_build_revision": BUILD,
            "retained_failed_qa_ids": plan["retained_failed_qa_ids"],
        },
    )
    save(
        "seed-intent.json",
        {
            "source_manifest_sha256": manifest_sha(SOURCE),
            "seed_run_id": SEED,
            "inherited_items": 1010,
            "inherited_valid_scores": 994,
            "inherited_failed_items": 16,
            "authorized_new_turns": 3,
        },
    )
    receipt = SplitResultWriter(ROOT).write(ROOT / SEED, state)
    checked = SplitRunReader(ROOT, ROOT / SEED).state()
    assert fingerprint(checked["items"]) == plan["retained_items_sha256"]
    assert (
        fingerprint({k: checked["scoring_results"][k] for k in plan["source_valid_score_keys"]})
        == plan["source_valid_scores_sha256"]
    )
    assert manifest_sha(SOURCE) == plan["source_manifest_sha256"]
    save(
        "seed-response.json",
        {
            "seed_run_id": SEED,
            "seed_manifest_sha256": manifest_sha(SEED),
            "inherited_items": 1010,
            "inherited_valid_scores": 994,
            "inherited_failed_items": 16,
            "remaining_agents": 3,
            "source_manifest_unchanged": True,
            "write_receipt": receipt,
        },
    )
    print(json.dumps(read("seed-response.json")), flush=True)


def resume(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / "resume-intent.json").exists()
    plan = read("plan.json")
    publication = published_runtime()
    idle()
    source()
    seed_state = SplitRunReader(ROOT, ROOT / SEED).state()
    assert fingerprint(seed_state["items"]) == plan["retained_items_sha256"]
    recovery = api(8874, f"runs/{SEED}/recovery-plan")
    assert (
        recovery["can_resume"]
        and recovery["inherited_qa_count"] == 1010
        and recovery["remaining_qa_count"] == 3
    )
    assert recovery["product_version_changed"] and recovery["current_product_build_revision"] == BUILD
    assert {i["qa_id"] for i in seed_state["items"]} == set(plan["retained_item_fingerprints"])
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["doubao"]["run_id"] == SOURCE
    assert {k: v for k, v in mapping.items() if k != "doubao"} == plan["other_stage_mapping"]
    payload = {"repair_missing_judges": False, "retry_qa_ids": [], "product_build_revision": BUILD}
    save(
        "resume-intent.json",
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "wait_id": wait_id,
            "seed_run_id": SEED,
            "source_run_id": SOURCE,
            "payload": payload,
            "recovery_plan": recovery,
            "publication": publication,
        },
    )
    result = api(8874, f"runs/{SEED}/resume", payload)
    save("resume-response.json", result)
    assert result["resumed_from"] == SEED and result["resumed_items"] == 1010
    assert not result["retried_qa_ids"] and result["repair_missing_judges"] is False
    assert result["scope_id"] == plan["scope_id"]
    assert json.loads((BASE / "active-runs.json").read_text()) == mapping
    mapping["doubao"] = result
    temporary = HERE / "active-runs.next.json"
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / "active-runs.json")
    print(json.dumps(result), flush=True)


def snapshot():
    result = prior.snapshot()
    publication = read("publish-response.json")
    plan = read("plan.json")
    response = read("resume-response.json")
    result["exact_run_matches"] = result["run_id"] == response["run_id"]
    result["service_pids_match"] = result["service_pids"] == publication["pids"]
    args = command(result["service_pids"][PRODUCT])
    result["product_app_matches"] = args[args.index("--app-dir") + 1] == publication["product_app_dir"]
    result["product_sources_match"] = (
        sha(RELEASE / RELATIVE) == AFTER
        and sha(RELEASE / "apps/api/src/sentrix/domain/assistant/request_budget.py")
        == prior.EXPECTED_PRODUCT_SOURCES["domain/assistant/request_budget.py"]
    )
    result["product_source_sha256"] = {"application/assistant/run_turn.py": sha(RELEASE / RELATIVE)}
    result["source_manifest_unchanged"] = manifest_sha(SOURCE) == plan["source_manifest_sha256"]
    result["seed_manifest_unchanged"] = (
        manifest_sha(SEED) == read("seed-response.json")["seed_manifest_sha256"]
    )
    result["all_source_manifests_unchanged"] = preserved(read("publish-intent.json")["source_manifests"])
    result["other_stage_mapping_unchanged"] = {
        k: v for k, v in json.loads((BASE / "active-runs.json").read_text()).items() if k != "doubao"
    } == plan["other_stage_mapping"]
    diagnostics, config = runtime()
    result["product_build_revision"] = diagnostics["build_revision"]
    result["product_build_revision_matches"] = (
        diagnostics["build_revision"] == config["runtime_config"]["product_build_revision"] == BUILD
    )
    result["requested_context_retry_qa_ids"] = sorted(TARGETS)
    result["retained_failure_count"] = 16
    return result


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["plan", "seed", "resume", "probe"])
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.operation == "resume":
        resume(args.wait_id)
    elif args.operation == "probe":
        print(json.dumps(snapshot(), ensure_ascii=False), flush=True)
    else:
        {"plan": plan, "seed": seed}[args.operation]()
