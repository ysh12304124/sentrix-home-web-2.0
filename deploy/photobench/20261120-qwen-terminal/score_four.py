"""Judge four saved answers once and verify all previous score receipts."""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import uuid
from pathlib import Path

from inspect_terminal import BASE, BATCH, KEEP_FAILED, ROOT, RUN, identity

from photobench.domain.evaluation.profiles import scoring_targets, valid_result
from photobench.infrastructure.results.split_store import SplitRunReader

HERE = Path(__file__).resolve().parent
QA = {
    "atm-fcfcb760-665f-4d0d-9bdb-fabc49e1ae7c",
    "atm-ef651ed4-aeb5-4528-9003-ac8be603e263",
    "atm-bd03c436-4698-4baa-8a89-6ae11a72efc9",
    "atm-41e4a473-65d8-4c5b-a73a-27e578157fac",
}


def api(path, data=None):
    request = urllib.request.Request(
        "http://127.0.0.1:8874/api/v1/" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def state():
    assert json.loads((BASE / "active-runs.json").read_text())["qwen37"]["run_id"] == RUN
    reader = SplitRunReader(ROOT, ROOT / RUN)
    current = reader.state()
    assert current["status"] == "completed_with_errors"
    assert len(current["items"]) == current["qa_count"] == 1013
    failures = [i["qa_id"] for i in current["items"] if i["execution_status"] != "completed"]
    assert failures == [KEEP_FAILED]
    batch = next(b for b in current["scoring_batches"] if b["batch_id"] == BATCH)
    frozen = json.loads((BASE / "scoring-frozen.json").read_text())
    assert batch["config"] == frozen["scoring_configs"]["atm_qs_v1"]
    assert batch["rule_version"] == "scoring-2026.11.12-v5"
    assert batch["judge"]["model"] == "doubao-seed-2.1-lite"
    return reader, current, batch


def inspect():
    assert not api("active-work")["work"]
    reader, current, batch = state()
    targets = scoring_targets(current, batch, "missing")
    assert len(targets) == 4 and {t["qa_id"] for t in targets} == QA
    assert not any(t.get("diagnostics_only") for t in targets)
    valid = {k: v for k, v in current["scoring_results"].items() if valid_result(v)}
    assert len(valid) == 1008
    payload = {"resume_batch_ids": [BATCH], "scope": "missing", "qa_ids": sorted(QA)}
    preview = api(f"runs/{RUN}/scoring-preview", payload)
    assert preview["qa_count"] == preview["subscore_count"] == 4
    assert preview["diagnostic_only_count"] == 0
    plan = {
        "run_id": RUN,
        "source_revision": reader.revision,
        "valid_scores_before": len(valid),
        "valid_score_keys": list(valid),
        "valid_score_receipts_sha256": reader.reader.fingerprint(valid),
        "answers_sha256": reader.reader.fingerprint(identity(current["items"])),
        "targets": targets,
        "payload": payload,
        "preview": preview,
        "retained_execution_failure": KEEP_FAILED,
        "agent_rerun": False,
    }
    save("scoring-plan.json", plan)
    print(json.dumps({"run_id": RUN, "judge_targets": 4, "valid_scores_before": 1008}), flush=True)


def launch(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / "scoring-intent.json").exists()
    plan = json.loads((HERE / "scoring-plan.json").read_text())
    assert not api("active-work")["work"]
    reader, _, _ = state()
    assert reader.revision == plan["source_revision"]
    save("scoring-intent.json", {"wait_id": wait_id, **plan})
    response = api(f"runs/{RUN}/rejudge", plan["payload"])
    save("scoring-response.json", response)
    assert response["run_id"] == RUN and response["total"] == 4
    assert response["status"] == "running"
    print(json.dumps(response), flush=True)


def probe(task_id):
    reader = SplitRunReader(ROOT, ROOT / RUN)
    current = reader.overview()
    activity = current.get("rejudge") or {}
    assert activity["rejudge_id"] == task_id
    work = api("active-work")["work"]
    return {
        "run_id": RUN,
        "rejudge_id": task_id,
        **{k: activity.get(k) for k in ("status", "completed", "total", "failed", "error")},
        "worker_alive": any(w["id"] == task_id and w["worker_alive"] for w in work),
        "scoring": current["summary"]["scoring"]["profiles"]["atm_qs_v1"],
        "disk_free": {p: shutil.disk_usage(p).free for p in ("/home", "/var/tmp")},
    }


def accept():
    assert not api("active-work")["work"]
    plan = json.loads((HERE / "scoring-plan.json").read_text())
    response = json.loads((HERE / "scoring-response.json").read_text())
    reader, current, batch = state()
    activity = current["rejudge"]
    assert activity["rejudge_id"] == response["rejudge_id"]
    assert activity["status"] == "completed" and activity["completed"] == activity["total"] == 4
    assert not activity["failed"] and not activity.get("error")
    valid = {k: v for k, v in current["scoring_results"].items() if valid_result(v)}
    assert len(valid) == 1012
    previous = {k: valid[k] for k in plan["valid_score_keys"]}
    assert len(previous) == 1008
    assert reader.reader.fingerprint(previous) == plan["valid_score_receipts_sha256"]
    assert reader.reader.fingerprint(identity(current["items"])) == plan["answers_sha256"]
    assert not scoring_targets(current, batch, "missing")
    receipt = {
        "run_id": RUN,
        "rejudge_id": activity["rejudge_id"],
        "read_revision": reader.revision,
        "items": 1013,
        "completed_answers": 1012,
        "valid_atm_scores": 1012,
        "unchanged_previous_valid_receipts": 1008,
        "all_answers_unchanged": True,
        "retained_execution_failure": KEEP_FAILED,
        "agent_rerun": False,
        "four_results": [
            {"qa_id": t["qa_id"], "status": valid[t["key"]]["status"], "score": valid[t["key"]]["score"]}
            for t in plan["targets"]
        ],
        "scoring": reader.overview()["summary"]["scoring"]["profiles"]["atm_qs_v1"],
    }
    save("scoring-acceptance.json", receipt)
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=("inspect", "launch", "probe", "accept"))
    parser.add_argument("--wait-id")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    if args.operation == "inspect":
        inspect()
    elif args.operation == "launch":
        launch(args.wait_id)
    elif args.operation == "accept":
        accept()
    else:
        print(json.dumps(probe(args.task_id)), flush=True)
