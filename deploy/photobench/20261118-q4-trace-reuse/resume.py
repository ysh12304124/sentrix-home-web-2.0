"""Resume the preserved Q4 checkpoint once after registering its new wake."""

import hashlib
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
RESULTS = Path("/home/realmagic/xy/atm-bench-20261110/results")
SOURCE = "20261118-175624-2026.11.17-atm-sources-addresses-current-resume-7955c3"


def api(path, payload=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:8872/api/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open("x") as handle:
        json.dump(value, handle, indent=2)


def main(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / "resume-intent.json").exists()
    activation = json.loads((HERE / "activate-response.json").read_text())
    assert activation["source_pointer_unchanged"]
    mapping_path = BASE / "active-runs.json"
    mapping = json.loads(mapping_path.read_text())
    assert mapping["q4km"]["run_id"] == SOURCE
    directory = RESULTS / SOURCE
    pointer = (directory / "snapshot-commit.json").read_bytes()
    metadata = directory / ".snapshots" / json.loads(pointer)["generation"] / "metadata.json"
    old = json.loads(metadata.read_text())["overview"]
    assert old["status"] == "failed" and old["item_count"] == 64
    assert old["qa_count"] == 1013 and old["reasoning"] and old["graph_enabled"]
    budgets = old["runtime_snapshot"]["execution_budgets"]
    assert budgets["turn_timeout"] == 3660 and budgets["qa_concurrency"] == 4
    assert not any(row.get("worker_alive") for row in api("active-work")["work"])
    with urllib.request.urlopen("http://127.0.0.1:8100/v1/models", timeout=15) as response:
        assert json.load(response)["data"][0]["id"] == "qwen3.5-4b-q4_k_m"
    plan = api(f"runs/{SOURCE}/recovery-plan")
    save("recovery-plan.json", plan)
    assert plan["can_resume"] and plan["scope_id"] == old["scope_id"]
    inherited = plan["inherited_qa_count"]
    assert 0 < inherited <= 64 and inherited + plan["remaining_qa_count"] == 1013
    assert not plan["product_version_changed"] and not plan["judge_use_gt_images"]
    assert plan["frozen_scoring_profiles"] == ["atm_qs_v1"]
    payload = {"repair_missing_judges": True}
    save(
        "resume-intent.json",
        {
            "wait_id": wait_id,
            "source_run": SOURCE,
            "before_mapping": mapping,
            "source_pointer_sha256": hashlib.sha256(pointer).hexdigest(),
            "payload": payload,
            "inherited_qa_count": inherited,
            "at": time.time(),
        },
    )
    # A transport timeout after this POST requires inspection, never another POST.
    result = api(f"runs/{SOURCE}/resume", payload)
    save("resume-response.json", result)
    assert result["resumed_from"] == SOURCE and result["resumed_items"] == inherited
    assert result["scope_id"] == old["scope_id"] and not result["retried_qa_ids"]
    assert (directory / "snapshot-commit.json").read_bytes() == pointer
    assert json.loads(mapping_path.read_text()) == mapping
    mapping["q4km"] = result
    temporary = mapping_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(mapping, indent=2) + "\n")
    temporary.replace(mapping_path)
    print(json.dumps({"wait_id": wait_id, **result}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
