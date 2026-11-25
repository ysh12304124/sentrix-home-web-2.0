"""Resume the failed Q4 QA checkpoint once, after a new wake registration."""

import hashlib
import json
import time
import urllib.request
import uuid

from publish import BASE, HERE, RESULTS, api, save


def resume(wait_id):
    uuid.UUID(wait_id)
    assert (HERE / "activate-response.json").exists()
    mapping_path = BASE / "active-runs.json"
    mapping = json.loads(mapping_path.read_text())
    original = mapping["q4km"]
    run_id = original["run_id"]
    assert run_id == "20261118-111519-2026.11.17-atm-sources-addresses-current-resume-90f21f"
    directory = RESULTS / run_id
    pointer = (directory / "snapshot-commit.json").read_bytes()
    generation = json.loads(pointer)["generation"]
    old = json.loads((directory / ".snapshots" / generation / "metadata.json").read_text())["overview"]
    assert old["status"] == "failed" and old["item_count"] == 51
    assert old["reasoning"] is True and old["graph_enabled"] is True
    budgets = old["runtime_snapshot"]["execution_budgets"]
    assert budgets["turn_timeout"] == 3660 and budgets["qa_concurrency"] == 4
    assert not api(8872, "active-work")["work"]
    with urllib.request.urlopen("http://127.0.0.1:8100/v1/models", timeout=15) as response:
        assert json.load(response)["data"][0]["id"] == "qwen3.5-4b-q4_k_m"
    plan = api(8872, f"runs/{run_id}/recovery-plan", timeout=90)
    save("recovery-plan-accepted.json", plan)
    assert plan["can_resume"] and plan["remaining_qa_count"] == 962, plan
    assert not plan["product_version_changed"] and not plan["judge_use_gt_images"]
    payload = {"repair_missing_judges": True}
    save(
        "qa-resume-intent.json",
        {
            "wait_id": wait_id,
            "before": original,
            "payload": payload,
            "source_pointer_sha256": hashlib.sha256(pointer).hexdigest(),
            "at": time.time(),
        },
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:8872/api/v1/runs/{run_id}/resume",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    save("qa-resume-response.json", result)
    assert result["resumed_from"] == run_id and result["resumed_items"] == 51
    assert result["scope_id"] == old["scope_id"] and not result["retried_qa_ids"]
    assert (directory / "snapshot-commit.json").read_bytes() == pointer
    assert json.loads(mapping_path.read_text()) == mapping
    mapping["q4km"] = result
    temporary = mapping_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(mapping, indent=2) + "\n")
    temporary.replace(mapping_path)
    print(json.dumps({"wait_id": wait_id, **result}), flush=True)


if __name__ == "__main__":
    import sys

    resume(sys.argv[1])
