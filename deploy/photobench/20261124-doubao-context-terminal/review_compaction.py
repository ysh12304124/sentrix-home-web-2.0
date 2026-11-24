"""Distinguish recovered context guards from the final transport failure."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from accept_context import DB, HERE, RUN, save


def main():
    accepted = json.loads((HERE / "terminal-acceptance.json").read_text())
    assert accepted["accepted"]
    reviews = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        for turn in accepted["all_new_turns"]:
            trace = json.loads(
                db.execute(
                    "select trace_json from agent_turns where id=?",
                    (turn["id"],),
                ).fetchone()[0]
                or "{}"
            )
            fields = ("call_id", "attempt", "status", "error_type", "error", "duration_ms")
            calls = [{k: call[k] for k in fields if k in call} for call in trace["model_calls"]]
            contexts = [
                index for index, call in enumerate(calls) if call.get("error_type") == "PromptTooLongError"
            ]
            recoveries = []
            for index in contexts:
                following = next(
                    (call for call in calls[index + 1 :] if call.get("error_type") != "PromptTooLongError"),
                    None,
                )
                recoveries.append(
                    {"context_call": calls[index]["call_id"], "next_non_context_call": following}
                )
            reviews.append(
                {
                    "qa_id": turn["qa_id"],
                    "turn_id": turn["id"],
                    "calls": calls,
                    "call_status_counts": dict(Counter(call.get("status") for call in calls)),
                    "context_guard_count": len(contexts),
                    "context_recovery_followups": recoveries,
                    "final_call": calls[-1],
                }
            )
    result = {
        "run_id": RUN,
        "reviews": reviews,
        "read_only": True,
        "agent_calls": 0,
        "judge_calls": 0,
        "interpretation": (
            "PromptTooLongError triggers the existing local compaction loop. "
            "Evaluate following calls before classifying it as an unrecovered failure."
        ),
    }
    save("compaction-review.json", result)
    print(
        json.dumps(
            {
                "run_id": RUN,
                "reviews": [
                    {
                        k: row[k]
                        for k in (
                            "qa_id",
                            "call_status_counts",
                            "context_guard_count",
                            "final_call",
                        )
                    }
                    for row in reviews
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
