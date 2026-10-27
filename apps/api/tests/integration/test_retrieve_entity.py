"""实体检索通道：``person_bridge`` 词法投影 + ``RetrieveEntity``。

对应 1.0 ``backend/retrieval/entity.py:1-71`` 的 ``EntityRetriever`` 与
``retrieval_indexes.py:180`` 的 ``("person_bridge", "people")`` 那一条。

两件事一起验，因为它们是一件事的两半：

- **写侧**：确认一个人之后，词法投影里出现 ``field_type='person_bridge'``、
  ``normalized_value=<人名>`` 的行。1.0 写进去的是 ``str(dict)``（``_normalize`` 作用在
  ``{"entity_id":…}`` 上），按人名永远查不中——2.0 取名字，这条断言钉住它。
- **读侧**：``RetrieveEntity`` 先读投影、查不到退回直接扫观察的 ``people`` 快照。

**人条件是硬条件，不是加分**：这一路不参与「像不像」的打分，``raw_score`` 恒 1.0。
检索器也**从不宣布身份**——命中只说明「这个 asset 里出现过这个名字」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.retrieval.retrieve_entity import CHANNEL, RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.places.aliases import EMPTY_ALIASES
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.text_vectors import (
    SqliteTextVectorRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

SCOPE = "album1"
OTHER = "album2"
BASE = datetime(2017, 11, 5, 10, 0, tzinfo=UTC)
# 1.0 ``retrieval/entity.py:65-66`` 的离散分。
DISCRETE_SCORE = 1.0


class ScriptedEmbedder:
    """查询向量恒定；图片向量按资产名区分。这一条验的不是视觉，给它一个确定值就够。"""

    model_id = scheme.IMAGE_MODEL
    dimension = 4

    def embed_query(self, text: str) -> Sequence[float]:
        return [1.0, 0.0, 0.0, 0.0]

    def embed_image(self, path: Path) -> Sequence[float]:
        return [0.5, 0.5, 0.0, 0.0]


@pytest.fixture
def library(migrations_ready_db: Database, tmp_path: Path):
    """两张图、两条观察，都没有人物——人物由各条用例自己加。"""
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    entities = SqliteEntityRepository(migrations_ready_db)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)

    media = tmp_path / "media"
    media.mkdir()
    for asset_id, caption in (("asset_1", "和妈妈在公园"), ("asset_2", "院子里")):
        path = media / f"{asset_id}.jpg"
        path.write_bytes(f"{asset_id} 的字节".encode())
        assets.add(
            Asset(
                id=asset_id,
                scope_id=SCOPE,
                file_name=f"{asset_id}.jpg",
                media_type=MediaType.IMAGE,
                path=str(path),
                mime_type="image/jpeg",
                size_bytes=1,
                status=AssetStatus.PROCESSED,
                content_sha256=f"sha-{asset_id}",
                captured_at=BASE,
                captured_location=None,
                created_at=BASE,
                updated_at=BASE,
            )
        )
        observation = Observation(
            id=f"obs_{asset_id[-1]}",
            scope_id=SCOPE,
            asset_id=asset_id,
            source_type="import",
            captured_at=BASE,
            confidence=0.65,
            created_at=BASE,
            updated_at=BASE,
            caption=caption,
        )
        observations.add(observation)
        # 词法投影与导入路径同一个时机写（``ImportAssets`` 的最后一步）。
        index.refresh_from_observation(observation)
    return {
        "assets": assets,
        "observations": observations,
        "index": index,
        "entities": entities,
        "vectors": SqliteVectorRepository(migrations_ready_db),
        "text_vectors": SqliteTextVectorRepository(migrations_ready_db),
        "places": places,
    }


def confirm_person(library, name: str, observation_id: str) -> str:
    """建一个已确认的人，并把提及写到那条观察上（这是 ``people_json`` 的唯一写入者）。"""
    entity = library["entities"].create_person(name, status="confirmed", scope_id=SCOPE)
    library["entities"].link_confirmed_mention(entity.id, observation_id)
    return entity.id


def person_bridge_rows(db: Database, name: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT field_type, normalized_value, source_type FROM observation_search_terms"
                " WHERE field_type = 'person_bridge' AND normalized_value = ?",
                (name,),
            ).fetchall()
        ]


def retriever(library) -> RetrieveEntity:
    return RetrieveEntity(index=library["index"], observations=library["observations"])


# ---- 写侧：投影里存的是名字 --------------------------------------------------------


def test_confirming_a_person_projects_the_name_not_the_raw_snapshot(library, migrations_ready_db: Database):
    """``people_json`` 里是 ``{"entity_id":…, "name":…}``，投影里必须是**名字**。

    1.0 在这里写的是 ``str(dict)``（``_normalize`` 作用在整条 dict 上），于是
    ``WHERE normalized_value = '妈妈'`` 永远查不中——它那条路是靠 ``EntityRetriever``
    的兜底在跑。断言的是 ``normalized_value`` 的**值**：只断言「有 person_bridge 行」
    的话，写进去一串 dict 文本照样是绿的。
    """
    confirm_person(library, "妈妈", "obs_1")
    observation = library["observations"].get("obs_1")
    assert observation is not None
    assert observation.people == ("妈妈",)

    library["index"].refresh_from_observation(observation)

    assert person_bridge_rows(migrations_ready_db, "妈妈") == [
        {"field_type": "person_bridge", "normalized_value": "妈妈", "source_type": "observation"}
    ]


# ---- 读侧：先投影、后兜底 ----------------------------------------------------------


def test_the_retriever_hits_the_projection(library):
    """投影建好之后，按人名就找得到那张照片。"""
    confirm_person(library, "妈妈", "obs_1")
    library["index"].refresh_from_observation(library["observations"].get("obs_1"))

    hits = retriever(library).execute(people=["妈妈"], scope_id=SCOPE, limit=10)

    assert [(hit.asset_id, hit.channel, hit.rank) for hit in hits] == [("asset_1", CHANNEL, 1)]


def test_the_retriever_falls_back_to_the_observation_snapshot(library):
    """**投影没建起来时**（老库、投影重建中途）退回直接读观察的 ``people``。

    这条兜底不是可选项：投影是派生数据，观察上的快照才是权威的那一份。
    """
    confirm_person(library, "妈妈", "obs_1")
    # 故意**不**刷投影。

    hits = retriever(library).execute(people=["妈妈"], scope_id=SCOPE, limit=10)

    assert [hit.asset_id for hit in hits] == ["asset_1"]


def test_a_name_nobody_has_returns_nothing(library):
    """**对照臂**：投影与观察都没有这个人时返回空表，不是「返回全部」。"""
    confirm_person(library, "妈妈", "obs_1")

    assert retriever(library).execute(people=["邻居"], scope_id=SCOPE, limit=10) == []


def test_a_prefix_of_a_name_does_not_match(library):
    """两条路都按**整串**匹配：人名是短词，「妈」不该把「妈妈」的照片交出来。"""
    confirm_person(library, "妈妈", "obs_1")

    assert retriever(library).execute(people=["妈"], scope_id=SCOPE, limit=10) == []


def test_the_scope_is_a_hard_filter(library):
    """**对照臂**：换一个空间，同一个人名什么都不该召回——包括兜底那条路。"""
    confirm_person(library, "妈妈", "obs_1")
    library["index"].refresh_from_observation(library["observations"].get("obs_1"))

    assert retriever(library).execute(people=["妈妈"], scope_id=OTHER, limit=10) == []


def test_several_people_are_an_union_not_an_intersection(library):
    """两个人名取并集：1.0 的 ``candidates |= ...``。「谁和谁的合影」不是这一层的语义。"""
    confirm_person(library, "妈妈", "obs_1")
    confirm_person(library, "爸爸", "obs_2")

    hits = retriever(library).execute(people=["妈妈", "爸爸"], scope_id=SCOPE, limit=10)

    assert [hit.asset_id for hit in hits] == ["asset_1", "asset_2"]
    assert [hit.rank for hit in hits] == [1, 2]


def test_the_hits_are_discrete_candidates_not_similarities(library):
    """这一路的分数不是「像不像」：``raw_score`` 恒 1.0、``score_kind`` 是 ``discrete``。

    融合只消费 ``rank``（见 ``domain/retrieval/models.py``），把这一路的原始分算成
    相似度会让它在展示与探针校准里与余弦混起来。
    """
    confirm_person(library, "妈妈", "obs_1")

    hit = retriever(library).execute(people=["妈妈"], scope_id=SCOPE, limit=10)[0]

    assert hit.raw_score == DISCRETE_SCORE
    assert hit.score_kind == "discrete"
    assert hit.higher_is_better is True


def test_a_limit_of_zero_returns_nothing(library):
    """边界：``limit=0`` 是「要 0 个」，不是 1.0 那个「先 append 再判上限」的一格。"""
    confirm_person(library, "妈妈", "obs_1")

    assert retriever(library).execute(people=["妈妈"], scope_id=SCOPE, limit=0) == []


# ---- 进融合：entity 是 anchor 通道 --------------------------------------------------


class ScriptedTextEmbedder:
    """文本通道的假嵌入器。

    这个夹具里不写任何文本向量，所以它返回什么都不会命中——它的作用只是让
    ``RetrieveTextAnn`` 能构造起来并且**不报降级**（返回空表会被判成
    ``embedder_unavailable``，那会让 ``degraded`` 字段在被测的链路上失真）。
    维度与视觉那个不同（3 vs 4）是有意的：共用同一个假件、维度又写成一样的话，
    「查错空间」这件事测不出来。
    """

    model_id = scheme.TEXT_MODEL
    dimension = 3

    def embed_query(self, text: str) -> Sequence[float]:
        return [1.0, 0.0, 0.0]


def build_evidence(library) -> RetrieveEvidence:
    return RetrieveEvidence(
        visual=RetrieveVisual(embedder=ScriptedEmbedder(), vectors=library["vectors"], index=None),
        entity=retriever(library),
        lexical=RetrieveLexical(index=library["index"]),
        metadata=RetrieveMetadata(
            assets=library["assets"],
            places=library["places"],
            aliases=EMPTY_ALIASES,
            recognize_place=lambda _asset_id: False,
        ),
        text=RetrieveTextAnn(embedder=ScriptedTextEmbedder(), vectors=library["text_vectors"]),
    )


def test_a_person_condition_reaches_the_fused_result_as_an_anchor(library):
    """带人物条件的检索里，候选的 ``channels`` 里有 ``entity``，且拿到 anchor 加成。

    这张图的 caption 里也有「妈妈」，所以 entity 与 lexical 两条都命中它。分数逐项可算：

        entity 1.0/61 + lexical 1.0/61 + anchor 加成 1.0

    **加成那 1.0 才是重点**：``fuse`` 只看候选**第一次**被哪条通道召回。``entity``
    若排到 ``lexical`` 之后，这个候选的 ``evidence_class`` 就是 ``semantic``，分数只剩
    ``2.0/61``——差一个数量级，而「命中列表里有没有 entity」这种断言两种排法都会绿。
    """
    confirm_person(library, "妈妈", "obs_1")

    result = build_evidence(library).execute(
        EvidenceRequest(text="妈妈", scope_id=SCOPE, limit=10, person=("妈妈",))
    )

    top = result.hits[0]
    assert top.asset_id == "asset_1"
    assert dict(top.channels) == {"entity": 1, "lexical": 1}
    assert top.raw_scores["entity"] == DISCRETE_SCORE
    assert top.score == pytest.approx(1.0 + (1.0 + 1.0) / 61)


def test_without_a_person_condition_the_entity_channel_is_silent(library):
    """**对照臂**：同一次查询、去掉人物条件，融合结果里就不该有 ``entity`` 这一路。"""
    confirm_person(library, "妈妈", "obs_1")

    result = build_evidence(library).execute(EvidenceRequest(text="妈妈", scope_id=SCOPE, limit=10))

    assert all("entity" not in hit.channels for hit in result.hits)
