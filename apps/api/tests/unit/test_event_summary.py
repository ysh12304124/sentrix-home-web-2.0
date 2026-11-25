"""事件总结：确定性摘要 + 模型那条路的两处收口。

对应 1.0 ``pipeline.py:636-691``。三条行为原样保留：

1. 事件没有观察 / 模型端点缺席 → **原样返回**，不写任何东西；
2. 模型返回的不是可用投影（裸字符串、占位标题）→ 用确定性摘要覆盖；
3. 模型调用抛异常 → 同上。

``fallback_projection`` 是这一片里唯一完全确定性的东西，所以它是这里的重点：
整册导入里绝大多数事件本来就走它。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.application.memories.summarize_event import (
    FALLBACK_CONFIDENCE,
    FALLBACK_MODEL,
    PLACEHOLDER_TITLES,
    SummarizeEvent,
    fallback_projection,
    projection_from_reply,
)
from sentrix.domain.assistant.models import ChatMessage, ChatRole, ModelReply
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.memories.events import EventRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)


def event(**overrides) -> EventRecord:
    fields: dict[str, object] = {
        "id": "evt_1",
        "scope_id": "album1",
        "title": "待总结事件",
        "event_type": "待判断",
        "time_start": NOW,
        "time_end": NOW,
        "place": "浙江省杭州市萧山区",
        "admin_district": "萧山区",
        "activity": "",
        "summary": "",
        "confidence": 0.5,
        "aggregation_score": 0.0,
        "aggregation_breakdown": {},
        "created_at": NOW,
    }
    fields.update(overrides)
    return EventRecord(**fields)  # type: ignore[arg-type]


def observation(**overrides) -> Observation:
    fields: dict[str, object] = {
        "id": "obs_1",
        "scope_id": "album1",
        "asset_id": "asset_1",
        "source_type": "import",
        "captured_at": NOW,
        "confidence": 0.65,
        "created_at": NOW,
        "updated_at": NOW,
        "caption": "一家人在公园里拍照",
        "place": "公园",
        "activity": "拍照",
        "event_type": "户外活动",
        "objects": ("相机",),
        "revision": 2,
    }
    fields.update(overrides)
    return Observation(**fields)  # type: ignore[arg-type]


# ---- 确定性摘要 ------------------------------------------------------------------


def test_fallback_uses_place_plus_first_activity_as_the_title():
    projection = fallback_projection(event(), [observation()])

    assert projection.title == "浙江省杭州市萧山区拍照"
    assert projection.activity == "拍照"
    assert projection.event_type == "户外活动"
    assert projection.confidence == pytest.approx(FALLBACK_CONFIDENCE)
    assert projection.model == FALLBACK_MODEL


def test_fallback_summary_lists_captions_and_truncates_to_four():
    observations = [observation(id=f"obs_{index}", caption=f"第{index}张") for index in range(6)]

    projection = fallback_projection(event(place="其他或不确定"), observations)

    assert projection.summary == "其他或不确定记录了第0张；第1张；第2张；第3张"
    assert projection.title == projection.activity  # 地点是占位值时标题不拼它


def test_fallback_drops_noise_values_and_duplicates():
    """1.0 ``:645``：``待判断`` / ``未识别`` / ``未知`` 不当内容，重复的只留一条。"""
    observations = [
        observation(activity="待判断", caption="未知", event_type="未识别"),
        observation(id="obs_2", activity="拍照", caption="拍照", event_type="户外活动"),
        observation(id="obs_3", activity="拍照", caption="拍照"),
    ]

    projection = fallback_projection(event(), observations)

    assert projection.activity == "拍照"
    assert projection.event_type == "户外活动"
    assert projection.summary.count("拍照") == 1


def test_fallback_is_deterministic():
    assert fallback_projection(event(), [observation()]) == fallback_projection(event(), [observation()])


def test_fallback_truncates_the_long_fields():
    observations = [observation(activity="活" * 60, event_type="类" * 60, caption="字" * 400)]

    projection = fallback_projection(event(), observations)

    assert len(projection.title) == 20
    assert len(projection.activity) == 20
    assert len(projection.event_type) == 20
    assert len(projection.summary) == 240


# ---- 模型那条路的收口 ------------------------------------------------------------


@pytest.mark.parametrize("reply", ["", "不是 JSON", "```json\n{}\n```", "{}"])
def test_unusable_replies_give_no_projection(reply: str):
    assert projection_from_reply(reply, event=event()) is None


@pytest.mark.parametrize("title", sorted(PLACEHOLDER_TITLES))
def test_placeholder_titles_fall_back(title: str):
    """1.0 ``:660``：模型把占位标题原样还回来，等于没总结。"""
    assert projection_from_reply(f'{{"title": "{title}", "summary": "x"}}', event=event()) is None


def test_a_usable_reply_becomes_a_projection():
    projection = projection_from_reply(
        '{"title": "在萧山区的家庭活动", "event_type": "户外活动", "activity": "拍照", '
        '"summary": "下午在公园里拍照", "confidence": 0.8}',
        event=event(),
    )

    assert projection is not None
    assert projection.title == "在萧山区的家庭活动"
    assert projection.confidence == pytest.approx(0.8)


# ---- 用例 ------------------------------------------------------------------------


class FakeChatModel:
    """与 ``OpenAIChatModel.complete`` 同契约的假件。

    **两条前置条件必须在这里也成立**：假件放过它们，「单条 user 消息」这种违反就
    永远不会在单测里露头。Task 57b 的 `SummarizeEvent` 正是这么漏的——它照 1.0
    ``model_clients.py:314`` 的 ``chat(prompt)`` 只发了一条 user 消息，而 2.0 的端口
    要求 ``messages[0]`` 是 system，于是**每一次模型调用都在本地就抛**
    （``ChatModelError``），整册导入 17 个事件全部退回确定性摘要（实测）。
    """

    def __init__(self, reply: str = "", *, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0
        self.messages: tuple[ChatMessage, ...] = ()

    def complete(self, messages, tools) -> ModelReply:  # noqa: ANN001
        assert messages and messages[0].role is ChatRole.SYSTEM, "messages[0] 必须是 system"
        assert not any(item.role is ChatRole.SYSTEM for item in messages[1:]), "system 只能出现在开头"
        self.messages = tuple(messages)
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ModelReply(content=self.reply)


@pytest.fixture
def repos(migrations_ready_db: Database):
    return (
        SqliteEventRepository(migrations_ready_db),
        SqliteObservationRepository(migrations_ready_db),
        SqliteAssetRepository(migrations_ready_db),
    )


def seed(repos, *, with_observation: bool = True):  # noqa: ANN001
    events, observations, assets = repos
    assets.add(
        Asset(
            id="asset_1",
            scope_id="album1",
            file_name="a.jpg",
            media_type=MediaType.IMAGE,
            path="/tmp/a.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256="sha-1",
            captured_at=NOW,
            captured_location=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    if with_observation:
        observations.add(observation())
        events.attach("evt_1", "obs_1")


def use_case(repos, model):  # noqa: ANN001
    events, observations, assets = repos
    return SummarizeEvent(events=events, observations=observations, assets=assets, model=model)


def test_event_without_observations_is_returned_untouched(repos):
    seed(repos, with_observation=False)
    model = FakeChatModel('{"title": "不该被用到"}')

    result = use_case(repos, model).execute("evt_1")

    assert result.title == "待总结事件"
    assert model.calls == 0


def test_metadata_only_observation_is_not_sent_to_generate_a_fictional_event(repos):
    seed(repos, with_observation=False)
    events, observations, _ = repos
    observations.add(
        observation(caption="", activity="", objects=(), clothing=(), place="其他或不确定", event_type="视频")
    )
    events.attach("evt_1", "obs_1")
    model = FakeChatModel('{"title":"时间戳记录","summary":"不应生成"}')
    result = use_case(repos, model).execute("evt_1")
    # Baseline import follows main; correction alone rejects empty evidence.
    assert model.calls == 1 and result.summary == "不应生成"
    with pytest.raises(RuntimeError, match="尚无可用观察"):
        use_case(repos, model).execute("evt_1", correction=True)


def test_missing_event_place_uses_consistent_observation_place_without_guessing_admin(repos):
    seed(repos)
    events, observations, _ = repos
    from sentrix.domain.media.ports import ObservationSemantics

    observations.enrich(
        "obs_1", semantics=ObservationSemantics(caption="有人在客厅看书", place="客厅", activity="看书")
    )
    events.apply_summary("evt_1", title="待总结事件", summary="", activity="", event_type="待判断", place="")
    model = FakeChatModel('{"title":"客厅看书","summary":"有人在客厅看书","activity":"看书"}')
    result = use_case(repos, model).execute("evt_1", correction=True)
    assert result.place == events.get("evt_1").place == "客厅"
    assert "客厅" in model.messages[1].content


def test_model_reply_is_persisted_and_revision_advances(repos):
    seed(repos)
    model = FakeChatModel(
        '{"title": "萧山区家庭活动", "event_type": "户外活动", "activity": "拍照", "summary": "下午拍照"}'
    )

    result = use_case(repos, model).execute("evt_1")

    assert result.title == "萧山区家庭活动"
    assert result.summary == "下午拍照"
    assert result.revision == 2
    assert model.calls == 1


def test_model_failure_falls_back_to_the_deterministic_summary(repos):
    seed(repos)

    result = use_case(repos, FakeChatModel(error=RuntimeError("8100 挂了"))).execute("evt_1")

    assert result.title == "浙江省杭州市萧山区拍照"
    assert result.revision == 2


def test_a_bare_string_reply_falls_back(repos):
    """1.0 ``:658-660`` 的注释记的就是这个：本地模型把结构化契约解码成裸字符串。"""
    seed(repos)

    result = use_case(repos, FakeChatModel("这是一段没有 JSON 的回复")).execute("evt_1")

    assert result.title == "浙江省杭州市萧山区拍照"


def test_missing_event_raises(repos):
    with pytest.raises(KeyError):
        use_case(repos, FakeChatModel()).execute("evt_gone")


def test_the_model_call_carries_a_system_message_that_holds_the_prompt(repos):
    """提示词进 system、证据进 user，且 system 是第一条。

    1.0 把两段拼成一条 user 消息（``model_clients.py:314`` 的 ``chat(prompt)``）；
    2.0 的 ``ChatModel`` 端口不接受那个形状（``chat_client.py:98``）。这条用例与
    ``FakeChatModel`` 的两条断言一起，把这个形状钉死。
    """
    seed(repos)
    model = FakeChatModel('{"title": "萧山区家庭活动", "summary": "下午拍照"}')

    use_case(repos, model).execute("evt_1")

    assert [item.role for item in model.messages] == [ChatRole.SYSTEM, ChatRole.USER]
    assert "事件总结器" in model.messages[0].content
    assert "obs_1" in model.messages[1].content  # 证据在 user 那条里
    assert "事件总结器" not in model.messages[1].content


def identity_update(name="小禾", previous_name="旧称", observation_id="obs_1"):
    return {
        "entity_id": "person_1",
        "name": name,
        "previous_name": previous_name,
        "status": "confirmed",
        "observation_ids": [observation_id],
    }


@pytest.mark.parametrize(
    "title,summary",
    [
        ("男孩在公园拍照", "小禾在公园拍照"),
        ("小禾在公园拍照", "旧称在公园拍照"),
        ("小禾在公园拍照", "小禾：一个孩子在公园拍照"),
        ("小禾在公园拍照", "小禾:一个孩子在公园拍照"),
    ],
)
def test_correction_rejects_inconsistent_title_or_summary_without_publishing(repos, title, summary):
    seed(repos)
    before = repos[0].get("evt_1")
    model = FakeChatModel(json.dumps({"title": title, "summary": summary}, ensure_ascii=False))
    with pytest.raises(RuntimeError, match="身份一致性"):
        use_case(repos, model).execute("evt_1", correction=True, identity_updates=[identity_update()])
    assert model.calls == 2
    assert repos[0].get("evt_1") == before


def test_correction_retries_then_publishes_title_and_summary_together(repos):
    seed(repos)

    class RepairModel(FakeChatModel):
        def complete(self, messages, tools):
            self.reply = json.dumps(
                {
                    "title": "男孩在公园拍照" if not self.calls else "小禾在公园拍照",
                    "summary": "小禾与一名未命名女孩在公园拍照",
                },
                ensure_ascii=False,
            )
            return super().complete(messages, tools)

    model = RepairModel()
    result = use_case(repos, model).execute("evt_1", correction=True, identity_updates=[identity_update()])
    assert result.title == "小禾在公园拍照"
    assert result.summary == "小禾与一名未命名女孩在公园拍照"
    assert result.revision == 2
    assert model.calls == 2


def test_correction_does_not_supply_names_from_other_events(repos):
    seed(repos)
    model = FakeChatModel('{"title":"在公园拍照","summary":"一家人在公园拍照"}')
    result = use_case(repos, model).execute(
        "evt_1", correction=True, identity_updates=[identity_update(observation_id="unrelated_obs")]
    )
    assert result.title == "在公园拍照"
    evidence = json.loads(model.messages[1].content)
    assert evidence["identity_updates"] == []
    assert "小禾" not in model.messages[1].content


def test_correction_requires_all_short_bound_names_in_title_and_summary(repos):
    seed(repos)
    model = FakeChatModel('{"title":"小禾与小川拍照","summary":"小禾和小川与一名未命名女孩在公园拍照"}')
    result = use_case(repos, model).execute(
        "evt_1",
        correction=True,
        identity_updates=[identity_update(), identity_update(name="小川", previous_name="")],
    )
    assert result.title == "小禾与小川拍照"
    assert "小川" in result.summary


def test_correction_rejects_title_that_leaves_another_named_person_generic(repos):
    seed(repos)
    before = repos[0].get("evt_1")
    model = FakeChatModel('{"title":"小禾与男孩拍照","summary":"小禾和小川在公园拍照"}')
    with pytest.raises(RuntimeError, match="身份一致性"):
        use_case(repos, model).execute(
            "evt_1",
            correction=True,
            identity_updates=[identity_update(), identity_update(name="小川", previous_name="")],
        )
    assert repos[0].get("evt_1") == before


def test_correction_model_failure_is_not_reported_as_synchronized(repos):
    seed(repos)
    model = FakeChatModel(error=RuntimeError("model offline"))
    with pytest.raises(RuntimeError, match="模型调用失败"):
        use_case(repos, model).execute("evt_1", correction=True, identity_updates=[identity_update()])
    assert repos[0].get("evt_1").revision == 1


def test_pending_user_event_edit_stays_out_of_automatic_identity_rebuild(repos, migrations_ready_db):
    from sentrix.infrastructure.persistence.repositories.corrections import SqliteMemoryCorrections

    seed(repos)
    corrections = SqliteMemoryCorrections(migrations_ready_db)
    corrections.correct("event", "evt_1", 1, {"title": "用户保留的标题"}, "用户编辑")
    with corrections.track("album1", "observation", "obs_1"):
        pass
    assert "evt_1" in corrections.affected_events("album1")
    assert "evt_1" not in corrections.summary_events("album1")
    corrections.refresh_dependencies("album1")
    assert repos[0].get("evt_1").title == "用户保留的标题"
