"""S7：脸级描述修正/撤回、审计与检索同步。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import (
    SqliteAppearanceRepository,
)
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.occurrence_terms import (
    SqlitePersonOccurrenceIndex,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('person_a', 'album1', 'person', '待确认', 'pending', '', 0.8, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, member_count,"
            " representative_embedding_json, confidence, revision, created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'pending', 'person_a', 1, '[]', 0.8, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for face_id, cluster_id in (("face_a", "cluster_a"), ("face_b", None)):
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
                " embedding_model, embedding_version, created_at, identity_state)"
                " VALUES (?, 'asset_a', 'obs_a', ?, '[]', '[]', 0.9, 0.8, '{}', 'front',"
                " 'buffalo_l', 'w600k_r50', ?, 'local_only')",
                (face_id, cluster_id, NOW.isoformat()),
            )
    appearance = SqliteAppearanceRepository(db)
    appearance.record_description(
        person_id="person_a",
        face_instance_id="face_a",
        description="穿灰色上衣的人",
        source_build_id="",
        input_kind="numbered_full_image",
        source_revision=2,
        model_name="vlm_face_description",
    )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    _seed(migrations_ready_db)
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_evidence(id, scope_id, owner_kind, owner_id, field_path,"
            " role, value_json, source_kind, source_channel, observation_revision, status,"
            " created_at, updated_at) VALUES ('ev_b', 'album1', 'face_instance', 'face_b',"
            " 'face.description', 'single_image_face', ?, 'model', 'vlm_face_description',"
            " 1, 'effective', ?, ?)",
            (
                json.dumps({"description": "穿蓝色外套的人"}, ensure_ascii=False),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    SqlitePersonOccurrenceIndex(migrations_ready_db).rebuild()
    return migrations_ready_db


def test_correction_updates_text_and_search(db: Database) -> None:
    appearance = SqliteAppearanceRepository(db)
    result = appearance.correct_description(
        face_instance_id="face_a",
        scope_id="album1",
        expected_revision=2,
        description="穿蓝色上衣的人",
        reason="颜色写错",
    )
    assert result["source"] == "appearance"
    assert result["revision"] == 3
    faces = SqliteFaceRepository(db)
    assert faces.list_occurrences(scope_id="album1", description_query="灰色") == ()
    rows = faces.list_occurrences(scope_id="album1", description_query="上衣")
    assert [item.face_instance_id for item in rows] == ["face_a"]
    with db.connect() as connection:
        audit = connection.execute(
            "SELECT target_kind, before_json, after_json FROM memory_corrections WHERE target_id = 'face_a'"
        ).fetchone()
    assert audit is not None and audit["target_kind"] == "face_instance"
    assert "灰色" in audit["before_json"] and "蓝色" in audit["after_json"]


def test_stale_revision_is_rejected(db: Database) -> None:
    with pytest.raises(ValueError, match="changed"):
        SqliteAppearanceRepository(db).correct_description(
            face_instance_id="face_a",
            scope_id="album1",
            expected_revision=1,
            description="新描述",
        )


def test_retraction_removes_description_and_does_not_fallback(db: Database) -> None:
    appearance = SqliteAppearanceRepository(db)
    result = appearance.correct_description(
        face_instance_id="face_a",
        scope_id="album1",
        expected_revision=2,
        retract=True,
        reason="不适合作长期描述",
    )
    assert result["retracted"] is True
    faces = SqliteFaceRepository(db)
    assert faces.list_occurrences(scope_id="album1", description_query="灰色") == ()
    by_id = {item.face_instance_id: item for item in faces.list_occurrences(scope_id="album1")}
    assert by_id["face_a"].description == ""
    assert by_id["face_a"].description_state == "not_provided"


def test_local_only_face_evidence_can_be_corrected(db: Database) -> None:
    appearance = SqliteAppearanceRepository(db)
    result = appearance.correct_description(
        face_instance_id="face_b",
        scope_id="album1",
        expected_revision=1,
        description="穿绿色外套的人",
    )
    assert result["source"] == "face_evidence"
    rows = SqliteFaceRepository(db).list_occurrences(scope_id="album1", description_query="绿色外套")
    assert [item.face_instance_id for item in rows] == ["face_b"]
