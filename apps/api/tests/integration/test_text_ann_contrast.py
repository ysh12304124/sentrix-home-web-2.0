"""T117：``text_ann`` 这条通道的**对照验收**——证明它真的在贡献，而不是「接上了」。

S6 补的六路里，``text_ann`` 是唯一一条「补齐前整个不存在」的（T115a–d 四段：端口、
adapter、写入侧、召回器）。它接上之后，**「接上了」与「起作用了」在读数上是两件事**：
通道存在、trace 里也写着 ``ok``，但它召回的名次如果深到融合之后翻不上来，
那就等于没接——而这正是 §4 那段「三个对照验收」要挡的东西。

所以这份用例造一张**只有文本通道能召回**的图，然后断言两半：

1. **它在结果里，而且文本通道给它的名次是 1**（不是垫底）。名次是 1 才说明这一路
   给的是**信号**；垫底意味着它召回了一堆东西、但没把任何一张抬上来；
2. **把文本向量抽掉，它就从结果里消失**。没有这一半的话，一个恰好被别的通道也召回到
   的图会让断言永远为真——那时用例量的是别的通道，不是这一条。

对照的两臂共用同一个夹具，只差**那一行文本向量**。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.places.aliases import EMPTY_ALIASES
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.text_vectors import (
    SqliteTextVectorRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

SCOPE = "album-t117"
BASE = datetime(2019, 7, 22, 12, 0, tzinfo=UTC)

# 视觉查询向量与两张图的视觉向量。**B 根本没有视觉向量**（见夹具），
# 所以它要能被召回就只剩文本那一条路。
VISUAL_QUERY = [1.0, 0.0, 0.0, 0.0]
VISUAL_NEAR = [0.95, 0.31, 0.0, 0.0]
VISUAL_FAR = [0.0, 0.0, 0.0, 1.0]

# 文本查询向量。B 的文本向量与它**方向相同**（内积 1.0），C 的只对上一半（0.5）——
# 于是 B 在文本通道里是**第一名**、C 是第二名。**要有两个候选，「名次不是垫底」这句
# 才有区分力**：只有一个候选时它恒等于 1，把排序反过来也照样绿（第一版就是这样）。
TEXT_QUERY = [1.0, 0.0, 0.0]
# ``TEXT_OF_B`` 是 B 在 **episodic** 空间那一行的向量（强）；它在 semantic 那一行很弱
# （``TEXT_OF_B_WEAK``）。**同一个 asset 在两个空间里分数不同**，是真实数据的样子
# （写入侧一条观察写三行），也是「按 asset 取最大相似度」那句话唯一有区分力的形状：
# 只有一行时「取最大」与「取第一个」算出来完全一样，那条行为就测不到。
TEXT_OF_B = [1.0, 0.0, 0.0]
TEXT_OF_B_WEAK = [0.1, 0.995, 0.0]
TEXT_OF_C = [0.5, 0.5, 0.0]


class ScriptedVisualEmbedder:
    model_id = scheme.IMAGE_MODEL
    dimension = 4

    def __init__(self, images: dict[bytes, Sequence[float]]) -> None:
        self._images = images

    def embed_query(self, text: str) -> Sequence[float]:
        return VISUAL_QUERY

    def embed_image(self, path: Path) -> Sequence[float]:
        return self._images[Path(path).read_bytes()]


class ScriptedTextEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 3

    def embed_query(self, text: str) -> Sequence[float]:
        return TEXT_QUERY


@pytest.fixture
def library(migrations_ready_db, tmp_path: Path):
    """四张图。``asset_b`` **没有视觉向量**、caption 与查询词也不相干——
    所以**只有文本通道能把它抬上来**；``asset_d`` 更进一步，只有 **episodic** 那一侧
    能召回到它。"""
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    vectors = SqliteVectorRepository(migrations_ready_db)
    text_vectors = SqliteTextVectorRepository(migrations_ready_db)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)

    media = tmp_path / "media"
    media.mkdir()
    payloads: dict[bytes, Sequence[float]] = {}
    # ``asset_d`` 的 caption 里**有**查询词，但词法通道要的是整体匹配——留一个不命中的
    # 说法，免得这一条变成在验词法。

    # 三张图的 caption 与查询词都不相干——**词法通道也够不到 asset_b**，
    # 否则这一条验的就不是文本通道了。
    captions = {
        "asset_a": "雪山",
        "asset_b": "室内",
        "asset_c": "街道",
        # 第四张：**只有 episodic 空间的文本向量**（见 ``write_text_vectors``）。
        # 它的用途是让「两个空间都查」这条有区分力——少了它，把 ``SPACES`` 砍成一个
        # 也照样召回得到，而 1.0 的写入侧是**两个空间都写**的
        # （``index_observation_text`` 那条写三行：episodic/observation、
        # episodic/event、semantic/observation）。漏查一个空间会静默丢掉事件级召回。
        "asset_d": "海边的房子",
    }
    # ``asset_b`` **没有视觉向量**——它是「嵌入失败/跳过」的真实状态（``index_visual``
    # 对空向量不写行）。不用「视觉向量离得远」是因为**视觉通道根本不过滤相关性**：
    # 2.0 的 S2 没有相关性阈值，``sqlite_exact`` 回退档会把**这个空间里的全部向量**
    # 按相似度排完返回。离得远只让它的名次靠后，不会让它消失——那样对照臂就废了
    # （第一版就是这么写的，跑出来 asset_b 照样在结果里）。
    # ``asset_d`` 也不写视觉向量——它要验的是文本通道的**另一个空间**。
    with_visual = ("asset_a", "asset_c")
    for asset_id, caption in captions.items():
        payload = f"{asset_id} 的字节".encode()
        path = media / f"{asset_id}.jpg"
        path.write_bytes(payload)
        payloads[payload] = VISUAL_NEAR if asset_id == "asset_a" else VISUAL_FAR
        assets.add(
            Asset(
                id=asset_id,
                scope_id=SCOPE,
                file_name=f"{asset_id}.jpg",
                media_type=MediaType.IMAGE,
                path=str(path),
                mime_type="image/jpeg",
                size_bytes=len(payload),
                status=AssetStatus.PROCESSED,
                content_sha256=f"sha-{asset_id}",
                captured_at=BASE,
                captured_location=None,
                created_at=BASE,
                updated_at=BASE,
            )
        )
        observation = Observation(
            id=f"obs_{asset_id}",
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
        if asset_id in with_visual:
            vectors.upsert(
                VectorRecord(
                    scope_id=SCOPE,
                    space=scheme.VISUAL_SPACE,
                    source_type="asset",
                    source_id=asset_id,
                    model_name=scheme.IMAGE_MODEL,
                    vector=tuple(payloads[payload]),
                )
            )
        index.refresh_from_observation(observation)

    return {
        "assets": assets,
        "places": places,
        "observations": observations,
        "index": index,
        "vectors": vectors,
        "text_vectors": text_vectors,
        "media": media,
        "payloads": payloads,
    }


def write_text_vectors(library) -> None:
    """给两条观察各写一行 semantic 空间的文本向量。

    **这就是那两臂唯一的差别。** 写入侧（T115b）写的是同一形状的行：
    ``source_type='observation'``、``metadata`` 里带 ``asset_id``
    （``RetrieveTextAnn._asset_of`` 只认这一个键，见它的 docstring）。

    写两行而不是一行：``asset_b`` 是这一路的第一名、``asset_c`` 是第二名。
    只有一个候选时「名次不是垫底」恒真，排序写反也测不出来。
    """
    rows = [
        # B 在 semantic 里只对上一成——它在 episodic 那一行才是强的。两行都必须写：
        # 少了弱的那一行，「按 asset 取最大」就退化成「取第一个」也看不出来。
        ("asset_b", scheme.SEMANTIC_SPACE, TEXT_OF_B_WEAK),
        ("asset_b", scheme.EPISODIC_SPACE, TEXT_OF_B),
        ("asset_c", scheme.SEMANTIC_SPACE, TEXT_OF_C),
        # **这条只在 episodic 空间里。** 写入侧真写三行（episodic/observation、
        # episodic/event、semantic/observation，T115b），这里只留最必要的一行：
        # 它在 semantic 里没有对应行，所以「两个空间都查」一旦被砍成一个，它就丢了。
        ("asset_d", scheme.EPISODIC_SPACE, TEXT_OF_C),
    ]
    for asset_id, space, vector in rows:
        library["text_vectors"].upsert(
            VectorRecord(
                scope_id=SCOPE,
                space=space,
                source_type="observation",
                source_id=f"obs_{asset_id}",
                model_name=scheme.TEXT_MODEL,
                vector=tuple(vector),
                metadata={"asset_id": asset_id},
            )
        )


def build(library) -> RetrieveEvidence:
    return RetrieveEvidence(
        visual=RetrieveVisual(
            embedder=ScriptedVisualEmbedder(library["payloads"]),
            vectors=library["vectors"],
            index=None,
        ),
        lexical=RetrieveLexical(index=library["index"]),
        metadata=RetrieveMetadata(
            assets=library["assets"],
            places=library["places"],
            aliases=EMPTY_ALIASES,
            recognize_place=lambda asset_id: False,
        ),
        entity=RetrieveEntity(index=library["index"], observations=library["observations"]),
        text=RetrieveTextAnn(embedder=ScriptedTextEmbedder(), vectors=library["text_vectors"]),
    )


def ask(library):
    return build(library).execute(EvidenceRequest(text="海边", scope_id=SCOPE, limit=6))


def channels_of(result, asset_id: str) -> dict[str, int]:
    for hit in result.hits:
        if hit.asset_id == asset_id:
            return dict(hit.channels)
    return {}


# ---- 两臂 --------------------------------------------------------------------------


def test_without_the_text_vector_that_photo_is_unreachable(library):
    """**对照臂。** 没有那一行文本向量时 ``asset_b`` 三路都够不到它：没有视觉向量、
    caption 与查询词不相干、没有人物条件、没有地点/时间条件。所以它要出现，
    **只可能**是文本通道抬上来的。
    """
    result = ask(library)

    delivered = [hit.asset_id for hit in result.hits]
    assert "asset_b" not in delivered, "没有文本向量它就该够不到"
    assert "asset_a" in delivered, "前提：夹具里至少有别的图是能被召回的"


def test_with_the_text_vector_it_is_recalled_at_rank_one(library):
    """**这一条就是 T117 的判据。** 两半一起断言：

    - ``channels`` 里出现了 ``text_ann`` 键 —— 这一路真的贡献了候选；
    - 它给这张图的名次是 **1** —— 不是垫底。名次深到融合翻不上来的话，
      通道「接上了」与「起作用了」在结果上就没有区别，而那正是这条验收要挡的。
    """
    write_text_vectors(library)

    result = ask(library)

    channels = channels_of(result, "asset_b")
    assert "text_ann" in channels, "文本通道没把 asset_b 抬上来——这一路等于没接"
    assert channels["text_ann"] == 1, f"文本通道给它的是垫底名次：{channels}"


def test_a_photo_keeps_its_best_row_across_spaces(library):
    """**按 asset 取最大相似度**（1.0 的行为，见 ``_recall`` 的 docstring）。

    B 在两个空间里各有一行，strong 的那行在 episodic。判定要取两者中更大的那个——
    取「第一个碰上的」（semantic 那行，很弱）会让 B 掉到 C 后面，而真实数据里
    一条观察本来就写三行、分数各不相同。
    """
    write_text_vectors(library)

    result = ask(library)

    assert channels_of(result, "asset_b")["text_ann"] == 1, "取的不是最大那一行"


def test_both_text_spaces_are_queried(library):
    """**两个空间都要查**（1.0 ``text_ann.py:26`` 的 ``spaces``）。

    ``asset_d`` 的文本向量**只写在 episodic 空间**里，semantic 里没有对应行——
    所以「只查一个空间」的实现会把它整个丢掉，而那是静默的：结果少一张图，
    没有任何报错。1.0 的写入侧两个空间都写（一个语义、一个事件），
    查询侧少查一个就等于把那条写入白做。
    """
    write_text_vectors(library)

    result = ask(library)

    assert "text_ann" in channels_of(result, "asset_d"), "只查了 semantic 空间——episodic 那一侧被丢了"


def test_the_channel_ranks_by_similarity_not_by_insertion_order(library):
    """同一个通道里两个候选的**相对名次**要按相似度定。

    上面那条只断言「B 是 1」——如果通道实际上按插入顺序排，而 B 恰好先插入，
    它也照样是 1。这一条把两个候选的相对位置一起钉住：B 的文本向量与查询同向
    （内积 1.0）、C 的只对上一半（0.5），所以 B 必须排在 C 前面。
    """
    write_text_vectors(library)

    result = ask(library)

    channels = channels_of(result, "asset_b")
    other = channels_of(result, "asset_c")
    assert channels["text_ann"] == 1
    assert other["text_ann"] == 2


def test_the_trace_confirms_the_channel_ran_and_contributed(library):
    """与 T125 的 trace 对上：这一路 ``ok``、有候选、报了后端。

    两处判据是互补的：``channels`` 看的是**融合之后**的贡献，trace 看的是**这一路自己**
    跑了没有。只有前者时，一个「根本没跑但恰好别处也召回到了」的实现看不出来。
    """
    write_text_vectors(library)

    result = ask(library)

    trace = next(item for item in result.channel_trace if item.channel == "text_ann")
    assert trace.status == "ok"
    assert trace.candidate_count >= 1
    assert trace.backend == "sqlite_exact"
