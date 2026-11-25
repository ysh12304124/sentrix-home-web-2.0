"""Read actual per-turn admission events for the active bounded continuations."""

import json
import sqlite3
import time
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
DATA = {
    "q4km": Path("/home/realmagic/xy/atm-bench-20261110/product-data"),
    "qwen37": Path("/var/tmp/sentrix-atm-doubao-20261112/product-data"),
}
result = {"observed_at": time.time(), "stages": {}}
mapping = json.loads((BASE / "active-runs.json").read_text())
for stage, data in DATA.items():
    scope = mapping[stage]["scope_id"]
    with sqlite3.connect(f"file:{data}/sentrix.db?mode=ro", uri=True, timeout=5) as db:
        db.execute("pragma query_only=on")
        turns = []
        for turn_id, status, reason, created in db.execute(
            "select id,status,termination_reason,created_at from agent_turns "
            "where scope_id=? order by created_at desc limit 8",
            (scope,),
        ):
            budget = None
            sequence = 0
            for event_sequence, raw in db.execute(
                "select event_seq,payload_json from agent_turn_events_v2 "
                "where turn_id=? order by event_seq desc limit 40",
                (turn_id,),
            ):
                event = json.loads(raw)
                if event.get("kind") == "request_budget":
                    budget = event
                    sequence = event_sequence
                    break
            turns.append(
                {
                    "turn_id": turn_id,
                    "status": status,
                    "termination_reason": reason,
                    "created_at": created,
                    "budget_event": budget,
                    "event_seq": sequence,
                }
            )
        result["stages"][stage] = {"run_id": mapping[stage]["run_id"], "turns": turns}
print(json.dumps(result, indent=2))
