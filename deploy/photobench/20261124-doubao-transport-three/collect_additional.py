"""Read missing video/email evidence, asset metadata and narrow date context for 16 QA."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from retry_transport import DB, HERE, c, save, source


def main():
    assert not (HERE / "additional-evidence.json").exists()
    state, _ = source()
    targets = set(c.read("authorized-plan.json")["unmodified_timeout_qa_ids_counted_as_incorrect"])
    items = {i["qa_id"]: i for i in state["items"]}
    scope = state["scope_id"]
    records = []
    with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row

        def asset(row):
            value = dict(row)
            value["observations"] = [
                dict(r) for r in db.execute("select * from observations where asset_id=?", (value["id"],))
            ]
            return value

        for qa in state["qa_snapshot"]["rows"]:
            qid = qa["qa_id"]
            if qid not in targets:
                continue
            assets, matched = [], set()
            for ref in qa.get("answer_evidence_media_refs", []):
                name = ref["media_id"]
                for row in db.execute(
                    "select * from assets where scope_id=? and (file_name=? or file_name like ?)",
                    (scope, name, name + ".%"),
                ):
                    if row["id"] in matched:
                        continue
                    matched.add(row["id"])
                    value = asset(row)
                    frames = list(
                        db.execute(
                            "select * from assets where parent_asset_id=? order by source_timestamp_sec",
                            (row["id"],),
                        )
                    )
                    if len(frames) > 8:
                        frames = [frames[round(i * (len(frames) - 1) / 7)] for i in range(8)]
                    value["sampled_existing_frames"] = [asset(frame) for frame in frames]
                    assets.append(value)
            emails = []
            for ref in qa.get("answer_evidence_source_refs", []):
                for row in db.execute(
                    "select * from assets where scope_id=? and "
                    "(external_source_id=? or external_source_id like ? or file_name like ?)",
                    (scope, ref["source_id"], "%" + ref["source_id"] + "%", "%" + ref["source_id"] + "%"),
                ):
                    emails.append(asset(row))
            turns = []
            runtime_ids = [t["turn_id"] for t in items[qid].get("runtime_turns") or [] if t.get("turn_id")]
            # A timeout can lack a saved runtime_turn; use the last matching original product turn.
            if not runtime_ids:
                runtime_ids = [
                    r[0]
                    for r in db.execute(
                        "select t.id from agent_turns t join agent_conversations c on c.id=t.conversation_id "
                        "where t.scope_id=? and c.title=? order by t.created_at desc limit 1",
                        (scope, qid),
                    )
                ]
            for tid in runtime_ids:
                row = db.execute(
                    "select id,status,termination_reason,answer,trace_json from agent_turns where id=?",
                    (tid,),
                ).fetchone()
                trace = json.loads(row["trace_json"] or "{}")
                kinds = Counter()
                samples, facts = {}, []
                for seq, raw in db.execute(
                    "select event_seq,payload_json from agent_turn_events_v2 "
                    "where turn_id=? order by event_seq",
                    (tid,),
                ):
                    event = json.loads(raw)
                    kind = str(event.get("kind"))
                    kinds[kind] += 1
                    if kind not in samples:
                        samples[kind] = {
                            "event_seq": seq,
                            "keys": list(event),
                            "payload_keys": list(event.get("payload") or {}),
                            "text": str(event.get("text") or "")[:600],
                        }
                    payload = event.get("payload") or {}
                    if any(k in payload for k in ("backend", "fallback_reason", "duration_ms", "tool_name")):
                        facts.append(
                            {
                                "event_seq": seq,
                                "kind": kind,
                                "payload": {
                                    k: v
                                    for k, v in payload.items()
                                    if not isinstance(v, (list, dict)) and len(str(v)) < 2000
                                },
                            }
                        )
                turns.append(
                    {
                        "turn_id": tid,
                        "status": row["status"],
                        "termination_reason": row["termination_reason"],
                        "answer": row["answer"],
                        "trace_keys": list(trace),
                        "event_kind_counts": dict(kinds),
                        "event_samples": samples,
                        "tool_facts": facts,
                        "budget": trace.get("budget"),
                        "retrieval_observations": trace.get("retrieval_observations"),
                        "tool_calls": [
                            {
                                k: v
                                for k, v in call.items()
                                if k
                                in {
                                    "tool",
                                    "name",
                                    "tool_name",
                                    "arguments",
                                    "duration_ms",
                                    "status",
                                    "error",
                                    "result_set_id",
                                    "call_id",
                                }
                            }
                            for call in trace.get("tool_calls") or []
                        ],
                    }
                )
            records.append({"qa_id": qid, "assets": assets, "email_assets": emails, "turns": turns})
        windows = {
            "june2024": ("2024-06-01", "2024-07-01"),
            "porto": ("2025-02-22", "2025-02-25"),
            "warm_bus": ("2024-11-23", "2024-11-25"),
            "cornwall": ("2023-04-15", "2023-04-17"),
            "photo_wall": ("2023-02-01", "2023-02-22"),
        }
        context = {}
        for name, (start, end) in windows.items():
            context[name] = [
                dict(r)
                for r in db.execute(
                    "select a.id,a.file_name,a.media_type,a.captured_at,a.gps_latitude,a.gps_longitude,"
                    "a.metadata_json,o.caption,o.ocr_text,o.source_text from assets a "
                    "left join observations o on o.asset_id=a.id where a.scope_id=? "
                    "and a.captured_at>=? and a.captured_at<? and a.parent_asset_id is null "
                    "order by a.captured_at",
                    (scope, start, end),
                )
            ]
    save(
        "additional-evidence.json",
        {"records": records, "date_context": context, "read_only": True, "agent_calls": 0, "judge_calls": 0},
    )
    print(
        json.dumps(
            {"records": len(records), "context_counts": {k: len(v) for k, v in context.items()}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
