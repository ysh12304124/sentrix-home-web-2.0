"""真 SQLite + 真迁移文件下的向量仓储契约测试。

两套实现只差表名（``memory_vectors`` / ``text_vectors``），所以同一份契约参数化跑
两遍：任何一边的行为漂移都会红。文本表多一条 ``CHECK``（只收 semantic/episodic），
所以 ``record()`` 的 space 跟着实现走。
"""

from __future__ import annotations

import pytest

from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.text_vectors import (
    SqliteTextVectorRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

MODEL = scheme.IMAGE_MODEL

_REPOSITORIES = (
    (SqliteVectorRepository, "visual"),
    (SqliteTextVectorRepository, "semantic"),
)


@pytest.fixture(params=_REPOSITORIES, ids=["memory_vectors", "text_vectors"])
def repository(request, migrations_ready_db: Database) -> SqliteVectorRepository | SqliteTextVectorRepository:
    repository_class, _ = request.param
    return repository_class(migrations_ready_db)


def space_for(repository) -> str:
    return "semantic" if isinstance(repository, SqliteTextVectorRepository) else "visual"


def record(
    source_id: str,
    vector: tuple[float, ...],
    *,
    space: str,
    scope_id: str = "home-default",
    source_type: str = "asset",
    model_name: str = MODEL,
    metadata: dict[str, object] | None = None,
) -> VectorRecord:
    return VectorRecord(
        scope_id=scope_id,
        space=space,
        source_type=source_type,
        source_id=source_id,
        model_name=model_name,
        vector=vector,
        metadata=metadata or {},
    )


def test_upsert_then_has_vector(repository):
    space = space_for(repository)
    assert (
        repository.has_vector(space=space, source_type="asset", source_id="asset_1", model_name=MODEL)
        is False
    )

    repository.upsert(record("asset_1", (1.0, 0.0, 0.0), space=space))

    assert (
        repository.has_vector(space=space, source_type="asset", source_id="asset_1", model_name=MODEL) is True
    )
    assert repository.count(space=space) == 1


def test_upsert_same_key_updates_in_place(repository):
    """同一 (space, source_type, source_id, model_name) 只该有一行。"""
    space = space_for(repository)
    repository.upsert(record("asset_1", (1.0, 0.0, 0.0), space=space))
    repository.upsert(record("asset_1", (0.0, 1.0, 0.0), space=space))

    assert repository.count(space=space) == 1
    hits = repository.search_exact(space=space, vector=(0.0, 1.0, 0.0), limit=5)
    assert len(hits) == 1
    assert hits[0].score == pytest.approx(1.0)


def test_two_models_coexist_as_separate_rows(repository):
    """同一张图两套模型的向量必须并存。

    1.0 就是因为 env 切换导致多套向量并存、索引对不上才把方案钉死的；并存本身
    不是问题，**查询时不带模型名**才是。这条钉住并存，下面那条钉住按模型过滤。
    """
    space = space_for(repository)
    repository.upsert(record("asset_1", (1.0, 0.0, 0.0), space=space, model_name="chinese-clip-ViT-L-14"))
    repository.upsert(record("asset_1", (0.0, 1.0, 0.0), space=space, model_name="ViT-B-32"))

    assert repository.count(space=space) == 2


def test_search_exact_filters_by_model_name(repository):
    space = space_for(repository)
    repository.upsert(record("asset_old", (0.0, 1.0, 0.0), space=space, model_name="ViT-B-32"))
    repository.upsert(record("asset_new", (1.0, 0.0, 0.0), space=space, model_name=MODEL))

    hits = repository.search_exact(space=space, vector=(1.0, 0.0, 0.0), limit=5, model_name=MODEL)

    assert [hit.source_id for hit in hits] == ["asset_new"]


def test_search_exact_orders_by_cosine_descending(repository):
    space = space_for(repository)
    repository.upsert(record("asset_near", (0.9, 0.1, 0.0), space=space))
    repository.upsert(record("asset_far", (0.0, 0.0, 1.0), space=space))

    hits = repository.search_exact(space=space, vector=(1.0, 0.0, 0.0), limit=5)

    assert [hit.source_id for hit in hits] == ["asset_near", "asset_far"]
    assert hits[0].score > hits[1].score


def test_search_exact_ties_break_on_source_id(repository):
    space = space_for(repository)
    repository.upsert(record("asset_z", (1.0, 0.0, 0.0), space=space))
    repository.upsert(record("asset_a", (1.0, 0.0, 0.0), space=space))

    hits = repository.search_exact(space=space, vector=(1.0, 0.0, 0.0), limit=5)

    assert [hit.source_id for hit in hits] == ["asset_a", "asset_z"]


def test_search_exact_skips_rows_with_a_different_dimension(repository):
    """维度不一致的脏行必须被排除，不能以「相似度 0」的形态混进结果。"""
    space = space_for(repository)
    repository.upsert(record("asset_512", (1.0,) * 512, space=space))
    repository.upsert(record("asset_768", (1.0,) * 768, space=space))

    hits = repository.search_exact(space=space, vector=(1.0,) * 768, limit=5)

    assert [hit.source_id for hit in hits] == ["asset_768"]


def test_search_exact_filters_by_scope(repository):
    space = space_for(repository)
    repository.upsert(record("asset_a", (1.0, 0.0, 0.0), space=space, scope_id="album1"))
    repository.upsert(record("asset_b", (1.0, 0.0, 0.0), space=space, scope_id="album2"))

    hits = repository.search_exact(space=space, vector=(1.0, 0.0, 0.0), limit=5, scope_id="album1")

    assert [hit.source_id for hit in hits] == ["asset_a"]


def test_search_exact_returns_empty_for_an_empty_query(repository):
    space = space_for(repository)
    repository.upsert(record("asset_1", (1.0, 0.0, 0.0), space=space))

    assert repository.search_exact(space=space, vector=(), limit=5) == []


def test_empty_vector_is_rejected_loudly(repository):
    """1.0 的空向量会被照单 upsert，整条链路不报错，事后手工清点才发现。

    这里改成硬拒绝：宁可在写入点炸，也不要在检索侧表现为「这张图就是搜不到」。
    """
    with pytest.raises(ValueError):
        repository.upsert(record("asset_empty", (), space=space_for(repository)))


def test_metadata_round_trips(repository):
    space = space_for(repository)
    repository.upsert(record("asset_1", (1.0, 0.0, 0.0), space=space, metadata={"observation_id": "obs_1"}))

    hits = repository.search_exact(space=space, vector=(1.0, 0.0, 0.0), limit=5)

    assert hits[0].metadata == {"observation_id": "obs_1"}


def test_count_filters_by_model_name(repository):
    """应用层要拿这个口径去和 Qdrant 的 collection 计数比对，必须能按模型过滤。"""
    space = space_for(repository)
    repository.upsert(record("asset_new", (1.0, 0.0, 0.0), space=space, model_name=MODEL))
    repository.upsert(record("asset_old", (1.0, 0.0, 0.0), space=space, model_name="ViT-B-32"))

    assert repository.count(space=space) == 2
    assert repository.count(space=space, model_name=MODEL) == 1
    assert repository.count(space=space, model_name="不存在") == 0
