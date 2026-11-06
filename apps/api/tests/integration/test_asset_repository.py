from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository


def make_asset(asset_id="asset_1", sha="abc123", scope="home-default") -> Asset:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    return Asset(
        id=asset_id,
        scope_id=scope,
        file_name="IMG_0001.HEIC",
        media_type=MediaType.IMAGE,
        path=f"/data/media/{asset_id}_IMG_0001.HEIC",
        mime_type="image/heic",
        size_bytes=1234,
        status=AssetStatus.QUEUED,
        content_sha256=sha,
        captured_at=datetime(2018, 4, 1, 21, 5, 47, tzinfo=UTC),
        captured_location=GpsPoint(18.7883, 98.9853),
        created_at=now,
        updated_at=now,
    )


def test_roundtrip_preserves_gps_and_captured_at(migrations_ready_db: Database):
    repo = SqliteAssetRepository(migrations_ready_db)
    asset = make_asset()

    repo.add(asset)
    loaded = repo.get(asset.id)

    assert loaded is not None
    assert loaded.captured_location == GpsPoint(18.7883, 98.9853)
    assert loaded.captured_at == asset.captured_at
    assert loaded.content_sha256 == "abc123"


def test_find_by_hash_is_scoped(migrations_ready_db: Database):
    repo = SqliteAssetRepository(migrations_ready_db)
    repo.add(make_asset(asset_id="asset_a", sha="same", scope="album1"))

    assert repo.find_by_hash("album1", "same") is not None
    assert repo.find_by_hash("album2", "same") is None


def test_update_status_persists(migrations_ready_db: Database):
    repo = SqliteAssetRepository(migrations_ready_db)
    repo.add(make_asset())

    repo.update_status("asset_1", AssetStatus.PROCESSED)

    assert repo.get("asset_1").status is AssetStatus.PROCESSED


def test_memory_space_roundtrip(migrations_ready_db: Database):
    spaces = SqliteSpaceRepository(migrations_ready_db)
    scope_id = spaces.ensure("album1", "第一个相册")

    loaded = spaces.get(scope_id)

    assert loaded is not None
    assert loaded.name == "第一个相册"
    assert [space.id for space in spaces.list_all()] == [scope_id]


def test_ensure_is_idempotent(migrations_ready_db: Database):
    spaces = SqliteSpaceRepository(migrations_ready_db)

    spaces.ensure("album1", "第一个相册")
    spaces.ensure("album1", "改个名字")

    assert len(spaces.list_all()) == 1
    assert spaces.get("album1").name == "第一个相册"


def test_offset_roundtrip_keeps_local_capture_day_for_calendar_queries(migrations_ready_db: Database):
    from dataclasses import replace
    from datetime import timedelta, timezone

    from sentrix.domain.media.models import Observation
    from sentrix.domain.retrieval.conditions import Condition, ConditionStatus, EvidenceContext, evaluate

    repo = SqliteAssetRepository(migrations_ready_db)
    local = datetime(2024, 1, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    original = replace(make_asset(), captured_at=local)
    repo.add(original)
    loaded = repo.get(original.id)
    assert loaded.captured_at.isoformat() == "2024-01-01T23:30:00-05:00"
    observation = Observation(
        id="o",
        scope_id=original.scope_id,
        asset_id=original.id,
        source_type="import",
        captured_at=local,
        confidence=1,
        created_at=local,
        updated_at=local,
    )
    context = EvidenceContext()
    assert (
        evaluate(Condition("time", "2024年1月1日"), loaded, observation, context=context).status
        == ConditionStatus.MATCHED
    )
    assert (
        evaluate(Condition("time", "2024年1月2日"), loaded, observation, context=context).status
        == ConditionStatus.CONTRADICTED
    )
