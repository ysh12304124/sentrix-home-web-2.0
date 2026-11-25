"""Read back committed checkpoints and verify the preserved answer prefix."""

import hashlib
import json
import time
import urllib.request
from pathlib import Path

from photobench.infrastructure.results.encoding import open_snapshot
from photobench.infrastructure.results.store import committed_path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
RESULTS = Path("/home/realmagic/xy/atm-bench-20261110/results")
response = json.loads((HERE / "resume-response.json").read_text())
intent = json.loads((HERE / "resume-intent.json").read_text())
source = RESULTS / response["resumed_from"]
target = RESULTS / response["run_id"]
assert json.loads((BASE / "active-runs.json").read_text())["q4km"]["run_id"] == target.name
pointer = (source / "snapshot-commit.json").read_bytes()
assert hashlib.sha256(pointer).hexdigest() == intent["source_pointer_sha256"]
matched = 0
before = hashlib.sha256()
after = hashlib.sha256()
with (
    open_snapshot(committed_path(source, "results.jsonl")) as old,
    open_snapshot(committed_path(target, "results.jsonl")) as new,
):
    for line in old:
        item = json.loads(line)
        if item.get("execution_status") in {"scheduled", "running"}:
            continue
        del item
        target_line = next(new)
        assert target_line == line, f"inherited answer changed at index {matched}"
        before.update(line.encode())
        after.update(target_line.encode())
        matched += 1
assert matched == response["resumed_items"]
metadata_path = committed_path(target, "metadata.json")
state = json.loads(metadata_path.read_text())["overview"]
assert state["scope_id"] == response["scope_id"]
assert state["qa_count"] == 1013 and state["reasoning"] and state["graph_enabled"]
budgets = state["runtime_snapshot"]["execution_budgets"]
assert budgets["turn_timeout"] == 3660 and budgets["qa_concurrency"] == 4
assert state["status"] == "running"
with urllib.request.urlopen("http://127.0.0.1:8872/api/v1/active-work", timeout=15) as handle:
    work = json.load(handle)["work"]
assert any(row["id"] == target.name and row["worker_alive"] for row in work)
result = {
    "at": time.time(),
    "run_id": target.name,
    "scope_id": state["scope_id"],
    "inherited_answers": matched,
    "source_sha256": before.hexdigest(),
    "inherited_sha256": after.hexdigest(),
    "source_pointer_unchanged": True,
    "status": state["status"],
    "current_phase": state["current_phase"],
    "items": state["item_count"],
    "summary": state.get("summary"),
    "active": work,
}
(HERE / "resume-verification.json").write_text(json.dumps(result, indent=2))
print(json.dumps({key: value for key, value in result.items() if key != "summary"}))
