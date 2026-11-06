"""真 SQLite + 真迁移下的 S4 仓储：地点 / 事件 / 事实 / 词法投影。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.memories.facts import Fact, FactStatus
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def assets(migrations_ready_db: Database) -> SqliteAssetRepository:
    return SqliteAssetRepository(migrations_ready_db)


@pytest.fixture
def observations(migrations_ready_db: Database) -> SqliteObservationRepository:
    return SqliteObservationRepository(migrations_ready_db)


@pytest.fixture
def places(migrations_ready_db: Database) -> SqliteAssetPlaceRepository:
    return SqliteAssetPlaceRepository(migrations_ready_db)


@pytest.fixture
def events(migrations_ready_db: Database) -> SqliteEventRepository:
    return SqliteEventRepository(migrations_ready_db)


@pytest.fixture
def facts(migrations_ready_db: Database) -> SqliteFactRepository:
    return SqliteFactRepository(migrations_ready_db)


def add_asset(repo: SqliteAssetRepository, asset_id: str = "asset_1", **overrides) -> Asset:
    fields: dict[str, object] = {
        "id": asset_id,
        "scope_id": "album1",
        "file_name": f"{asset_id}.jpg",
        "media_type": MediaType.IMAGE,
        "path": f"/tmp/{asset_id}.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 10,
        "status": AssetStatus.PROCESSED,
        "content_sha256": f"sha-{asset_id}",
        "captured_at": NOW,
        "captured_location": XIAOSHAN,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    asset = Asset(**fields)  # type: ignore[arg-type]
    repo.add(asset)
    return asset


def add_observation(
    repo: SqliteObservationRepository, observation_id: str = "obs_1", **overrides
) -> Observation:
    fields: dict[str, object] = {
        "id": observation_id,
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
        "ocr_text": "",
        "objects": ("相机", "长椅"),
        "clothing": ("外套",),
        "revision": 1,
    }
    fields.update(overrides)
    observation = Observation(**fields)  # type: ignore[arg-type]
    repo.add(observation)
    return observation


# ---- 地点 ------------------------------------------------------------------------


def test_place_round_trips_all_two_slots(places: SqliteAssetPlaceRepository, assets):
    add_asset(assets)
    value = Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        gps=XIAOSHAN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=value)

    stored = places.get("asset_1")
    assert stored is not None
    assert stored.admin == AdminArea("浙江省", "杭州市", "萧山区")
    assert stored.nearest == NearestPlace("赵州镇", "大石桥村", 270.0)
    assert stored.precision is PlacePrecision.DISTRICT
    assert stored.source is PlaceSource.PYGEOCN
    assert stored.gps == XIAOSHAN
    assert stored.display() == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"


def test_place_save_is_idempotent_and_corrective(places: SqliteAssetPlaceRepository, assets):
    """重复解析同一个 asset 只覆盖那一行——地点是属性，不是事件流。"""
    add_asset(assets)
    coarse = Place(
        admin=AdminArea("浙江省", "杭州市", ""),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    fine = Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=coarse)
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=fine)

    assert places.count() == 1
    stored = places.get("asset_1")
    assert stored is not None and stored.admin.district == "萧山区"


def test_place_absent_is_distinguishable_from_empty_resolution(places: SqliteAssetPlaceRepository, assets):
    """「没解析过」没有行；「解析过」有行。1.0 把两者都是空字符串。"""
    add_asset(assets)
    assert places.get("asset_1") is None
    assert places.count() == 0


def test_place_get_many_returns_only_known(places: SqliteAssetPlaceRepository, assets):
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    value = Place(
        admin=AdminArea("浙江省"),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=None, place=value)

    found = places.get_many(["asset_1", "asset_2"])
    assert set(found) == {"asset_1"}


# ---- 事件 ------------------------------------------------------------------------


def test_event_create_and_fold(events: SqliteEventRepository):
    created = events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="拍照",
        summary="一家人在公园里拍照",
        confidence=0.65,
        aggregation_score=0.0,
        aggregation_breakdown={"selected": False, "ambiguity": False},
    )
    assert created.title == "待总结事件"
    assert created.revision == 1

    folded = events.fold_observation(
        "evt_1",
        time_start=NOW.replace(hour=14),
        time_end=NOW.replace(hour=14),
        place="",
        admin_district="",
        activity="",
        summary="孩子在放风筝",
        confidence=0.9,
        aggregation_score=0.72,
        aggregation_breakdown={"total": 0.72},
    )
    assert folded.time_end == NOW.replace(hour=14)
    assert folded.revision == 2
    assert folded.confidence == pytest.approx(0.9)
    assert "孩子在放风筝" in folded.summary
    assert folded.aggregation_score == pytest.approx(0.72)


def test_event_fold_never_lowers_confidence(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.9,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    folded = events.fold_observation(
        "evt_1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.1,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert folded.confidence == pytest.approx(0.9)


def test_event_summary_is_not_appended_twice(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="拍照",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    once = events.fold_observation(
        "evt_1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="拍照",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert once.summary == "拍照"


def test_event_merge_moves_observations_and_deletes_source(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    add_observation(observations, "obs_1", asset_id="asset_1")
    add_observation(observations, "obs_2", asset_id="asset_2")
    for event_id in ("evt_1", "evt_2"):
        events.create(
            event_id=event_id,
            scope_id="album1",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )
    events.attach("evt_1", "obs_1")
    events.attach("evt_2", "obs_2")

    events.merge("evt_2", "evt_1")

    assert events.get("evt_2") is None
    assert list(events.observation_ids("evt_1")) == ["obs_1", "obs_2"]


def test_event_merge_skips_already_shared_observations(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets)
    add_observation(observations)
    for event_id in ("evt_1", "evt_2"):
        events.create(
            event_id=event_id,
            scope_id="album1",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )
    events.attach("evt_1", "obs_1")
    events.attach("evt_2", "obs_1")

    events.merge("evt_2", "evt_1")

    assert list(events.observation_ids("evt_1")) == ["obs_1"]


def test_event_merge_to_self_is_a_noop(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.merge("evt_1", "evt_1")
    assert events.get("evt_1") is not None


def test_event_list_is_scoped_and_active_only(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.create(
        event_id="evt_2",
        scope_id="album2",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert [item.id for item in events.list_active("album1", limit=10)] == ["evt_1"]
    assert [item.id for item in events.list_by_scope("album1", limit=10)] == ["evt_1"]


def test_event_dissolve_removes_links_then_row(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets)
    add_observation(observations)
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.attach("evt_1", "obs_1")
    events.dissolve("evt_1")
    assert events.get("evt_1") is None
    assert list(events.observation_ids("evt_1")) == []


def test_event_fold_on_missing_event_raises(events: SqliteEventRepository):
    with pytest.raises(KeyError):
        events.fold_observation(
            "missing",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )


# ---- 事实 ------------------------------------------------------------------------


def test_fact_insert_reinforce_and_status(facts: SqliteFactRepository):
    created = facts.insert(
        Fact(
            id="fact_1",
            scope_id="album1",
            subject="妈妈",
            predicate="去过",
            object="赵县",
            status=FactStatus.ACTIVE,
            confidence=0.65,
            evidence_ids=("obs_1",),
            revision=1,
        )
    )
    assert created.evidence_ids == ("obs_1",)
    assert facts.active_for(scope_id="album1", subject="妈妈", predicate="去过") is not None

    reinforced = facts.reinforce("fact_1", evidence_ids=("obs_1", "obs_2"), confidence=0.8)
    assert reinforced.evidence_ids == ("obs_1", "obs_2")
    assert reinforced.confidence == pytest.approx(0.8)

    retracted = facts.set_status("fact_1", "retracted")
    assert retracted.status is FactStatus.RETRACTED
    assert facts.active_for(scope_id="album1", subject="妈妈", predicate="去过") is None


def test_supersede_others_is_scoped(facts: SqliteFactRepository):
    """1.0 的 ``confirm_fact`` 不带 scope，跨相册同名事实会被一起作废。2.0 收敛在本 scope。"""
    for scope, fact_id in (("album1", "fact_a"), ("album2", "fact_b")):
        facts.insert(
            Fact(
                id=fact_id,
                scope_id=scope,
                subject="妈妈",
                predicate="去过",
                object="赵县",
                status=FactStatus.ACTIVE,
                confidence=0.6,
                evidence_ids=("obs_1",),
                revision=1,
            )
        )
    facts.insert(
        Fact(
            id="fact_pending",
            scope_id="album1",
            subject="妈妈",
            predicate="去过",
            object="保定",
            status=FactStatus.PENDING,
            confidence=0.7,
            evidence_ids=("obs_9",),
            revision=2,
            supersedes_fact_id="fact_a",
        )
    )

    changed = facts.supersede_others(
        scope_id="album1", subject="妈妈", predicate="去过", keep_fact_id="fact_pending"
    )

    assert changed == 1
    assert facts.get("fact_a").status is FactStatus.SUPERSEDED
    assert facts.get("fact_b").status is FactStatus.ACTIVE


# ---- 词法投影 --------------------------------------------------------------------


def test_lexical_refresh_writes_terms_and_fts(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    add_asset(assets)
    observation = add_observation(observations)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)

    written = index.refresh_from_observation(observation)

    assert written > 0
    hits = dict(index.search_fts("拍照", scope_id="album1", limit=10))
    assert hits == {"asset_1": 1}


def test_lexical_single_character_query_returns_nothing(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    add_asset(assets)
    observation = add_observation(observations)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    index.refresh_from_observation(observation)

    assert index.search_fts("色", scope_id="album1", limit=10) == []


def test_lexical_exact_assets_requires_full_equality(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    add_asset(assets)
    observation = add_observation(observations, place="萧山区")
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    index.refresh_from_observation(observation)

    assert index.exact_assets("萧山区", scope_id="album1") == frozenset({"asset_1"})
    assert index.exact_assets("萧山", scope_id="album1") == frozenset()


def test_lexical_refresh_drops_terms_removed_by_re_enrichment(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    """重新富集把 objects 收敛掉之后，旧词条必须消失——增量更新做不到这件事。"""
    add_asset(assets)
    observation = add_observation(observations)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    index.refresh_from_observation(observation)
    assert index.search_fts("长椅", scope_id="album1", limit=10)

    from sentrix.domain.media.ports import ObservationSemantics

    enriched = observations.enrich(
        observation.id, semantics=ObservationSemantics(caption="只有相机", objects=("相机",))
    )
    index.refresh_from_observation(enriched)

    assert index.search_fts("长椅", scope_id="album1", limit=10) == []
    assert index.search_fts("相机", scope_id="album1", limit=10)


def test_lexical_rebuild_scope_does_not_wipe_other_scopes(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    """1.0 的 scoped rebuild 会先清空整张 FTS 再只补一个 scope。"""
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2", scope_id="album2")
    add_observation(observations, "obs_1", asset_id="asset_1", scope_id="album1")
    add_observation(observations, "obs_2", asset_id="asset_2", scope_id="album2", place="秦皇岛市")
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    index.rebuild_scope("album1")
    index.rebuild_scope("album2")

    index.rebuild_scope("album1")

    # 断言的是**另一个 scope 的行还在**，不是命中几个 token——「秦皇岛」会拆成
    # 整串 + 两个 bigram，具体命中数属于 tokenization 的事，不是这条用例要验的。
    hits = index.search_fts("秦皇岛", scope_id="album2", limit=10)
    assert hits and hits[0][0] == "asset_2"
    assert index.search_fts("秦皇岛", scope_id="album1", limit=10) == []


def test_lexical_count_is_scoped(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2", scope_id="album2")
    add_observation(observations, "obs_1", asset_id="asset_1", scope_id="album1")
    add_observation(observations, "obs_2", asset_id="asset_2", scope_id="album2")
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
    index.refresh_from_observation(observations.get("obs_1"))
    index.refresh_from_observation(observations.get("obs_2"))

    assert index.count("album1") > 0
    assert index.count("album1") < index.count()


def test_lexical_refresh_writes_the_asset_anchors(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    """观察文本里没有的地名 / 日期 / 文件名要单独建锚（1.0 ``retrieval_indexes.py:198-245``）。

    没有这三列，「按日期找照片」「按真实地名找照片」在词法通道上无从命中——``caption``
    是场景描述，``2017-10-05`` 与 ``萧山区`` 都不在里面。
    """
    add_asset(assets)
    add_observation(observations)
    places.save(
        asset_id="asset_1",
        scope_id="album1",
        gps=XIAOSHAN,
        place=Place(
            admin=AdminArea("浙江省", "杭州市", "萧山区"),
            nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
            precision=PlacePrecision.DISTRICT,
            source=PlaceSource.PYGEOCN,
            gps=XIAOSHAN,
            confidence=0.9,
        ),
    )
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)

    index.refresh_from_observation(observations.get("obs_1"))

    assert dict(index.search_fts("2017-10-05", scope_id="album1", limit=10))["asset_1"] > 0
    assert dict(index.search_fts("萧山区", scope_id="album1", limit=10))["asset_1"] > 0
    assert dict(index.search_fts("asset_1", scope_id="album1", limit=10))["asset_1"] > 0
    with migrations_ready_db.connect() as connection:
        rows = connection.execute(
            "SELECT field_type, source_type, normalized_value FROM observation_search_terms"
            " WHERE field_type IN ('geo', 'time', 'file')"
        ).fetchall()
    assert {(row["field_type"], row["source_type"]) for row in rows} == {
        ("geo", "asset_geo"),
        ("time", "asset_time"),
        ("file", "asset_file"),
    }
    # 坐标不许进这个词：1.0 的 ``label`` 在 PyGeoCN 那条路上可能退化成坐标串。
    assert all("," not in row["normalized_value"] for row in rows)


def test_without_a_resolved_place_there_is_no_geo_anchor(
    migrations_ready_db: Database, observations: SqliteObservationRepository, assets, places
):
    """反向：地点没解析出来就不写 geo 行——不写一行空地点，也不拿坐标顶上。

    日期与文件名锚点与地点无关，仍然要写。
    """
    add_asset(assets)
    add_observation(observations)
    index = SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)

    index.refresh_from_observation(observations.get("obs_1"))

    assert index.search_fts("萧山区", scope_id="album1", limit=10) == []
    assert dict(index.search_fts("2017-10-05", scope_id="album1", limit=10))["asset_1"] > 0
