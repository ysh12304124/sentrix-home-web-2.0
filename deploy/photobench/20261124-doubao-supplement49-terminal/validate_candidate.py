"""Replay three saved failing contexts with the committed candidate, without model requests."""

import importlib.util
import json
import sqlite3
import sys

from inspect_terminal import DB, HERE, current, save

BASELINE = "647db108f9348df9feced5d6d98e97ae338d9ec51d437018856cb206e3a0ec24"
PRODUCT = HERE.parent / "conversation-delete-9363d4b-v2/product/apps/api/src"
sys.path.insert(0, str(PRODUCT))
from sentrix.domain.assistant.models import ChatMessage, ChatRole, ToolCall  # noqa: E402
from sentrix.infrastructure.assistant.chat_client import (  # noqa: E402
    TOKENS_PER_CHAR,
    TOOLS_TOKENS_PER_CHAR,
)

import hashlib  # noqa: E402

reader, state, _ = current()
accepted = json.loads((HERE / "terminal-acceptance.json").read_text())
assert reader.revision == accepted["read_revision"]
assert (
    hashlib.sha256((PRODUCT / "sentrix/application/assistant/run_turn.py").read_bytes()).hexdigest()
    == BASELINE
)
candidate = HERE / "candidate-run_turn.py"
spec = importlib.util.spec_from_file_location("compaction_candidate", candidate)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
qa_ids = {
    "atm-1549c95e-6cfe-484a-96e2-535835bc38b1",
    "atm-230bf920-cc9f-46c9-a77a-f0ce976e5bb7",
    "atm-5703b8c1-2e4f-4a5e-9385-44643f5a33c5",
}
output = []
with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
    for turn in accepted["all_new_turns"]:
        if turn["qa_id"] not in qa_ids:
            continue
        trace = json.loads(
            db.execute("select trace_json from agent_turns where id=?", (turn["id"],)).fetchone()[0]
        )
        debug = trace["debug_calls"][-1]
        messages = [
            ChatMessage(
                role=ChatRole(m["role"]),
                content=m.get("content") or "",
                tool_call_id=m.get("tool_call_id") or "",
                tool_calls=tuple(
                    ToolCall(c["id"], c["name"], c.get("arguments") or "{}") for c in m.get("tool_calls", [])
                ),
            )
            for m in debug["messages"]
        ]
        newest = next(m for m in reversed(messages) if m.role is ChatRole.TOOL)
        result = module._drop_oldest_tool_round_trip(messages)
        assert result is not None and result[:2] == messages[:2] and newest in result
        assert {c.call_id for m in result for c in m.tool_calls} == {
            m.tool_call_id for m in result if m.role is ChatRole.TOOL
        }
        tokens = int(
            sum(len(m.content) for m in result) * TOKENS_PER_CHAR
            + len(json.dumps(debug["tools"], ensure_ascii=False)) * TOOLS_TOKENS_PER_CHAR
        )
        assert tokens + 256 + 128 <= 16384
        output.append(
            {
                "qa_id": turn["qa_id"],
                "estimated_tokens": tokens,
                "latest_preview_unchanged": True,
                "tool_pairs_match": True,
            }
        )
assert {r["qa_id"] for r in output} == qa_ids
result = {
    "accepted": True,
    "read_revision": reader.revision,
    "replays": output,
    "baseline_product_unchanged": True,
    "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
    "agent_calls": 0,
    "judge_calls": 0,
    "business_deployment": False,
}
save("candidate-validation.json", result)
print(json.dumps(result), flush=True)
