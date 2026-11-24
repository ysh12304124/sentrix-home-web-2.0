"""S4：事件文档构建、发布版本与时间线展示投影。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.application.memories.publish_event_document import PublishEventDocument
from sentrix.application.memories.read_timeline import ReadTimeline
from sentrix.domain.media.models import GpsPoint
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
from sentrix.infrastructure.persistence.repositories.event_documents import (
    SqliteEventDocumentRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.participants import (
    SqliteEventParticipantRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _asset(connection, asset_id: str, captured: str, gps: bool = True) -> None:
    connection.execute(
        "INSERT INTO assets (id, scope_id, file_name, media_type, path, status, captured_at,"
        " gps_latitude, gps_longitude, capture_time_json, created_at, updated_at)"
        " VALUES (?, 'album1', ?, 'image', ?, 'processed', ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            f"{asset_id}.jpg",
            f"/tmp/{asset_id}.jpg",
            captured,
            30.0 if gps else None,
            120.0 if gps else None,
            json.dumps(
                {
                    "raw": captured,
                    "source": "exif",
                    "timezone": "",
                    "status": "offset_only" if captured.endswith("+08:00") else "timezone_unknown",
                    "precision": "datetime",
                    "timezone_source": "exif_offset",
                }
            ),
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
        " short_caption, place, revision, created_at, updated_at)"
        " VALUES (?, 'album1', ?, 'import', ?, ?, ?, 2, ?, ?)",
        (
            f"obs_{asset_id}",
            asset_id,
            captured,
            f"{asset_id} 的短描述",
            "海边",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )


@pytest.fixture
def world(migrations_ready_db: Database) -> dict[str, object]:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        _asset(connection, "asset_a", "2024-01-01T23:30:00+08:00")
        _asset(connection, "asset_b", "2024-01-02T08:00:00+08:00")
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " name_state, summary, confidence, created_at, updated_at)"
            " VALUES ('person_mom', 'album1', 'person', '妈妈', 'confirmed', 'confirmed', '',"
            " 0.9, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
            " representative_embedding_json, member_count, confidence, revision,"
            " created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'confirmed', 'person_mom', '[]', 1, 0.9, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for face_id, asset_id, state in (
            ("face_a", "asset_a", "user_verified"),
            ("face_local", "asset_b", "local_only"),
        ):
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
                " embedding_model, embedding_version, created_at, identity_state)"
                " VALUES (?, ?, ?, ?, '[]', '[]', 0.9, 0.8, '{}', 'front', 'buffalo_l',"
                " 'w600k_r50', ?, ?)",
                (
                    face_id,
                    asset_id,
                    f"obs_{asset_id}",
                    "cluster_a" if face_id == "face_a" else None,
                    NOW.isoformat(),
                    state,
                ),
            )
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id,"
            " observation_id, asset_id, crop_bbox_json, clothing_json, confidence, model_name,"
            " status, created_at, updated_at, description_text, input_kind, source_revision,"
            " description_state) VALUES ('pae_a', 'person_mom', 'face_a', 'obs_asset_a',"
            " 'asset_a', '[]', '[]', 0.0, 'vlm_face_description', 'observed', ?, ?,"
            " '穿灰色上衣的人', 'numbered_full_image', 2, 'effective')",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO memory_evidence (id, scope_id, owner_kind, owner_id, field_path, role,"
            " value_json, source_kind, source_modality, source_channel, status, created_at,"
            " updated_at) VALUES ('ev_local', 'album1', 'face_instance', 'face_local',"
            " 'face.description', 'single_image_face', ?, 'model', 'image',"
            " 'vlm_face_description', 'effective', ?, ?)",
            (
                json.dumps({"description": "穿蓝色外套的人"}, ensure_ascii=False),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

    events = SqliteEventRepository(migrations_ready_db)
    event = events.create(
        event_id="event_1",
        scope_id="album1",
        time_start=datetime.fromisoformat("2024-01-01T23:30:00+08:00"),
        time_end=datetime.fromisoformat("2024-01-02T08:00:00+08:00"),
        place="海边",
        admin_district="",
        activity="散步",
        summary="旧摘要",
        confidence=0.8,
        aggregation_score=0.9,
        aggregation_breakdown={},
    )
    events.attach(event.id, "obs_asset_a")
    events.attach(event.id, "obs_asset_b")
    participants = SqliteEventParticipantRepository(migrations_ready_db)
    participants.upsert_participant(
        event.id,
        "person_mom",
        role="visible_subject",
        evidence_ids=("obs_asset_a",),
        confidence=0.75,
    )
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    for asset_id in ("asset_a", "asset_b"):
        places.save(
            asset_id=asset_id,
            scope_id="album1",
            gps=GpsPoint(30.0, 120.0),
            place=Place(
                admin=AdminArea(province="浙江省", city="杭州市"),
                nearest=NearestPlace(),
                precision=PlacePrecision.CITY,
                source=PlaceSource.PYGEOCN,
            ),
        )
    return {
        "db": migrations_ready_db,
        "events": events,
        "observations": SqliteObservationRepository(migrations_ready_db),
        "assets": SqliteAssetRepository(migrations_ready_db),
        "places": places,
        "faces": SqliteFaceRepository(migrations_ready_db),
        "participants": participants,
        "documents": SqliteEventDocumentRepository(migrations_ready_db),
    }


def _publisher(world: dict[str, object]) -> PublishEventDocument:
    return PublishEventDocument(
        events=world["events"],  # type: ignore[arg-type]
        observations=world["observations"],  # type: ignore[arg-type]
        assets=world["assets"],  # type: ignore[arg-type]
        places=world["places"],  # type: ignore[arg-type]
        faces=world["faces"],  # type: ignore[arg-type]
        participants=world["participants"],  # type: ignore[arg-type]
        documents=world["documents"],  # type: ignore[arg-type]
    )


def test_document_keeps_capture_range_scene_people_and_legacy_summary_state(world) -> None:
    document = _publisher(world).execute("event_1")
    assert document is not None
    assert document.revision == 1
    assert document.summary == "旧摘要"
    assert document.summary_source == "event_summary"
    assert document.summary_state == "model_generated_unverified"
    assert "legacy_summary_without_per_claim_refs" in document.coverage.notes
    assert document.time.display_time == "2024-01-01 至 2024-01-02"
    assert document.time.occurrence_time is None
    assert document.time.occurrence_time_state == "unknown"
    assert document.time.member_dates == ("2024-01-01", "2024-01-02")
    assert document.place.scene == ("海边",)
    assert document.place.display_place == "浙江省杭州市"
    assert document.place.conflict is False
    assert {item.display_label for item in document.participants} == {"妈妈", "本图…ce_local"}
    mom = next(item for item in document.participants if item.person_id == "person_mom")
    assert mom.verified_name == "妈妈"
    assert mom.descriptions == ("穿灰色上衣的人",)
    assert all("海边" not in value for value in document.place.physical_locations)


def test_publish_is_idempotent_and_revisions_supersede(world) -> None:
    publisher = _publisher(world)
    first = publisher.execute("event_1")
    second = publisher.execute("event_1")
    assert first is not None and second is not None
    assert (first.id, first.revision) == (second.id, second.revision)

    with world["db"].connect() as connection, connection:  # type: ignore[union-attr]
        connection.execute(
            "UPDATE observations SET short_caption = '新描述', revision = 3 WHERE id = 'obs_asset_a'"
        )
    third = publisher.execute("event_1")
    assert third is not None and third.revision == 2
    assert world["documents"].active_for_event("event_1").id == third.id  # type: ignore[union-attr]


def test_timeline_uses_member_dates_and_separates_scene_from_place(world) -> None:
    _publisher(world).execute("event_1")
    timeline = ReadTimeline(
        events=world["events"],  # type: ignore[arg-type]
        observations=world["observations"],  # type: ignore[arg-type]
        assets=world["assets"],  # type: ignore[arg-type]
        places=world["places"],  # type: ignore[arg-type]
        documents=world["documents"],  # type: ignore[arg-type]
    )
    any_day = timeline.execute(scope_id="album1", date="2024-01-02")
    assert any_day.total == 1
    first_day = timeline.execute(scope_id="album1", date="2024-01-02", date_filter_mode="first_capture")
    assert first_day.total == 0
    assert timeline.execute(scope_id="album1", place="杭州").total == 1
    assert timeline.execute(scope_id="album1", place="海边").total == 0
    assert timeline.execute(scope_id="album1", scene="海边").total == 1

    detail = timeline.get_event("event_1")
    assert detail is not None and detail.document is not None
    assert detail.document.display_place == "浙江省杭州市"
    assert detail.document.scene == ("海边",)
    assert detail.document.occurrence_time_state == "unknown"
    calendar = timeline.calendar("album1")
    assert {
        (day.date, day.count) for year in calendar.years for month in year.months for day in month.days
    } == {
        ("2024-01-01", 1),
        ("2024-01-02", 1),
    }


def test_multiple_physical_places_are_a_conflict_not_a_single_location(world) -> None:
    world["places"].save(  # type: ignore[union-attr]
        asset_id="asset_b",
        scope_id="album1",
        gps=GpsPoint(31.0, 121.0),
        place=Place(
            admin=AdminArea(province="上海市", city="上海市"),
            nearest=NearestPlace(),
            precision=PlacePrecision.CITY,
            source=PlaceSource.PYGEOCN,
        ),
    )
    document = _publisher(world).execute("event_1")
    assert document is not None
    assert document.place.conflict is True
    assert document.place.display_place == ""
    assert set(document.place.physical_locations) == {"浙江省杭州市", "上海市"}
