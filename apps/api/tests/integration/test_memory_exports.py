"""S6：三文件导出包、字段来源、场景/地点分离与主干冲突。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sentrix.application.media.export_memories import ExportImageMemories, ExportRequest
from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.media.export_writer import FileSystemMemoryExportWriter
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_exports import (
    SqliteMemoryExportRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(db: Database, tmp_path) -> tuple[SqliteAssetRepository, SqliteMemoryExportRepository]:
    image = tmp_path / "IMG_0001.jpg"
    image.write_bytes(b"jpeg")
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " captured_at, gps_latitude, gps_longitude, capture_time_json, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'IMG_0001.jpg', 'image', ?, 'processed', ?, 30.0, 120.0,"
            " ?, ?, ?)",
            (
                str(image),
                "2024-01-01T12:00:00+08:00",
                json.dumps(
                    {
                        "raw": "2024:01:01",
                        "source": "exif",
                        "status": "offset_only",
                        "precision": "date",
                        "timezone_source": "exif_offset",
                    }
                ),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at, caption,"
            " short_caption, detail_caption, tags_json, entities_json, field_states_json,"
            " ocr_text, place, revision, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, '旧 caption', '短句',"
            " '详细描述：海边一家人的合影', ?, ?, ?, '门票文字', '海边', 2, ?, ?)",
            (
                "2024-01-01T12:00:00+08:00",
                json.dumps(["旅行", "旅行", "夏天"], ensure_ascii=False),
                json.dumps([{"name": "爸爸", "entity_type": "person"}], ensure_ascii=False),
                json.dumps({"detail_caption": "present", "ocr_text": "present"}),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO observation_builds (id, scope_id, asset_id, observation_id, stage,"
            " status, build_key, model_name, protocol_version, created_at, updated_at, finished_at)"
            " VALUES ('build_a', 'album1', 'asset_a', 'obs_a', 'observation', 'succeeded',"
            " 'build-a', 'qwen3.8-flash', 'image-observation-v2', ?, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute("UPDATE observations SET active_build_id = 'build_a' WHERE id = 'obs_a'")
        # 派生关键帧：不能混成官方图片素材。
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " derived_kind, parent_asset_id, created_at, updated_at)"
            " VALUES ('kf_1', 'album1', 'kf_1.jpg', 'image', '/tmp/kf.jpg', 'processed',"
            " 'video_keyframe', NULL, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    places = SqliteAssetPlaceRepository(db)
    places.save(
        asset_id="asset_a",
        scope_id="album1",
        gps=GpsPoint(30.0, 120.0),
        place=Place(
            admin=AdminArea(province="浙江省", city="杭州市"),
            nearest=NearestPlace(),
            precision=PlacePrecision.CITY,
            source=PlaceSource.PYGEOCN,
            precise_address="浙江省杭州市西湖区",
        ),
    )
    return SqliteAssetRepository(db), SqliteMemoryExportRepository(db)


def _export(db: Database, tmp_path) -> tuple[object, SqliteMemoryExportRepository, object]:
    assets, snapshots = _seed(db, tmp_path)
    writer = FileSystemMemoryExportWriter(tmp_path / "exports")
    use_case = ExportImageMemories(
        assets=assets,
        observations=SqliteObservationRepository(db),
        places=SqliteAssetPlaceRepository(db),
        faces=SqliteFaceRepository(db),
        evidence=SqliteMemoryEvidenceRepository(db),
        builds=SqliteMemoryBuildRepository(db),
        snapshots=snapshots,
        writer=writer,
    )
    return use_case.execute(ExportRequest(scope_id="album1")), snapshots, writer


def test_export_package_has_three_files_and_field_provenance(migrations_ready_db, tmp_path) -> None:
    snapshot, _, writer = _export(migrations_ready_db, tmp_path)
    assert snapshot.status == "ready"
    assert {item.name for item in snapshot.files} == {
        "image_memories.jsonl",
        "image_memory_provenance.jsonl",
        "export_manifest.json",
    }
    records = [
        json.loads(line)
        for line in writer.path_for(snapshot.id, "image_memories.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["id"] == "IMG_0001"
    assert record["type"] == "image"
    assert record["timestamp"] == "2024-01-01"
    assert record["caption"] == "详细描述：海边一家人的合影"
    assert record["short_caption"] == "短句"
    assert record["tags"] == ["旅行", "夏天"]
    assert record["entities"] == [{"name": "爸爸", "type": "person"}]
    assert record["location_name"] == "浙江省杭州市西湖区"
    assert record["model_used"] == "qwen3.8-flash"
    assert record["processing_version"] == "image-observation-v2"
    assert record["processed_at"] is not None
    provenance = [
        json.loads(line)
        for line in writer.path_for(snapshot.id, "image_memory_provenance.jsonl").read_text().splitlines()
    ]
    by_field = {item["field"]: item for item in provenance}
    assert by_field["ocr_regions"]["status"] == "not_provided"
    assert by_field["ocr_regions"]["regions"] is None
    assert by_field["timestamp"]["status"] == "effective"
    assert by_field["location_name"]["status"] == "effective"
    manifest = json.loads(writer.path_for(snapshot.id, "export_manifest.json").read_text())
    assert manifest["place_policy"] == "physical_location_only"
    assert manifest["counts"]["excluded_derived"] == 1


def test_duplicate_stems_block_publish_unless_explicit_mapping(migrations_ready_db, tmp_path) -> None:
    _seed(migrations_ready_db, tmp_path)
    image = tmp_path / "IMG_0001.jpg"
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " created_at, updated_at) VALUES ('asset_b', 'album1', 'IMG_0001.jpg', 'image', ?,"
            " 'processed', ?, ?)",
            (str(image), NOW.isoformat(), NOW.isoformat()),
        )
    blocked = _build_use_case(migrations_ready_db, tmp_path).execute(ExportRequest(scope_id="album1"))
    assert blocked.status == "blocked"
    assert any(item.startswith("duplicate_id:IMG_0001") for item in blocked.conflicts)
    assert blocked.files == ()

    fixed = _build_use_case(migrations_ready_db, tmp_path).execute(
        ExportRequest(scope_id="album1", id_map={"asset_b": "IMG_0001_B"})
    )
    assert fixed.status == "ready"


def _build_use_case(db: Database, tmp_path) -> ExportImageMemories:
    return ExportImageMemories(
        assets=SqliteAssetRepository(db),
        observations=SqliteObservationRepository(db),
        places=SqliteAssetPlaceRepository(db),
        faces=SqliteFaceRepository(db),
        evidence=SqliteMemoryEvidenceRepository(db),
        builds=SqliteMemoryBuildRepository(db),
        snapshots=SqliteMemoryExportRepository(db),
        writer=FileSystemMemoryExportWriter(tmp_path / "exports"),
    )
