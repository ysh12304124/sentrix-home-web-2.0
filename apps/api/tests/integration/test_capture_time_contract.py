from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from sentrix.application.media.extract_video_scenes import _offset
from sentrix.application.retrieval.retrieve_metadata import _within
from sentrix.domain.media.capture_time import CaptureTime, elapsed_seconds, time_sort_key
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, VideoMetadata
from sentrix.domain.memories.events import within_candidate_window
from sentrix.domain.places.aliases import EMPTY_ALIASES
from sentrix.domain.places.matching import place_admin_matches, place_text_matches
from sentrix.domain.places.models import AdminArea, NearestPlace, Place, PlacePrecision, PlaceSource
from sentrix.domain.retrieval.structured_query import StructuredFilter
from sentrix.infrastructure.media.capture_time import ResolveCaptureTime, coordinate_timezone
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.structured import SqliteStructuredQueryRepository


def test_container_timestamp_preserves_offset_across_host_timezones(monkeypatch):
    import time

    value = "2018-03-31T23:30:00+07:00"
    for zone in ("UTC", "Asia/Shanghai", "America/Los_Angeles"):
        with monkeypatch.context() as env:
            env.setenv("TZ", zone)
            time.tzset()
            assert VideoMetadata(captured_at=value).captured_datetime().isoformat() == value
        time.tzset()


@pytest.mark.parametrize(
    ("lat", "lon", "zone"),
    [
        (30.2, 120.2, "Asia/Shanghai"),
        (18.78, 99.00, "Asia/Bangkok"),
        (40.71, -74.01, "America/New_York"),
        (51.51, -0.13, "Europe/London"),
    ],
)
def test_real_coordinate_timezone(lat, lon, zone):
    assert coordinate_timezone(lat, lon) == zone


def test_video_utc_and_photo_local_represent_same_instant():
    resolver = ResolveCaptureTime()
    gps = GpsPoint(18.78, 99.00)
    photo, provenance = resolver(datetime(2018, 4, 3, 9, 53, 52), gps, "exif")
    video, _ = resolver(datetime(2018, 4, 3, 2, 53, 52, tzinfo=UTC), gps, "creation_time")
    assert photo == video
    assert photo.isoformat() == "2018-04-03T09:53:52+07:00"
    assert provenance.status == "gps_timezone_inferred"
    assert provenance.raw == "2018-04-03T09:53:52"


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("2024-11-03T01:30:00", "ambiguous_local_time"),
        ("2024-03-10T02:30:00", "nonexistent_local_time"),
    ],
)
def test_dst_unknown_offset_not_guessed(raw, status):
    value = datetime.fromisoformat(raw)
    result, evidence = ResolveCaptureTime(lambda lat, lon: "America/New_York")(
        value,
        GpsPoint(40.71, -74.01),
        "exif",
    )
    assert result == value and result.tzinfo is None
    assert evidence.status == status


def test_missing_and_timezone_unknown_remain_unknown():
    resolver = ResolveCaptureTime()
    assert resolver(None, None, "exif") == (None, CaptureTime())
    value = datetime(2018, 4, 3, 9)
    assert resolver(value, None, "exif")[0] == value
    assert resolver(value, None, "exif")[1].status == "timezone_unknown"


def test_calendar_and_explicit_zone_queries():
    value = datetime.fromisoformat("2018-03-31T23:30:00+07:00")
    assert _within((datetime(2018, 3, 31), datetime(2018, 4, 1)), value)
    assert not _within((datetime(2018, 4, 1), datetime(2018, 4, 2)), value)
    bounds = (
        datetime.fromisoformat("2018-04-01T00:00:00+08:00"),
        datetime.fromisoformat("2018-04-02T00:00:00+08:00"),
    )
    assert _within(bounds, value)
    assert not _within(bounds, value.replace(tzinfo=None))
    assert not _within(bounds, None)


def test_dst_elapsed_and_frame_offsets_use_absolute_time():
    zone = ZoneInfo("America/New_York")
    first = datetime(2024, 11, 3, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2024, 11, 3, 1, 30, tzinfo=zone, fold=1)
    assert elapsed_seconds(second, first) == 3600
    assert _offset(first, 3600).fold == 1
    assert time_sort_key(second) > time_sort_key(first)
    assert not within_candidate_window(first, first.replace(tzinfo=None))


def asset(asset_id, time, **kwargs):
    return Asset(
        id=asset_id,
        scope_id="album1",
        file_name=asset_id + ".jpg",
        path="/tmp/x.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256=asset_id,
        captured_at=time,
        captured_location=GpsPoint(18.78, 99.00),
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
        updated_at=datetime(2026, 11, 4, tzinfo=UTC),
        **kwargs,
    )


