"""Summarize retained failure evidence and replay context compaction without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from sentrix.application.assistant.run_turn import _drop_oldest_tool_round_trip
from sentrix.domain.assistant.models import ChatMessage, ChatRole, ToolCall
from sentrix.infrastructure.assistant.chat_client import TOKENS_PER_CHAR, TOOLS_TOKENS_PER_CHAR


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def main(audit):
    reviewed = json.loads((audit / "remaining-review.json").read_text())
    rows, replays, strawberry = [], [], []
    for record in reviewed["reviews"]:
        turn = record["product_turns"][-1]
        calls = turn["model_calls"]
        errors = [c for c in calls if c.get("status") == "error" and not c.get("recovered_by_call_id")]
        if not record["is_execution_failure"]:
            strawberry.append(
                {
                    "qa": record["qa_definition"],
                    "answer": record["item_summary"]["answer"],
                    "scoring": [
                        {k: s.get(k) for k in ("key", "score", "status", "reason", "checks")}
                        for s in record["scores"]
                    ],
                }
            )
            continue
        context = bool(errors and errors[-1].get("error_type") == "PromptTooLongError")
        row = {
            "qa_id": record["qa_id"],
            "question": record["qa_definition"]["question"],
            "turn_id": turn["id"],
            "product_status": turn["status"],
            "recorded_termination_reason": turn["termination_reason"],
            "category": "context_over_limit" if context else "one_hour_limit",
            "fatal_model_error": errors[-1:]
            if context or turn["termination_reason"] == "model_error"
            else [],
            "budget": turn["budget"],
            "created_at": turn["created_at"],
            "updated_at": turn["updated_at"],
            "retrieval_observations": turn["trace_summary"].get("retrieval_observations"),
        }
        rows.append(row)
        if not context:
            continue
        debug = turn["trace_summary"]["debug_calls"][-1]
        raw = debug["messages"]
        messages = [
            ChatMessage(
                role=ChatRole(m["role"]),
                content=m.get("content") or "",
                tool_call_id=m.get("tool_call_id") or "",
                tool_calls=tuple(
                    ToolCall(call_id=c["id"], name=c["name"], arguments_json=c.get("arguments") or "{}")
                    for c in m.get("tool_calls", [])
                ),
            )
            for m in raw
        ]
        before = sum(len(m.content) for m in messages)
        latest_tool = next(m for m in reversed(messages) if m.role is ChatRole.TOOL)
        result = _drop_oldest_tool_round_trip(messages)
        assert result is not None
        assert latest_tool in result and result[:2] == messages[:2]
        expected = {c.call_id for m in result for c in m.tool_calls}
        actual = {m.tool_call_id for m in result if m.role is ChatRole.TOOL}
        assert expected == actual
        schema_tokens = len(json.dumps(debug["tools"], ensure_ascii=False)) * TOOLS_TOKENS_PER_CHAR
        after = sum(len(m.content) for m in result)
        estimate = int(after * TOKENS_PER_CHAR + schema_tokens)
        assert estimate + 256 + 128 <= 16384
        replays.append(
            {
                "qa_id": record["qa_id"],
                "call_id": debug["call_id"],
                "original_model_error": errors[-1]["error"],
                "before_content_chars": before,
                "after_content_chars": after,
                "estimated_prompt_tokens_including_tools": estimate,
                "context_tokens": 16384,
                "latest_preview_unchanged": True,
                "latest_preview_sha256": hashlib.sha256(latest_tool.content.encode()).hexdigest(),
                "tool_call_pairs_match": expected == actual,
                "remaining_tool_calls": [c.name for m in result for c in m.tool_calls],
            }
        )
    result = {
        "run_id": reviewed["run_id"],
        "read_revision": reviewed["read_revision"],
        "failure_count": len(rows),
        "categories": dict(Counter(r["category"] for r in rows)),
        "failures": rows,
        "context_replays": replays,
        "strawberry_cases": strawberry,
        "agent_calls": 0,
        "judge_calls": 0,
        "result_or_gt_writes": 0,
    }
    with (audit / "failure19-review.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"categories": result["categories"], "replays": replays}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("audit", type=Path)
    main(parser.parse_args().audit)
