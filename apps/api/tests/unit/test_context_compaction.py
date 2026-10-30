"""上下文压缩的取舍：**最新一次检索/翻页的六图 preview 不能被丢**。

模型回答任何照片问题都依赖那一轮的 ``preview``（handle、时间、地点、人物、摘要）。
丢了它，模型只知道「有结果集」却看不见候选，只能猜；比少一轮精读贵得多。
"""

from __future__ import annotations

import json

from sentrix.application.assistant.run_turn import (
    _compact_latest_preview_round_trip,
    _drop_oldest_tool_round_trip,
    _round_trip_spans,
)
from sentrix.domain.assistant.models import ChatMessage, ChatRole, ToolCall


def assistant(call_id: str, *names: str) -> ChatMessage:
    return ChatMessage(
        role=ChatRole.ASSISTANT,
        tool_calls=tuple(
            ToolCall(call_id=f"{call_id}-{index}", name=name) for index, name in enumerate(names)
        ),
    )


def tool(call_id: str, content: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.TOOL, content=content, tool_call_id=call_id)


def messages(*rounds: list[ChatMessage]) -> list[ChatMessage]:
    base = [ChatMessage(ChatRole.SYSTEM, "系统"), ChatMessage(ChatRole.USER, "问题")]
    for round_ in rounds:
        base.extend(round_)
    return base


SEARCH = [assistant("s1", "search_memories"), tool("s1-0", '{"query":"海边","preview":["p1"]}')]
PAGE = [assistant("p2", "get_result_page"), tool("p2-0", '{"page":2,"preview":["p7"]}')]
INSPECT = [assistant("i3", "inspect_photo"), tool("i3-0", '{"detail":"红色上衣"}')]


def test_round_trip_spans_cover_assistant_plus_tool_results():
    assert _round_trip_spans(messages(SEARCH, PAGE)) == [(2, 4), (4, 6)]


def test_drops_oldest_round_but_keeps_latest_preview():
    result = _drop_oldest_tool_round_trip(messages(SEARCH, PAGE))
    assert result is not None
    contents = [message.content for message in result]
    assert '{"page":2,"preview":["p7"]}' in contents
    assert '{"query":"海边","preview":["p1"]}' not in contents


def test_keeps_the_latest_of_search_and_page_when_both_exist():
    result = _drop_oldest_tool_round_trip(messages(SEARCH, PAGE, INSPECT))
    assert result is not None
    contents = [message.content for message in result]
    # 最新的一份六图集是 PAGE；更老的 SEARCH 可以丢，新的 INSPECT 也保留。
    assert '{"page":2,"preview":["p7"]}' in contents
    assert '{"detail":"红色上衣"}' in contents
    assert '{"query":"海边","preview":["p1"]}' not in contents


def test_drops_older_inspect_before_a_newer_search_preview():
    result = _drop_oldest_tool_round_trip(messages(INSPECT, SEARCH))
    assert result is not None
    contents = [message.content for message in result]
    assert '{"query":"海边","preview":["p1"]}' in contents
    assert '{"detail":"红色上衣"}' not in contents


def test_returns_none_when_only_the_protected_preview_remains():
    assert _drop_oldest_tool_round_trip(messages(SEARCH)) is None
    assert _drop_oldest_tool_round_trip(messages()) is None


def test_dropped_history_keeps_tool_call_id_pairing():
    result = _drop_oldest_tool_round_trip(messages(SEARCH, PAGE))
    assert result is not None
    expected = {
        call.call_id
        for message in result
        if message.role is ChatRole.ASSISTANT
        for call in message.tool_calls
    }
    actual = {message.tool_call_id for message in result if message.role is ChatRole.TOOL}
    assert actual == expected


def test_compacts_latest_preview_and_preserves_handles_and_transcript():
    payload = {"group_photo_rows":[{"handle":"p1","group_size":3}],"preview":[
        {"handle":"p1","captured_at":"2026-10-01T10:00:00","scene_hint":"office meeting",
         "evidence_summary":"An office meeting; office meeting","level":"high","people":[],
         "speech_evidence":[{"start_sec":1.0,"end_sec":4.0,"text":"We are glad you joined us.","speaker":""}]}]}
    original = messages([assistant("s1","search_memories"),tool("s1-0",json.dumps(payload))])
    result = _compact_latest_preview_round_trip(original)
    assert result is not None
    compacted = json.loads(result[-1].content)
    row = compacted["preview"][0]
    assert row["handle"] == "p1"
    assert row["captured_at"] == "2026-10-01T10:00:00"
    assert row["speech_evidence"][0]["text"] == "We are glad you joined us."
    assert "scene_hint" not in row and "level" not in row and "people" not in row
    assert "group_photo_rows" not in compacted
    assert original[-1].content == json.dumps(payload)


def test_progressively_shortens_visual_summary_without_truncating_speech():
    payload = {"preview":[{"handle":"p1","evidence_summary":"visual " * 100,
        "speech_evidence":[{"start_sec":2,"end_sec":8,"text":"exact transcript"}]}]}
    current = messages([assistant("s1","search_memories"),tool("s1-0",json.dumps(payload))])
    first = _compact_latest_preview_round_trip(current)
    second = _compact_latest_preview_round_trip(first or [])
    assert first is not None and second is not None
    row = json.loads(second[-1].content)["preview"][0]
    assert len(row["evidence_summary"]) < 450
    assert row["speech_evidence"][0]["text"] == "exact transcript"