def test_persistence_sort_and_structured_query_share_time_rules(migrations_ready_db):
    repo = SqliteAssetRepository(migrations_ready_db)
    early = datetime.fromisoformat("2018-04-01T01:00:00+09:00")
    late = datetime.fromisoformat("2018-03-31T23:30:00+07:00")
    evidence = CaptureTime(late.isoformat(), "exif", "Asia/Bangkok", "offset_gps_local")
    repo.add(asset("early", early))
    repo.add(asset("late", late, capture_time=evidence))
    assert [a.id for a in repo.list_by_scope("album1")] == ["late", "early"]
    assert repo.get("late").capture_time == evidence
    queries = SqliteStructuredQueryRepository(migrations_ready_db, EMPTY_ALIASES)
    filters = StructuredFilter(
        scope_id="album1", time_start="2018-03-31T00:00:00", time_end="2018-04-01T00:00:00"
    )
    assert queries.count(filters) == 1
    assert queries.captured_bound(StructuredFilter(scope_id="album1"), latest=True) == late.isoformat()


def test_persisted_video_retains_timezone_for_dst_frame_offsets(migrations_ready_db):
    repo = SqliteAssetRepository(migrations_ready_db)
    first = datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0)
    evidence = CaptureTime(first.isoformat(), "creation_time", "America/New_York", "offset_gps_local")
    repo.add(asset("dst-video", first, capture_time=evidence))
    restored = repo.get("dst-video")
    frame_time = _offset(restored.captured_at, 3600)
    assert frame_time.isoformat() == "2024-11-03T01:30:00-05:00"
    assert frame_time.fold == 1


def test_admin_query_does_not_prove_road_or_hotel(migrations_ready_db):
    place = Place(
        AdminArea("河北省", "石家庄市", "正定县"),
        NearestPlace("正定镇", "北门里村", 300, township_distance_m=1100),
        PlacePrecision.DISTRICT,
        PlaceSource.PYGEOCN,
    )
    assert place_admin_matches("河北省石家庄正定县", place, EMPTY_ALIASES)
    assert not place_admin_matches("正定县某条道路", place, EMPTY_ALIASES)
    assert not place_admin_matches("北门里村", place, EMPTY_ALIASES)
    assert place_text_matches("正定县某条道路", place, EMPTY_ALIASES)  # 仅能作为粗候选
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(asset("local", datetime(2018, 4, 3)))
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    places.save(asset_id="local", scope_id="album1", gps=None, place=place)
    assert places.get("local") == place
    queries = SqliteStructuredQueryRepository(migrations_ready_db, EMPTY_ALIASES)
    assert queries.count(StructuredFilter(scope_id="album1", place="正定县")) == 1
    assert queries.count(StructuredFilter(scope_id="album1", place="正定县某条道路")) == 0


def test_mixed_timezone_merge_refuses_before_mutation(migrations_ready_db):
    events = SqliteEventRepository(migrations_ready_db)
    for event_id, time in [("aware", datetime(2024, 1, 1, tzinfo=UTC)), ("naive", datetime(2024, 1, 1))]:
        events.create(
            event_id=event_id,
            scope_id="album1",
            time_start=time,
            time_end=time,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=1,
            aggregation_score=0,
            aggregation_breakdown={},
        )
    with pytest.raises(ValueError, match="不能归并"):
        events.merge("naive", "aware")
    assert events.get("naive") and events.get("aware")


def test_explicit_query_timezone_uses_same_rules_as_metadata():
    from sentrix.domain.retrieval.time_expression import parse_time_expression

    bounds = parse_time_expression("2018-04-01[Asia/Shanghai]")
    assert bounds is not None
    assert _within(bounds, datetime.fromisoformat("2018-03-31T23:30:00+07:00"))


def test_unknown_capture_is_not_replaced_by_import_time(migrations_ready_db, tmp_path):
    from io import BytesIO

    from PIL import Image

    from sentrix.application.media.import_assets import ImportAssets, ImportRequest
    from sentrix.infrastructure.media.metadata import PillowMetadataReader
    from sentrix.infrastructure.media.storage import FileSystemMediaStorage
    from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository

    content = BytesIO()
    Image.new("RGB", (8, 8)).save(content, format="JPEG")
    assets = SqliteAssetRepository(migrations_ready_db)
    importer = ImportAssets(
        assets=assets,
        observations=SqliteObservationRepository(migrations_ready_db),
        storage=FileSystemMediaStorage(tmp_path / "media"),
        metadata=PillowMetadataReader(),
    )
    result = importer.execute(ImportRequest("album1", "unknown.jpg", "image/jpeg", content.getvalue()))
    restored = assets.get(result.asset_id)
    assert restored.captured_at is None
    assert restored.capture_time.status == "unknown"
