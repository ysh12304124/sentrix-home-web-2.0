"""时间线读取：按日期成型、照片带地点展示、分页说的是「事件的页」。

任务验收（设计 §8 的 S4 行）是「导入整个相册，时间线按日期成型」。这条用例覆盖它
在**用例层**的形状：同一天的照片聚在同一个事件里，不同天分成不同的段。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sentrix.application.memories.read_timeline import ReadTimeline
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.places.models import AdminArea, NearestPlace, Place, PlacePrecision, PlaceSource
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

XIAOSHAN = GpsPoint(30.1839, 120.2642)
DAY_ONE = datetime(2017, 10, 5, 9, 0, 0, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)


@pytest.fixture
def timeline(migrations_ready_db: Database) -> ReadTimeline:
    return ReadTimeline(
        events=SqliteEventRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        assets=SqliteAssetRepository(migrations_ready_db),
        places=SqliteAssetPlaceRepository(migrations_ready_db),
    )


def photo(repos, *, index: int, captured_at: datetime, place: Place | None) -> str:  # noqa: ANN001
    assets, observations, places = repos
    asset_id, observation_id = f"asset_{index}", f"obs_{index}"
    assets.add(
        Asset(
            id=asset_id,
            scope_id="album1",
            file_name=f"{index}.jpg",
            media_type=MediaType.IMAGE,
            path=f"/tmp/{index}.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256=f"sha-{index}",
            captured_at=captured_at,
            captured_location=XIAOSHAN,
            created_at=captured_at,
            updated_at=captured_at,
        )
    )
    observations.add(
        Observation(
            id=observation_id,
            scope_id="album1",
            asset_id=asset_id,
            source_type="import",
            captured_at=captured_at,
            confidence=0.6,
            created_at=captured_at,
            updated_at=captured_at,
            caption=f"第{index}张",
        )
    )
    if place is not None:
        places.save(asset_id=asset_id, scope_id="album1", gps=XIAOSHAN, place=place)
    return observation_id


def make_event(events, *, event_id: str, when: datetime, place: str, district: str) -> None:  # noqa: ANN001
    events.create(
        event_id=event_id,
        scope_id="album1",
        time_start=when,
        time_end=when,
        place=place,
        admin_district=district,
        activity="拍照",
        summary="一家人在公园里拍照",
        confidence=0.65,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )


@pytest.fixture
def repos(migrations_ready_db: Database):
    return (
        SqliteAssetRepository(migrations_ready_db),
        SqliteObservationRepository(migrations_ready_db),
        SqliteAssetPlaceRepository(migrations_ready_db),
    )


def place() -> Place:
    return Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        gps=XIAOSHAN,
        confidence=0.9,
    )


def test_two_photos_on_one_day_form_one_event(timeline: ReadTimeline, repos, migrations_ready_db):
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="浙江省杭州市萧山区", district="萧山区")
    for index in (1, 2):
        events.attach("evt_1", photo(repos, index=index, captured_at=DAY_ONE, place=place()))

    result = timeline.execute(scope_id="album1")

    assert [day.date for day in result.days] == ["2017-10-05"]
    assert [event.id for event in result.days[0].events] == ["evt_1"]
    assert [item.asset_id for item in result.days[0].events[0].photos] == ["asset_1", "asset_2"]


def test_two_days_form_two_groups(timeline: ReadTimeline, repos, migrations_ready_db):
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="浙江省杭州市萧山区", district="萧山区")
    make_event(events, event_id="evt_2", when=DAY_TWO, place="河北省石家庄市赵县", district="赵县")
    events.attach("evt_1", photo(repos, index=1, captured_at=DAY_ONE, place=place()))
    events.attach("evt_2", photo(repos, index=2, captured_at=DAY_TWO, place=None))

    result = timeline.execute(scope_id="album1")

    assert [day.date for day in result.days] == ["2017-10-06", "2017-10-05"]
    assert result.total == 2


def test_photos_carry_the_backend_computed_place_display(timeline: ReadTimeline, repos, migrations_ready_db):
    """地点展示是**后端算好的最终字符串**：前端不做任何拼接（计划 Task 61）。"""
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="浙江省杭州市萧山区", district="萧山区")
    events.attach("evt_1", photo(repos, index=1, captured_at=DAY_ONE, place=place()))

    item = timeline.execute(scope_id="album1").days[0].events[0]

    assert item.photos[0].place == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"
    assert item.place == "浙江省杭州市萧山区"
    assert item.admin_district == "萧山区"


def test_photo_without_a_resolved_place_has_an_empty_display_not_a_guess(
    timeline: ReadTimeline, repos, migrations_ready_db
):
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="", district="")
    events.attach("evt_1", photo(repos, index=1, captured_at=DAY_ONE, place=None))

    item = timeline.execute(scope_id="album1").days[0].events[0]

    assert item.photos[0].place == ""


def test_pagination_pages_events_and_reports_the_real_total(
    timeline: ReadTimeline, repos, migrations_ready_db
):
    events = SqliteEventRepository(migrations_ready_db)
    for index in range(5):
        make_event(
            events,
            event_id=f"evt_{index}",
            when=DAY_ONE - timedelta(days=index),
            place="",
            district="",
        )

    first = timeline.execute(scope_id="album1", limit=2)
    second = timeline.execute(scope_id="album1", limit=2, offset=2)

    assert first.total == 5
    assert len(first.days) <= 2
    assert [event.id for day in first.days for event in day.events] == ["evt_0", "evt_1"]
    assert [event.id for day in second.days for event in day.events] == ["evt_2", "evt_3"]


def test_scope_isolation(timeline: ReadTimeline, migrations_ready_db):
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="", district="")
    events.create(
        event_id="evt_other",
        scope_id="album2",
        time_start=DAY_ONE,
        time_end=DAY_ONE,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )

    result = timeline.execute(scope_id="album1")

    assert [event.id for day in result.days for event in day.events] == ["evt_1"]
    assert result.total == 1


def test_event_detail_lists_its_photos(timeline: ReadTimeline, repos, migrations_ready_db):
    events = SqliteEventRepository(migrations_ready_db)
    make_event(events, event_id="evt_1", when=DAY_ONE, place="浙江省杭州市萧山区", district="萧山区")
    events.attach("evt_1", photo(repos, index=1, captured_at=DAY_ONE, place=place()))

    detail = timeline.get_event("evt_1")

    assert detail is not None
    assert [item.file_name for item in detail.photos] == ["1.jpg"]


def test_missing_event_detail_is_none(timeline: ReadTimeline):
    assert timeline.get_event("evt_gone") is None
