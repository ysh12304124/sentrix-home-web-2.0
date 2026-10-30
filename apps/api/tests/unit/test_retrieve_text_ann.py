"""``RetrieveTextAnn`` 的单元测试：全假适配器。

**要钉的是三条会静默出错的形状**，不是「能不能查出东西」：

1. **按 asset 聚合取最大**——不聚合的话，同一张图被三个 observation 命中会拿到三个
   名次，而 RRF 对「同一候选出现多次」不去重，它的分会变成三倍；
2. **查两个空间**——写入侧写两处（semantic / episodic），只查一个会漏掉一半，
   而结果少几张图不会有任何报错；
3. **映不回 Asset 的命中要丢掉**——1.0 那句 ``metadata.get("asset_id") or source_id``
   在文本空间里退回的是 **observation id**，它会一路当成 asset_id 传下去，
   到交付那一步才 404，**而检索这一层看起来完全正常**。
"""

from __future__ import annotations

import pytest

from sentrix.application.retrieval.retrieve_text_ann import (
    CHANNEL,
    SPACES,
    RetrieveTextAnn,
    TextSearchRequest,
)
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorHit

SCOPE = "album1"


class FakeTextEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 3

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = [1.0, 0.0, 0.0] if vector is None else vector
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)


class FakeVectors:
    """按 space 返回预设命中；记录每次查询的 space，好断言「两个空间都查了」。"""

    def __init__(
        self,
        by_space: dict[str, list[VectorHit]] | None = None,
        *,
        foreign: dict[str, int] | None = None,
    ) -> None:
        self.by_space = by_space or {}
        # 「这个 space 里有 N 条向量，但**都属于别的方案**」：不带 model 过滤数得到，
        # 带当前 model 数不到，``search_exact`` 也查不出东西——漂移的真实形状。
        # 默认空，既有用例语义不变。
        self.foreign = foreign or {}
        self.queried: list[str] = []

    def search_exact(self, *, space, vector, limit, scope_id=None, model_name=None):
        self.queried.append(space)
        return list(self.by_space.get(space, []))[:limit]

    def count(self, *, space: str, model_name=None) -> int:
        if model_name is None:
            return len(self.by_space.get(space, [])) + self.foreign.get(space, 0)
        return len(self.by_space.get(space, []))


def hit(
    source_id: str,
    score: float,
    *,
    asset_id: str | None,
    space: str,
    source_type: str = "observation",
    metadata_extra: dict[str, object] | None = None,
) -> VectorHit:
    metadata = {"asset_id": asset_id} if asset_id else {}
    metadata.update(metadata_extra or {})
    return VectorHit(
        source_id=source_id,
        scope_id=SCOPE,
        source_type=source_type,
        model_name=scheme.TEXT_MODEL,
        score=score,
        metadata=metadata,
    )


def build(by_space=None, **kwargs) -> tuple[RetrieveTextAnn, FakeTextEmbedder, FakeVectors]:
    embedder = FakeTextEmbedder(**kwargs)
    vectors = FakeVectors(by_space)
    return RetrieveTextAnn(embedder=embedder, vectors=vectors), embedder, vectors


def test_queries_both_spaces():
    """写入侧写两处，查询侧就要两个都查。只查一个会漏掉一半，**而结果少几张图不报错**。"""
    _, _, vectors = build()

    RetrieveTextAnn(embedder=FakeTextEmbedder(), vectors=vectors).execute(
        TextSearchRequest(text="海边", scope_id=SCOPE)
    )

    assert vectors.queried == list(SPACES)


def test_aggregates_by_asset_taking_the_max_score():
    """同一张图被两个 observation 命中 → 一条候选，取**最大**那个分数。

    不聚合的话它会以两个名次进融合层，而 RRF 对同一候选出现多次不去重——
    分数被抬成两份，那张图会因此排到它不该在的位置。
    """
    by_space = {
        scheme.SEMANTIC_SPACE: [
            hit("obs_1", 0.62, asset_id="asset_a", space=scheme.SEMANTIC_SPACE),
            hit("obs_2", 0.91, asset_id="asset_a", space=scheme.SEMANTIC_SPACE),
        ],
        scheme.EPISODIC_SPACE: [
            hit("obs_3", 0.75, asset_id="asset_b", space=scheme.EPISODIC_SPACE),
        ],
    }
    index, _, _ = build(by_space)

    result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert [item.asset_id for item in result.hits] == ["asset_a", "asset_b"]
    assert result.hits[0].raw_scores[CHANNEL] == pytest.approx(0.91)


def test_audio_segment_hit_keeps_its_time_range() -> None:
    by_space = {
        scheme.SEMANTIC_SPACE: [
            hit(
                "aseg_1",
                0.9,
                asset_id="asset_audio",
                space=scheme.SEMANTIC_SPACE,
                source_type="audio_segment",
                metadata_extra={"start_ms": 1200, "end_ms": 3400},
            )
        ]
    }
    index, _, _ = build(by_space)

    result = index.execute(TextSearchRequest(text="公园散步", scope_id=SCOPE))

    assert result.hits[0].asset_id == "asset_audio"
    assert result.hits[0].start_ms == 1200
    assert result.hits[0].end_ms == 3400


