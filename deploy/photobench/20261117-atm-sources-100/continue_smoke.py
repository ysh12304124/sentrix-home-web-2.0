"""继续已完成邮件答卷的导出核验与三道Hard题，不重跑邮件题。"""

import json, time
from smoke import BASE, HTTP, request, save, start_run

assert not (BASE / "continue-smoke-intent.json").exists()
first = json.loads((BASE / "smoke-atm-bench-email-result.json").read_text())
first_run, email_items = first["run"], first["items"]
assert first_run["status"] == "completed"
assert len(email_items) == 2 and all(item.get("predicted_source_refs") for item in email_items)
for export in ["export-trace", "export-sft"]:
    response = HTTP.get(f"http://127.0.0.1:8874/api/v1/runs/{first_run['run_id']}/{export}")
    response.raise_for_status()
    assert "source_metrics" in response.text
    save("smoke-atm-bench-email-" + export + ".json", response.json())
save("continue-smoke-intent.json", {"started_at": time.time(), "inherited_run": first_run["run_id"]})
report = json.loads((BASE / "smoke-api.json").read_text())
second_run, hard_items = start_run("atm-bench-hard", first_run["scope_id"])
report["runs"] = [
    {
        "run_id": run["run_id"],
        "status": run["status"],
        "questions": len(items),
        "answers": [
            {
                "qa_id": item["qa_id"],
                "answer": item.get("answer"),
                "citations": item.get("predicted_source_refs"),
                "source_metrics": item.get("source_metrics"),
            }
            for item in items
        ],
    }
    for run, items in [(first_run, email_items), (second_run, hard_items)]
]
report["status"] = "passed"
save("smoke-acceptance.json", report)
print(
    json.dumps({"status": "passed", "runs": [run["run_id"] for run in [first_run, second_run]]}),
    flush=True,
)
