"""上下文压缩的取舍：**最新一次检索/翻页的六图 preview 不能被丢**。

模型回答任何照片问题都依赖那一轮的 ``preview``（handle、时间、地点、人物、摘要）。
丢了它，模型只知道「有结果集」却看不见候选，只能猜；比少一轮精读贵得多。
"""

from __future__ import annotations

from sentrix.application.assistant.run_turn import (
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


def test_parallel_facts_do_not_prevent_retaining_only_the_latest_preview():
    preview = '{"preview":[{"handle":"photo_1","file_name":"ticket.jpg"}]}'
    original = messages(
        [
            assistant("parallel", "query_memory_facts", "search_memories"),
            tool("parallel-0", "x" * 320431),
            tool("parallel-1", preview),
        ]
    )
    result = _drop_oldest_tool_round_trip(original)
    assert result is not None
    assert result[:2] == original[:2]
    assert result[2].tool_calls == (original[2].tool_calls[1],)
    assert result[3] == original[4]
    assert result[3].content == preview
    assert _drop_oldest_tool_round_trip(result) is None
    assert len(original[3].content) == 320431


def test_parallel_searches_keep_the_last_returned_preview_and_its_call():
    original = messages(
        [
            assistant("parallel", *("search_memories" for _ in range(7))),
            *(tool(f"parallel-{i}", f"preview-{i}") for i in range(7)),
        ]
    )
    result = _drop_oldest_tool_round_trip(original)
    assert result is not None
    assert result[2].tool_calls == (original[2].tool_calls[-1],)
    assert result[3:] == [original[-1]]
    assert result[3].tool_call_id == result[2].tool_calls[0].call_id


def test_latest_preview_uses_response_order_and_preserves_following_history():
    parallel = [
        assistant("parallel", "search_memories", "get_result_page"),
        tool("parallel-1", "page returned first"),
        tool("parallel-0", "search returned last"),
    ]
    original = messages(parallel, INSPECT)
    result = _drop_oldest_tool_round_trip(original)
    assert result is not None
    assert result[2].tool_calls == (original[2].tool_calls[0],)
    assert result[3] == original[4]
    assert result[4:] == original[5:]