def test_weaker_audio_hit_does_not_attach_irrelevant_context_to_stronger_observation() -> None:
    by_space = {
        scheme.SEMANTIC_SPACE: [
            hit("obs_1", 0.95, asset_id="asset_video", space=scheme.SEMANTIC_SPACE),
            hit(
                "aseg_1",
                0.40,
                asset_id="asset_video",
                space=scheme.SEMANTIC_SPACE,
                source_type="audio_segment",
                metadata_extra={"start_ms": 12_000, "end_ms": 15_000},
            ),
        ]
    }
    index, _, _ = build(by_space)

    result = index.execute(TextSearchRequest(text="红色背包", scope_id=SCOPE))

    assert result.hits[0].asset_id == "asset_video"
    assert result.hits[0].start_ms is None
    assert result.hits[0].end_ms is None


def test_scores_from_different_spaces_compete_together():
    """两个空间的分要放在一起比。分开排名会让「semantic 的第一名」永远压过
    「episodic 的第一名」，而它们本来就是同一个空间里的同一种相似度。"""
    by_space = {
        scheme.SEMANTIC_SPACE: [hit("obs_1", 0.4, asset_id="asset_low", space=scheme.SEMANTIC_SPACE)],
        scheme.EPISODIC_SPACE: [hit("obs_2", 0.9, asset_id="asset_high", space=scheme.EPISODIC_SPACE)],
    }
    index, _, _ = build(by_space)

    result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert [item.asset_id for item in result.hits] == ["asset_high", "asset_low"]


def test_hits_without_an_asset_id_are_dropped(caplog):
    """**1.0 会退回 source_id**，而在文本空间里那是 observation id——它会被一路当成
    asset_id 传下去，到交付那一步才 404，而检索这一层看起来完全正常。"""
    by_space = {
        scheme.SEMANTIC_SPACE: [
            hit("obs_1", 0.9, asset_id="asset_a", space=scheme.SEMANTIC_SPACE),
            hit("obs_2", 0.95, asset_id=None, space=scheme.SEMANTIC_SPACE),
        ]
    }
    index, _, _ = build(by_space)

    with caplog.at_level("WARNING"):
        result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert [item.asset_id for item in result.hits] == ["asset_a"]
    assert "obs_2" not in {item.asset_id for item in result.hits}
    assert "缺少 asset_id" in caplog.text


def test_empty_text_returns_nothing_without_embedding():
    index, embedder, vectors = build()

    result = index.execute(TextSearchRequest(text="   ", scope_id=SCOPE))

    assert result.hits == ()
    assert embedder.calls == []
    assert vectors.queried == []


def test_unavailable_embedder_is_degraded_not_empty_silently():
    """sidecar 没起时适配器返回空表（端口上没有 available 可问）。**必须置 degraded**，
    否则「文本能力没了」看起来与「这个词没命中」一模一样。"""
    index, _, vectors = build(vector=[])

    result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert result.hits == ()
    assert result.degraded is True
    assert result.degraded_reason == "embedder_unavailable"
    assert vectors.queried == []


def test_dimension_mismatch_is_degraded():
    index, _, vectors = build(vector=[1.0, 2.0])

    result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert result.degraded is True
    assert result.degraded_reason.startswith("query_dimension_mismatch")
    assert vectors.queried == []


def test_backend_is_exact_not_a_degraded_index():
    """文本通道**没有降级档**：它本来就只查权威存储。``degraded=False`` 不是
    「索引健康」，是「这条通道不依赖索引」——两者的区别写在字段注释里。"""
    by_space = {scheme.SEMANTIC_SPACE: [hit("obs_1", 0.9, asset_id="asset_a", space=scheme.SEMANTIC_SPACE)]}
    index, _, _ = build(by_space)

    result = index.execute(TextSearchRequest(text="海边", scope_id=SCOPE))

    assert result.backend == "sqlite_exact"
    assert result.degraded is False
    assert result.authoritative_vectors == 1


def test_vectors_from_another_scheme_are_reported_as_scheme_drift():
    """文本通道写两个 space，任一漂移都要报。

    定点变异：去掉 `execute` 里那段 `drift` 计算，这条红；
    只查 `semantic` 不查 `episodic`，下面第二条红。
    """
    vectors = FakeVectors(foreign={scheme.SEMANTIC_SPACE: 46})

    result = RetrieveTextAnn(embedder=FakeTextEmbedder(), vectors=vectors).execute(
        TextSearchRequest(text="海边", scope_id=SCOPE)
    )

    assert result.degraded is True
    assert "scheme_out_of_sync" in result.degraded_reason


def test_drift_in_the_episodic_space_alone_is_still_reported():
    """**只漂了 episodic 也要报。** 只查 semantic 的实现会漏掉这一种——
    两个 space 各自独立，一个干净不代表另一个干净。
    """
    vectors = FakeVectors(foreign={scheme.EPISODIC_SPACE: 46})

    result = RetrieveTextAnn(embedder=FakeTextEmbedder(), vectors=vectors).execute(
        TextSearchRequest(text="海边", scope_id=SCOPE)
    )

    assert result.degraded is True
    assert scheme.EPISODIC_SPACE in result.degraded_reason


def test_an_empty_store_is_not_scheme_drift():
    """空 store 两个数都是 0 → 安静。理由同视觉通道那条。"""
    vectors = FakeVectors()

    result = RetrieveTextAnn(embedder=FakeTextEmbedder(), vectors=vectors).execute(
        TextSearchRequest(text="海边", scope_id=SCOPE)
    )

    assert result.degraded is False
    assert result.degraded_reason == ""
