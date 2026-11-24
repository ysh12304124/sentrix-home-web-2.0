"""字段依据的多态 owner 校验与 JSON round-trip。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import MemoryEvidence, ObservationBuild
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def seed(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for asset_id, observation_id in (("asset_a", "obs_a"), ("asset_b", "obs_b")):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, 'album1', ?, 'image', ?, ?, ?)",
                (asset_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, 'album1', ?, 'vlm', ?, ?)",
                (observation_id, asset_id, NOW.isoformat(), NOW.isoformat()),
            )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('person_a', 'album1', 'person', '妈妈', 'confirmed', '', 1.0, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, bbox_json, embedding_json,"
            " detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            " embedding_version, created_at)"
            " VALUES ('face_a', 'asset_a', 'obs_a', ?, ?, 0.9, 0.8, ?, 'front', 'buffalo_l',"
            " 'w600k_r50', ?)",
            (json.dumps([0, 0, 10, 10]), json.dumps([1.0, 0.0]), json.dumps({}), NOW.isoformat()),
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    seed(migrations_ready_db)
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqliteMemoryEvidenceRepository:
    return SqliteMemoryEvidenceRepository(db)


def _evidence(**overrides: object) -> MemoryEvidence:
    data = {
        "id": "ev_1",
        "scope_id": "album1",
        "owner_kind": "asset",
        "owner_id": "asset_a",
        "field_path": "observations.caption",
        "source_kind": "model",
        "source_modality": "image",
        "source_channel": "vlm_observation",
        "role": "scene_description",
        "value": {"text": "海边"},
        "value_hash": "hash-1",
        "locator": {"bbox": [1, 2, 3, 4]},
    }
    data.update(overrides)
    return MemoryEvidence(**data)  # type: ignore[arg-type]


def test_asset_evidence_round_trips_json(repo: SqliteMemoryEvidenceRepository) -> None:
    created = repo.add(_evidence())
    assert created.value == {"text": "海边"}
    assert created.locator == {"bbox": [1, 2, 3, 4]}
    assert created.status == "effective"


def test_observation_and_face_owner_scope_checked(
    repo: SqliteMemoryEvidenceRepository,
) -> None:
    repo.add(_evidence(id="ev_obs", owner_kind="observation", owner_id="obs_a"))
    repo.add(_evidence(id="ev_face", owner_kind="face_instance", owner_id="face_a"))
    repo.add(_evidence(id="ev_entity", owner_kind="entity", owner_id="person_a"))
    with pytest.raises(ValueError, match="owner not found"):
        repo.add(_evidence(owner_kind="observation", owner_id="obs_missing"))


def test_scope_mismatch_rejected(repo: SqliteMemoryEvidenceRepository) -> None:
    with pytest.raises(ValueError, match="scope mismatch"):
        repo.add(_evidence(scope_id="album2"))


def test_build_must_belong_to_the_same_asset(db: Database, repo: SqliteMemoryEvidenceRepository) -> None:
    builds = SqliteMemoryBuildRepository(db)
    build, _ = builds.create_or_get(
        ObservationBuild(
            id="build_1",
            scope_id="album1",
            asset_id="asset_a",
            observation_id="obs_a",
            stage="observation",
            status="succeeded",
            build_key="key-1",
        )
    )
    repo.add(_evidence(build_id=build.id))
    with pytest.raises(ValueError, match="different asset"):
        repo.add(
            _evidence(
                owner_kind="observation",
                owner_id="obs_b",
                build_id=build.id,
            )
        )


def test_list_filters_field_path_and_status(repo: SqliteMemoryEvidenceRepository) -> None:
    repo.add(_evidence())
    repo.add(_evidence(id="ev_2", field_path="observations.ocr_text", status="retracted"))
    rows = repo.list_for_owner(
        scope_id="album1",
        owner_kind="asset",
        owner_id="asset_a",
    )
    assert {item.id for item in rows} == {"ev_1", "ev_2"}
    assert [
        item.id
        for item in repo.list_for_owner(
            scope_id="album1",
            owner_kind="asset",
            owner_id="asset_a",
            field_path="observations.ocr_text",
        )
    ] == ["ev_2"]
    assert [
        item.id
        for item in repo.list_for_owner(
            scope_id="album1",
            owner_kind="asset",
            owner_id="asset_a",
            statuses=("effective",),
        )
    ] == ["ev_1"]
