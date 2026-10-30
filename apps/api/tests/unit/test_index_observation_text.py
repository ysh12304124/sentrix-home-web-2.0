"""``IndexObservationText`` 的单元测试：全假适配器，不碰 sidecar 也不碰磁盘。

**要钉的三件事都不是「有没有写向量」**，而是三条会静默出错的形状：

1. ``asset_id`` 在不在 observation 那两行的 metadata 里——``text_ann`` 命中的是
   observation，候选要按 Asset 交付，**映不回去的话命中了也没法交付**；
2. 三行是不是同一条向量——分开嵌三次会得到三个略微不同的向量，
   而它们在同一个空间里本该是同一条；
3. 幂等——重复导入同一张图不该再打一次 sidecar。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sentrix.application.retrieval.index_observation_text import (
    EVENT_SOURCE,
    OBSERVATION_SOURCE,
    IndexObservationText,
    compose_text,
)
from sentrix.domain.media.models import Observation, ObservationFact
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord

NOW = datetime(2026, 9, 29, tzinfo=UTC)


class FakeTextEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 4

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = [0.25, 0.5, 0.75, 1.0] if vector is None else vector
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)


class FakeVectors:
    def __init__(self) -> None:
        self.records: list[VectorRecord] = []

    def upsert(self, record: VectorRecord) -> None:
        self.records.append(record)

    def get(self, *, space, source_type, source_id, model_name=None):
        return next(
            (
                item
                for item in reversed(self.records)
                if item.space == space
                and item.source_type == source_type
                and item.source_id == source_id
                and (model_name is None or item.model_name == model_name)
            ),
            None,
        )

    def delete_source(self, *, space, source_type, source_id, model_name):
        before = len(self.records)
        self.records = [
            item
            for item in self.records
            if not (
                item.space == space
                and item.source_type == source_type
                and item.source_id == source_id
                and item.model_name == model_name
            )
        ]
        return before - len(self.records)


def make_observation(**overrides) -> Observation:
    base = dict(
        id="obs_1",
        scope_id="album1",
        asset_id="asset_1",
        source_type="import",
        captured_at=NOW,
        confidence=0.9,
        created_at=NOW,
        updated_at=NOW,
        caption="海边",
        activity="散步",
        place="青岛",
        ocr_text="门票",
        clothing=("蓝外套", "白鞋"),
        objects=("海鸥", "沙滩"),
        facts=(ObservationFact(subject="爸爸", predicate="去过的城市", object="青岛"),),
    )
    base.update(overrides)
    return Observation(**base)


def build(**kwargs) -> tuple[IndexObservationText, FakeTextEmbedder, FakeVectors]:
    embedder = FakeTextEmbedder(**kwargs)
    vectors = FakeVectors()
    return IndexObservationText(embedder=embedder, vectors=vectors), embedder, vectors


def test_compose_text_follows_the_1_0_field_order():
    """字段顺序是 1.0 的顺序。**顺序本身不影响向量质量，但它决定了幂等判定是否可靠**——
    文本变了而 ``model_name`` 没变的话，第二次跑会被 ``has_vector`` 当成「已经有了」。"""
    text = compose_text(make_observation())

    assert text == "海边 散步 青岛 门票 蓝外套 白鞋 爸爸 去过的城市 青岛 海鸥 沙滩"


def test_compose_text_skips_empty_fields():
    text = compose_text(
        make_observation(caption="", activity="", ocr_text="", clothing=(), objects=(), facts=())
    )

    assert text == "青岛"


def test_writes_three_rows_with_one_embedding():
    """三行同一条向量。分开嵌三次会得到三个略微不同的向量，而它们在同一个空间里
    本该是同一条——`episodic` 与 `semantic` 的相似度比较会因此带上一次嵌入的噪声。"""
    index, embedder, vectors = build()

    outcome = index.index(make_observation(), "evt_1")

    assert outcome.indexed is True
    assert outcome.rows == 3
    assert len(embedder.calls) == 1, "只该嵌一次"
    assert len({record.vector for record in vectors.records}) == 1
    assert {(r.space, r.source_type) for r in vectors.records} == {
        (scheme.EPISODIC_SPACE, OBSERVATION_SOURCE),
        (scheme.EPISODIC_SPACE, EVENT_SOURCE),
        (scheme.SEMANTIC_SPACE, OBSERVATION_SOURCE),
    }


def test_observation_rows_carry_the_asset_id():
    """**这条是召回能不能落地的关键。** ANN 命中的是 observation，候选按 Asset 交付，
    `text_ann` 就是靠这个 metadata 映回去的。少了它，命中了也交付不出来，
    而**接口照样 200**，只是结果里少几张图。"""
    index, _, vectors = build()

    index.index(make_observation(), "evt_1")

    observation_rows = [r for r in vectors.records if r.source_type == OBSERVATION_SOURCE]
    assert len(observation_rows) == 2
    for record in observation_rows:
        assert record.metadata["asset_id"] == "asset_1"
        assert record.metadata["event_id"] == "evt_1"


def test_event_row_points_at_the_event_and_the_observation():
    index, _, vectors = build()

    index.index(make_observation(), "evt_1")

    (event_row,) = [r for r in vectors.records if r.source_type == EVENT_SOURCE]
    assert event_row.source_id == "evt_1"
    assert event_row.metadata == {"observation_id": "obs_1"}


def test_second_call_is_idempotent():
    """重复导入同一张图不该再打一次 sidecar。"""
    index, embedder, vectors = build()

    index.index(make_observation(), "evt_1")
    outcome = index.index(make_observation(), "evt_1")

    assert outcome.indexed is True
    assert outcome.rows == 0
    assert outcome.reason == "already_indexed"
    assert len(embedder.calls) == 1, "幂等命中不该再嵌一次"
    assert len(vectors.records) == 3


def test_new_revision_removes_old_vectors_before_embedding_failure():
    """重转写后不能在新嵌入失败时继续召回旧 ASR 文本。"""
    index, embedder, vectors = build()
    index.index(make_observation(revision=1), "evt_old")
    embedder.vector = []

    outcome = index.index(make_observation(revision=2, transcript_text="新的音频转写"), "evt_new")

    assert outcome.reason == "embedding_unavailable"
    assert vectors.records == []


def test_empty_text_does_not_call_the_sidecar():
    index, embedder, vectors = build()

    outcome = index.index(
        make_observation(caption="", activity="", place="", ocr_text="", clothing=(), objects=(), facts=()),
        "evt_1",
    )

    assert outcome.indexed is False
    assert outcome.reason == "empty_text"
    assert embedder.calls == []
    assert vectors.records == []


def test_unavailable_embedder_writes_nothing():
    """sidecar 挂了 → 空向量 → 不写。**不能写成「导入成功但没有文本向量」的静默成功**。"""
    index, _, vectors = build(vector=[])

    outcome = index.index(make_observation(), "evt_1")

    assert outcome.indexed is False
    assert outcome.reason == "embedding_unavailable"
    assert vectors.records == []


def test_dimension_mismatch_writes_nothing():
    """维度不符**在检索侧不报错**，只是永远算不出相似度——所以在这里挡掉。"""
    index, _, vectors = build(vector=[1.0, 2.0])

    outcome = index.index(make_observation(), "evt_1")

    assert outcome.indexed is False
    assert outcome.reason.startswith("dimension_mismatch")
    assert vectors.records == []


def test_without_an_event_only_the_observation_rows_are_written():
    """1.0 里这条路走不到（它总是先归并进一个事件）。2.0 的事件归并是可选的协作者，
    所以没有事件时只写观察那两行——**语义空间照样可检索**，不该整条跳过。"""
    index, _, vectors = build()

    outcome = index.index(make_observation(), None)

    assert outcome.rows == 2
    assert outcome.reason == "no_event"
    assert {r.source_type for r in vectors.records} == {OBSERVATION_SOURCE}


def test_row_count_matches_what_was_written():
    """``rows`` 是给调用方看的可观测面（``ImportResult.text_rows``）。
    它与 ``len(vectors.records)`` 必须一致，否则那个计数在骗人。"""
    index, _, vectors = build()

    outcome = index.index(make_observation(), "evt_1")

    assert outcome.rows == len(vectors.records)
