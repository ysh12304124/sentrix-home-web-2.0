"""Collect bounded evidence for 16 timeout QA; never invoke Agent or Judge."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from retry_transport import DB, HERE, SOURCE, c, fingerprint, save, source


def main():
    assert not (HERE / "sixteen-evidence.json").exists()
    state, _ = source()
    before = fingerprint(state["items"]), fingerprint(state["scoring_results"])
    targets = set(c.read("authorized-plan.json")["unmodified_timeout_qa_ids_counted_as_incorrect"])
    items = {i["qa_id"]: i for i in state["items"]}
    records = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for qa in state["qa_snapshot"]["rows"]:
            if qa["qa_id"] not in targets:
                continue
            qid, assets = qa["qa_id"], []
            refs = qa.get("answer_evidence_media_refs", [])
            for ref in refs:
                for row in db.execute(
                    "select id,file_name,path,captured_at,content_sha256 "
                    "from assets where scope_id=? and file_name=?",
                    (state["scope_id"], ref["media_id"]),
                ):
                    asset = dict(row)
                    asset["observations"] = [
                        dict(r)
                        for r in db.execute("select * from observations where asset_id=?", (asset["id"],))
                    ]
                    assets.append(asset)
            runtime = items[qid].get("runtime_turns") or []
            tids = [t["turn_id"] for t in runtime if t.get("turn_id")]
            turns = []
            for tid in tids:
                row = db.execute(
                    "select id,status,termination_reason,answer,trace_json,created_at,updated_at "
                    "from agent_turns where id=?",
                    (tid,),
                ).fetchone()
                if not row:
                    continue
                turn = dict(row)
                trace = json.loads(turn.pop("trace_json") or "{}")
                observations = trace.get("retrieval_observations") or {}
                calls = trace.get("model_calls") or []
                turn["budget"] = trace.get("budget")
                turn["retrieval_observations"] = observations
                turn["model_call_statuses"] = dict(Counter(str(x.get("status")) for x in calls))
                events = []
                for seq, raw in db.execute(
                    "select event_seq,payload_json from agent_turn_events_v2 "
                    "where turn_id=? order by event_seq",
                    (tid,),
                ):
                    event = json.loads(raw)
                    if event.get("kind") in {"tool_call", "tool_timing", "request_budget", "answer", "done"}:
                        events.append({"event_seq": seq, **event})
                turn["selected_events"] = events
                turns.append(turn)
            records.append(
                {
                    "qa_id": qid,
                    "qa_definition": qa,
                    "evidence_assets": assets,
                    "item": {
                        k: items[qid].get(k)
                        for k in (
                            "execution_status",
                            "termination_reason",
                            "error",
                            "answer",
                            "runtime_turns",
                        )
                    },
                    "recorded_turns": turns,
                }
            )
    assert len(records) == 16
    state2, _ = source()
    assert before == (fingerprint(state2["items"]), fingerprint(state2["scoring_results"]))
    save(
        "sixteen-evidence.json",
        {"source_run_id": SOURCE, "records": records, "agent_calls": 0, "judge_calls": 0, "read_only": True},
    )
    print(
        json.dumps(
            {
                "qa_count": len(records),
                "evidence_assets": sum(len(r["evidence_assets"]) for r in records),
                "output": str(HERE / "sixteen-evidence.json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
