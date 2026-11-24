"""Read compact model-error evidence for the three accepted product turns."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from accept_context import DB, HERE, RUN, current, fingerprint, save


def errors(value, path=""):
    if isinstance(value, dict):
        if value.get("error_type") or (value.get("status") == "error" and value.get("call_id")):
            keys = (
                "call_id",
                "model",
                "stage",
                "kind",
                "attempt",
                "status",
                "error_type",
                "error",
                "duration_ms",
                "input_tokens",
                "prompt_tokens",
                "completion_tokens",
                "context_tokens",
            )
            yield {"path": path, **{k: value[k] for k in keys if k in value}}
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from errors(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from errors(child, path + "/" + str(index))


def main():
    accepted = json.loads((HERE / "terminal-acceptance.json").read_text())
    reader, state, _ = current()
    assert accepted["accepted"] and reader.revision == accepted["read_revision"]
    assert fingerprint(state["items"]) == accepted["all_items_reference_sha256"]
    assert fingerprint(state["scoring_results"]) == accepted["all_scoring_receipts_sha256"]
    reviews = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        for turn in accepted["all_new_turns"]:
            kinds, event_errors = Counter(), []
            for seq, raw in db.execute(
                "select event_seq,payload_json from agent_turn_events_v2 where turn_id=? order by event_seq",
                (turn["id"],),
            ):
                event = json.loads(raw)
                kinds[event.get("kind")] += 1
                event_errors.extend({"event_seq": seq, **error} for error in errors(event))
            trace = json.loads(
                db.execute(
                    "select trace_json from agent_turns where id=?",
                    (turn["id"],),
                ).fetchone()[0]
                or "{}"
            )
            trace_errors = list(errors(trace.get("model_calls", []), "/model_calls"))
            reviews.append(
                {
                    "qa_id": turn["qa_id"],
                    "turn_id": turn["id"],
                    "status": turn["status"],
                    "termination_reason": turn["termination_reason"],
                    "updated_at": turn["updated_at"],
                    "event_kind_counts": dict(kinds),
                    "event_errors": event_errors,
                    "trace_model_errors": trace_errors,
                }
            )
    result = {
        "run_id": RUN,
        "read_revision": reader.revision,
        "reviews": reviews,
        "read_only": True,
        "agent_calls": 0,
        "judge_calls": 0,
        "answers_and_scores_unchanged": True,
    }
    save("error-review.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
