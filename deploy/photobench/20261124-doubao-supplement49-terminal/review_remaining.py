"""Read the remaining failures and strawberry QA without changing results or ground truth."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from inspect_terminal import DB, HERE, RUN, current, fingerprint, save


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def main():
    accepted = json.loads((HERE / "terminal-acceptance.json").read_text())
    reader, state, _ = current()
    assert accepted["accepted"] and reader.revision == accepted["read_revision"]
    assert fingerprint(state["items"]) == accepted["all_items_reference_sha256"]
    rows = state["qa_snapshot"]["rows"]
    strawberry = [
        r
        for r in rows
        if re.search(r"草莓|奶昔|strawberry|milkshake", json.dumps(r, ensure_ascii=False), re.I)
    ]
    failed = set(accepted["execution_failures"])
    selected = failed | {r["qa_id"] for r in strawberry}
    items = {i["qa_id"]: i for i in state["items"]}
    output = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for qa in sorted(selected):
            item = items[qa]
            turns = []
            for row in db.execute(
                "select t.id,t.status,t.termination_reason,t.attempt_no,t.answer,t.trace_json,"
                "t.created_at,t.updated_at from agent_turns t join agent_conversations c "
                "on c.id=t.conversation_id where t.scope_id=? and c.title=? order by t.created_at",
                (state["scope_id"], qa),
            ):
                turn = dict(row)
                trace = json.loads(turn.pop("trace_json") or "{}")
                turn["trace_keys"] = list(trace)
                turn["model_calls"] = trace.get("model_calls", [])
                turn["budget"] = trace.get("budget")
                turn["trace_summary"] = {
                    k: v
                    for k, v in trace.items()
                    if k not in {"model_calls", "events", "messages", "tools", "tool_calls", "debug"}
                }
                events = [
                    json.loads(r[0])
                    for r in db.execute(
                        "select payload_json from agent_turn_events_v2 where turn_id=? order by event_seq",
                        (turn["id"],),
                    )
                ]
                turn["event_kind_counts"] = dict(Counter(e.get("kind") for e in events))
                turn["model_errors"] = [
                    n
                    for n in nodes(events)
                    if n.get("error_type") or (n.get("status") == "error" and n.get("call_id"))
                ]
                turn["retrieval_backends"] = [
                    {k: n.get(k) for k in ("backend", "fallback_reason", "reason", "duration_ms")}
                    for n in nodes(events)
                    if n.get("backend") in {"sqlite_exact", "qdrant"}
                ]
                turn["tool_events"] = [
                    e
                    for e in events
                    if e.get("kind") in {"tool_call", "tool_result", "tool_timing", "request_budget"}
                ]
                turn["event_sample"] = events[:2] + events[-3:]
                turns.append(turn)
            output.append(
                {
                    "qa_id": qa,
                    "is_execution_failure": qa in failed,
                    "qa_definition": next(r for r in rows if r["qa_id"] == qa),
                    "item_keys": list(item),
                    "item_summary": {
                        k: item.get(k)
                        for k in (
                            "answer",
                            "execution_status",
                            "termination_reason",
                            "error",
                            "runtime_turns",
                            "attachments",
                            "retrieved_candidates",
                        )
                    },
                    "scores": [v for v in state["scoring_results"].values() if v["qa_id"] == qa],
                    "product_turns": turns,
                }
            )
    result = {
        "run_id": RUN,
        "read_revision": reader.revision,
        "failure_count": len(failed),
        "strawberry_definitions": strawberry,
        "reviews": output,
        "read_only": True,
        "agent_calls": 0,
        "judge_calls": 0,
    }
    save("remaining-review.json", result)
    print(
        json.dumps(
            {
                "failures": len(failed),
                "strawberry": strawberry,
                "reviews": [
                    {
                        "qa_id": r["qa_id"],
                        "turns": [
                            {
                                "id": t["id"],
                                "trace_keys": t["trace_keys"],
                                "event_kind_counts": t["event_kind_counts"],
                                "last_model_error": t["model_errors"][-1:],
                            }
                            for t in r["product_turns"][-1:]
                        ],
                    }
                    for r in output
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
