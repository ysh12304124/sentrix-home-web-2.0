"""Read compact exact-run, live-turn and resource evidence for the Doubao run."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from activate_run import BASE, DATA, EVALUATOR, HERE, LIMITS, PRODUCT, SCOPE, environment, pid

sys.path.insert(0, str(BASE))
from probe import probe


def main():
    result = probe("doubao")
    scoring = (result.pop("summary", {}) or {}).get("selected_scoring", {})
    result["scoring"] = {k: scoring.get(k) for k in (
        "batch_id", "rule_version", "qa_definition_sha256", "total", "scored", "failed", "status", "scored_mean"
    )}
    activation = json.loads((HERE / "activate-response.json").read_text())
    processes = {unit: pid(unit) for unit in (PRODUCT, EVALUATOR)}
    result["service_pids"] = processes
    result["service_pids_match"] = processes == activation["pids"]
    env = environment(processes[PRODUCT])
    result["product_limits"] = {k: int(env["SENTRIX_" + k.upper()]) for k in LIMITS}
    result["product_limits_match"] = result["product_limits"] == LIMITS
    result["product_database_match"] = env["SENTRIX_DATA_DIR"] == str(DATA)
    result["reasoning_context_tokens"] = int(env["SENTRIX_VLM_REASONING_CONTEXT_TOKENS"])
    result["process_rss_kib"] = {}
    for unit, process in processes.items():
        fields = dict(line.split(":", 1) for line in Path(f"/proc/{process}/status").read_text().splitlines() if ":" in line)
        result["process_rss_kib"][unit] = int(fields["VmRSS"].split()[0])
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    result["memory_available_kib"] = int(memory["MemAvailable"].split()[0])
    with sqlite3.connect("file:" + str(DATA / "sentrix.db") + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        result["live_turns"] = [dict(row) for row in db.execute(
            "select t.id,t.status,t.updated_at,(select max(e.event_seq) from agent_turn_events_v2 e "
            "where e.turn_id=t.id) as event_seq from agent_turns t "
            "where scope_id=? and status in ('pending','running')", (SCOPE,)
        )]
        result["recent_budget_outcomes"] = [
            {**dict(row), "budget": json.loads(row["budget"]) if row["budget"] else None}
            for row in db.execute(
                "select id,status,termination_reason,json_extract(trace_json,'$.budget') as budget "
                "from agent_turns where scope_id=? and julianday(created_at)>=julianday(?) "
                "order by created_at desc limit 8", (SCOPE, result["started_at"])
            )
        ]
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
