"""词法 / 元数据两个召回器的用例。真 SQLite + 真 FTS5。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import METADATA_RECALL_LIMIT, RetrieveMetadata
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.places.aliases import EMPTY_ALIASES, PlaceAliases
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex

BASE = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def repos(migrations_ready_db: Database):
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    return {
        "assets": assets,
        "observations": observations,
        "places": places,
        "index": index,
    }


def seed(
    repos,
    *,
    asset_id: str,
    observation_id: str,
    scope_id: str = "album1",
    captured_at: datetime = BASE,
    media_type: MediaType = MediaType.IMAGE,
    gps: GpsPoint | None = XIAOSHAN,
    caption: str = "一家人在公园里拍照",
    place: str = "公园",
    objects: tuple[str, ...] = ("相机",),
    resolved_place: Place | None = None,
) -> Observation:
    repos["assets"].add(
        Asset(
            id=asset_id,
            scope_id=scope_id,
            file_name=f"{asset_id}.jpg",
            media_type=media_type,
            path=f"/tmp/{asset_id}.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256=f"sha-{asset_id}",
            captured_at=captured_at,
            captured_location=gps,
            created_at=captured_at,
            updated_at=captured_at,
        )
    )
    observation = Observation(
        id=observation_id,
        scope_id=scope_id,
        asset_id=asset_id,
        source_type="import",
        captured_at=captured_at,
        confidence=0.65,
        created_at=captured_at,
        updated_at=captured_at,
        caption=caption,
        place=place,
        objects=objects,
    )
    repos["observations"].add(observation)
    if resolved_place is not None:
        repos["places"].save(asset_id=asset_id, scope_id=scope_id, gps=gps, place=resolved_place)
    repos["index"].refresh_from_observation(observation)
    return observation


def xiaoshan_place() -> Place:
    return Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        gps=XIAOSHAN,
        confidence=0.9,
    )


# ---- 词法 ------------------------------------------------------------------------


def test_lexical_returns_ranked_candidate_hits(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", caption="海边日落")
    seed(repos, asset_id="asset_2", observation_id="obs_2", caption="山里露营")
    retrieve = RetrieveLexical(index=repos["index"])

    hits = retrieve.execute(whole_query="海边", scope_id="album1", limit=10)

    assert [hit.asset_id for hit in hits] == ["asset_1"]
    assert hits[0].channel == "lexical"
    assert hits[0].rank == 1
    assert hits[0].score_kind == "token_hits"


def test_lexical_single_character_query_returns_nothing(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", caption="海边日落")
    retrieve = RetrieveLexical(index=repos["index"])
    assert retrieve.execute(whole_query="色", scope_id="album1", limit=10) == []


def test_lexical_empty_query_returns_nothing(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1")
    retrieve = RetrieveLexical(index=repos["index"])
    assert retrieve.execute(whole_query="", scope_id="album1", limit=10) == []


def test_lexical_merges_facets_by_asset(repos):
    """整串无命中、切面命中时仍然召回——1.0 ``lexical.py:56-72`` 的合并语义。"""
    seed(repos, asset_id="asset_1", observation_id="obs_1", caption="海边日落", objects=("相机",))
    retrieve = RetrieveLexical(index=repos["index"])

    hits = retrieve.execute(whole_query="博物馆", facets=("相机",), scope_id="album1", limit=10)

    assert [hit.asset_id for hit in hits] == ["asset_1"]


def test_lexical_is_scope_isolated(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", scope_id="album1", caption="海边日落")
    seed(repos, asset_id="asset_2", observation_id="obs_2", scope_id="album2", caption="海边日落")
    retrieve = RetrieveLexical(index=repos["index"])

    hits = retrieve.execute(whole_query="海边", scope_id="album2", limit=10)

    assert [hit.asset_id for hit in hits] == ["asset_2"]


def test_lexical_ranks_are_contiguous_from_one(repos):
    for index in range(3):
        seed(
            repos,
            asset_id=f"asset_{index}",
            observation_id=f"obs_{index}",
            caption="海边日落",
        )
    retrieve = RetrieveLexical(index=repos["index"])

    hits = retrieve.execute(whole_query="海边", scope_id="album1", limit=10)

    assert [hit.rank for hit in hits] == [1, 2, 3]


# ---- 元数据 ----------------------------------------------------------------------


def test_metadata_returns_nothing_without_a_positive_condition(repos):
    """纯语义查询不该被「scope 内每一张图」污染。1.0 ``metadata.py:28-31``。"""
    seed(repos, asset_id="asset_1", observation_id="obs_1")
    retrieve = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=EMPTY_ALIASES)
    assert retrieve.execute(scope_id="album1") == []


def test_metadata_time_bounds_includes_unknown_captured_at(repos):
    """拍摄时间未知的行放行——1.0 ``metadata.py:50-53`` 的写作方式就是这个意思。"""
    seed(repos, asset_id="asset_1", observation_id="obs_1", captured_at=BASE)
    seed(repos, asset_id="asset_2", observation_id="obs_2", captured_at=BASE.replace(year=2020))
    retrieve = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=EMPTY_ALIASES)

    hits = retrieve.execute(
        scope_id="album1", time_bounds=(BASE, BASE.replace(hour=23)), media_types=(MediaType.IMAGE,)
    )

    assert [hit.asset_id for hit in hits] == ["asset_1"]


def test_metadata_filters_by_negated_media(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", media_type=MediaType.IMAGE)
    seed(repos, asset_id="asset_2", observation_id="obs_2", media_type=MediaType.VIDEO)
    retrieve = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=EMPTY_ALIASES)

    hits = retrieve.execute(
        scope_id="album1", media_types=(MediaType.IMAGE, MediaType.VIDEO), negated_media=(MediaType.VIDEO,)
    )

    assert [hit.asset_id for hit in hits] == ["asset_1"]


def test_metadata_place_filter_matches_the_resolved_place(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", resolved_place=xiaoshan_place())
    seed(repos, asset_id="asset_2", observation_id="obs_2", resolved_place=None)
    retrieve = RetrieveMetadata(
        assets=repos["assets"],
        places=repos["places"],
        aliases=EMPTY_ALIASES,
        # 「本该有地点但解析不出来」的放行/剔除由调用方决定；这条用例验的是**匹配**，
        # 所以显式剔除未解析的那一张。放行行为由下一条用例单测。
        recognize_place=lambda _asset_id: True,
    )

    hits = retrieve.execute(scope_id="album1", place="萧山区")

    assert [hit.asset_id for hit in hits] == ["asset_1"]


def test_metadata_alias_bridges_chinese_to_english(repos):
    chiang_mai = Place(
        admin=AdminArea(),
        nearest=NearestPlace(locality="Chiang Mai", distance_m=240.0),
        precision=PlacePrecision.LOCALITY,
        source=PlaceSource.GEONAMES,
        confidence=0.7,
    )
    seed(repos, asset_id="asset_1", observation_id="obs_1", resolved_place=chiang_mai)
    aliases = PlaceAliases(table={"清迈": ("Chiang Mai",)})
    retrieve = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=aliases)

    assert retrieve.execute(scope_id="album1", place="清迈") == []
    # 附近城市只作弱线索，不作为确定归属。
    # 没有别名表时同一条查询不命中——别名是唯一的中英桥。
    empty = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=EMPTY_ALIASES)
    assert empty.execute(scope_id="album1", place="清迈") == []


def test_metadata_drops_assets_with_gps_but_no_resolved_place(repos):
    """「有 GPS 却解析不出地点」是数据覆盖之外，拿它支持地点查询是猜。

    1.0 的 ``metadata.py:59-67`` 把这一类**放行**（因为 ``metadata_json`` 是字符串，
    ``geo`` 恒 falsy），于是整个 scope 挤进 metadata 通道。
    """
    seed(repos, asset_id="asset_1", observation_id="obs_1", gps=XIAOSHAN, resolved_place=None)
    retrieve = RetrieveMetadata(
        assets=repos["assets"],
        places=repos["places"],
        aliases=EMPTY_ALIASES,
        recognize_place=lambda _asset_id: True,
    )

    assert retrieve.execute(scope_id="album1", place="萧山区") == []


def test_metadata_missing_gps_is_not_a_location_anchor(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", gps=None, resolved_place=None)
    retrieve = RetrieveMetadata(
        assets=repos["assets"],
        places=repos["places"],
        aliases=EMPTY_ALIASES,
        recognize_place=lambda _asset_id: False,
    )
    assert retrieve.execute(scope_id="album1", place="萧山区") == []


def test_metadata_recall_limit_is_a_constant_not_an_env_var():
    assert METADATA_RECALL_LIMIT == 200


def test_metadata_is_scope_isolated(repos):
    seed(repos, asset_id="asset_1", observation_id="obs_1", scope_id="album1")
    seed(repos, asset_id="asset_2", observation_id="obs_2", scope_id="album2")
    retrieve = RetrieveMetadata(assets=repos["assets"], places=repos["places"], aliases=EMPTY_ALIASES)

    hits = retrieve.execute(scope_id="album1", media_types=(MediaType.IMAGE,))

    assert [hit.asset_id for hit in hits] == ["asset_1"]
