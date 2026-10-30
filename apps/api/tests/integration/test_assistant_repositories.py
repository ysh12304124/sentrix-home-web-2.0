"""真 SQLite + 真迁移文件下的助手仓储测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sentrix.domain.assistant.models import (
    Attachment,
    ChatRole,
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    TurnRecord,
    TurnStatus,
)
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.media.transcription import AudioEvidenceExcerpt
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository
from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


@pytest.fixture
def conversations(migrations_ready_db):
    return SqliteConversationRepository(migrations_ready_db)


@pytest.fixture
def turns(migrations_ready_db):
    return SqliteTurnRepository(migrations_ready_db)


@pytest.fixture
def result_sets(migrations_ready_db):
    return SqliteResultSetRepository(migrations_ready_db)


# ---- 会话 -------------------------------------------------------------------


def test_a_created_conversation_is_listed_for_its_scope(conversations):
    created = conversations.create(scope_id="album1", title="新对话", created_at=NOW)

    listed = conversations.list_for_scope("album1", 10)

    assert [item.id for item in listed] == [created.id]
    assert conversations.list_for_scope("album2", 10) == []


def test_a_deleted_conversation_disappears_from_list_and_get(conversations):
    """软删对外就是「不存在」——``state = 'active'`` 是查询条件，不是调用方的事后判断。"""
    created = conversations.create(scope_id="album1", title="新对话", created_at=NOW)

    assert conversations.delete(created.id, NOW) is True

    assert conversations.get(created.id) is None
    assert conversations.list_for_scope("album1", 10) == []
    assert conversations.delete(created.id, NOW) is False


def test_renaming_a_missing_conversation_returns_false(conversations):
    assert conversations.rename("conv_nope", "别的名字", NOW) is False


def test_history_is_the_most_recent_messages_in_chronological_order(conversations):
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    for index in range(4):
        conversations.add_message(
            conversation_id=conversation.id,
            scope_id="album1",
            turn_id=f"turn_{index}",
            role=ChatRole.USER.value,
            content=f"第 {index} 句",
            created_at=NOW + timedelta(seconds=index),
        )

    history = conversations.history_for_prompt(conversation.id, 3)

    assert [message.content for message in history] == ["第 1 句", "第 2 句", "第 3 句"]


def test_a_new_message_moves_the_conversation_to_the_top(conversations):
    older = conversations.create(scope_id="album1", title="旧的", created_at=NOW)
    newer = conversations.create(scope_id="album1", title="新的", created_at=NOW + timedelta(hours=1))

    conversations.add_message(
        conversation_id=older.id,
        scope_id="album1",
        turn_id="turn_1",
        role=ChatRole.USER.value,
        content="刚问的",
        created_at=NOW + timedelta(hours=2),
    )

    assert [item.id for item in conversations.list_for_scope("album1", 10)] == [
        older.id,
        newer.id,
    ]


def test_deleting_a_conversation_removes_its_messages_and_turns(conversations, turns):
    """删对话不删记忆，但对话自己的消息与回合必须一起走干净。"""
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    record = TurnRecord(
        id="turn_1",
        conversation_id=conversation.id,
        scope_id="album1",
        question="给我看看海边的照片",
        status=TurnStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    turns.create(record)
    turns.append_event(
        "turn_1",
        ProgressEvent(
            step_index=1,
            stage=ProgressStage.PLANNING,
            status=ProgressStatus.COMPLETE,
            text="已确认需要哪些证据。",
        ),
    )
    conversations.add_message(
        conversation_id=conversation.id,
        scope_id="album1",
        turn_id="turn_1",
        role=ChatRole.USER.value,
        content="给我看看海边的照片",
        created_at=NOW,
    )

    conversations.delete(conversation.id, NOW)

    assert conversations.list_messages(conversation.id, 10) == []
    assert turns.get("turn_1") is None
    assert turns.events_after("turn_1", 0) == []


# ---- 回合 -------------------------------------------------------------------


def make_turn(turn_id: str = "turn_1", *, conversation_id: str = "conv_missing") -> TurnRecord:
    return TurnRecord(
        id=turn_id,
        conversation_id=conversation_id,
        scope_id="album1",
        question="给我看看在海边的照片",
        status=TurnStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def test_claim_flips_pending_to_running_exactly_once(turns, conversations):
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    turns.create(make_turn("turn_1", conversation_id=conversation.id))

    assert turns.claim("turn_1", NOW) is True
    assert turns.claim("turn_1", NOW) is False
    assert turns.get("turn_1").status is TurnStatus.RUNNING


def test_events_are_replayed_by_step_index(turns, conversations):
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    turns.create(make_turn("turn_1", conversation_id=conversation.id))
    for index in range(1, 4):
        turns.append_event(
            "turn_1",
            ProgressEvent(
                step_index=index,
                stage=ProgressStage.THINKING,
                status=ProgressStatus.RUNNING,
                text=f"第 {index} 步",
                payload={"n": index},
            ),
        )

    replay = turns.events_after("turn_1", 1)

    assert [event.step_index for event in replay] == [2, 3]
    assert replay[0].text == "第 2 步"
    assert replay[0].payload == {"n": 2}
    assert replay[0].stage is ProgressStage.THINKING


def test_finishing_a_turn_records_the_reason_answer_and_attachments(turns, conversations):
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    turns.create(make_turn("turn_1", conversation_id=conversation.id))

    turns.finish(
        "turn_1",
        status=TurnStatus.COMPLETE.value,
        termination_reason="answered",
        answer="这是你要的海边照片。",
        attachments=(Attachment(handle="photo_1", asset_id="asset_1", file_name="a.jpg"),),
        requirements=({"id": "photo_set", "evidence_type": "photo_set", "status": "satisfied"},),
        retrieved_candidates=("asset_1", "asset_2", "asset_3"),
        trace={"budget": {"model_steps": 3}},
        updated_at=NOW,
    )

    record = turns.get("turn_1")
    assert record.status is TurnStatus.COMPLETE
    assert record.termination_reason == "answered"
    assert record.answer == "这是你要的海边照片。"
    assert record.attachments[0].asset_id == "asset_1"
    assert record.requirements[0]["status"] == "satisfied"
    # **候选集与交付集是两份数据**：交付了 1 张，检索给了 3 张。合成一格就再也分不出
    # 「检索没找到」与「找到了没交付」。
    assert record.retrieved_candidates == ("asset_1", "asset_2", "asset_3")
    # 回合级事实也往返：它不是事件流，是收尾时一次性写下的一格。
    assert record.trace == {"budget": {"model_steps": 3}}


def test_a_turn_without_retrieval_records_no_candidates(turns, conversations):
    """反向：没检索过就是**空元组**，不是某个默认值。

    拿交付集冒充候选集，正是这条字段要修掉的那个错——所以「没检索」必须读得出是空。
    """
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    turns.create(make_turn("turn_1", conversation_id=conversation.id))

    turns.finish(
        "turn_1",
        status=TurnStatus.COMPLETE.value,
        termination_reason="answered",
        answer="不知道。",
        attachments=(),
        requirements=(),
        retrieved_candidates=(),
        trace={},
        updated_at=NOW,
    )

    assert turns.get("turn_1").retrieved_candidates == ()


def test_a_pending_turn_has_no_termination_reason_yet(turns, conversations):
    """``TurnRecord.termination_reason`` 是空串表示「还没结束」——与
    ``TurnResult.termination_reason`` 的非可选枚举是两回事，后者只在收尾时构造。"""
    conversation = conversations.create(scope_id="album1", title="对话", created_at=NOW)
    turns.create(make_turn("turn_1", conversation_id=conversation.id))

    assert turns.get("turn_1").termination_reason == ""


# ---- 结果集 -----------------------------------------------------------------


def test_a_result_set_survives_a_round_trip(result_sets):
    result_sets.add(
        ResultSet(
            id="rs_1",
            scope_id="album1",
            query="海边",
            asset_ids=("asset_1", "asset_2"),
            created_at=NOW,
        )
    )

    loaded = result_sets.get("rs_1")

    assert loaded is not None
    assert loaded.asset_ids == ("asset_1", "asset_2")
    assert loaded.query == "海边"
    assert loaded.page(1).entries[1].handle == "photo_2"


def test_the_evidence_classes_survive_a_round_trip(result_sets):
    """候选的证据分级必须**落库**，不能只活在首页那次调用里。

    ``get_result_page`` 读的是库里这一份。分级不落库的话，翻页拿到的 preview 就会
    丢掉 ``level``——同一张图在第一页是 ``exact``、翻回来变成空的。
    """
    result_sets.add(
        ResultSet(
            id="rs_levels",
            scope_id="album1",
            query="海边",
            asset_ids=("asset_1", "asset_2"),
            created_at=NOW,
            evidence_classes={"asset_1": "anchor", "asset_2": "semantic"},
        )
    )

    loaded = result_sets.get("rs_levels")

    assert loaded is not None
    assert loaded.evidence_classes == {"asset_1": "anchor", "asset_2": "semantic"}
    assert loaded.page(1).entries[0].evidence_class == "anchor"
    assert loaded.page(1).entries[1].evidence_class == "semantic"


def test_audio_evidence_survives_a_round_trip(result_sets):
    result_sets.add(
        ResultSet(
            id="rs_audio",
            scope_id="home",
            query="拉面店点了什么",
            asset_ids=("asset_1",),
            created_at=NOW,
            audio_evidence={
                "asset_1": (
                    AudioEvidenceExcerpt(1_000, 2_000, "I ordered", False, "speaker_0"),
                    AudioEvidenceExcerpt(2_000, 4_000, "classic tonkotsu ramen", True, "speaker_0"),
                )
            },
        )
    )

    loaded = result_sets.get("rs_audio")

    assert loaded is not None
    assert loaded.audio_evidence["asset_1"][1].text == "classic tonkotsu ramen"
    assert loaded.audio_evidence["asset_1"][1].is_anchor is True
    assert loaded.page(1).entries[0].audio_evidence == loaded.audio_evidence["asset_1"]


def test_a_result_set_without_levels_reads_back_as_unknown_not_semantic(result_sets):
    """没带分级的结果集读回来是**「不知道」**（空串），不是 ``semantic``。

    这两件事不一样：前者是「这个结果集没有分级信息」（老数据、测试直接造的），
    后者是「判过，是相似度命中」。把前者伪造成后者，模型会以为拿到了一条它没被给过的
    判断。
    """
    result_sets.add(
        ResultSet(
            id="rs_plain",
            scope_id="album1",
            query="海边",
            asset_ids=("asset_1",),
            created_at=NOW,
        )
    )

    loaded = result_sets.get("rs_plain")

    assert loaded is not None
    assert loaded.evidence_classes == {}
    assert loaded.page(1).entries[0].evidence_class == ""


def test_a_missing_result_set_is_none(result_sets):
    assert result_sets.get("rs_nope") is None
