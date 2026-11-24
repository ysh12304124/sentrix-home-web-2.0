"""S2：人物出现读取与单次归属驳回。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import MemoryEvidence
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import (
    SqliteAppearanceRepository,
)
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
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
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
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
            " VALUES ('person_a', 'album1', 'person', '未确认姓名人物', 'pending', '', 0.8, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
            " member_count, confidence, revision, created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'pending', 'person_a', '[]', 1, 0.8, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for face_id, cluster_id, state in (
            ("face_a", "cluster_a", "candidate"),
            ("face_b", None, "local_only"),
        ):
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
                " embedding_model, embedding_version, created_at, identity_state)"
                " VALUES (?, 'asset_a', 'obs_a', ?, ?, ?, 0.9, 0.8, ?, 'front', 'buffalo_l',"
                " 'w600k_r50', ?, ?)",
                (
                    face_id,
                    cluster_id,
                    json.dumps([0, 0, 10, 10]),
                    json.dumps([1.0, 0.0]),
                    json.dumps({}),
                    NOW.isoformat(),
                    state,
                ),
            )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
            " confidence, created_at) VALUES ('mention_a', 'person_a', 'obs_a', 'face_a', 0.9, ?)",
            (NOW.isoformat(),),
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    seed(migrations_ready_db)
    appearance = SqliteAppearanceRepository(migrations_ready_db)
    appearance.record_description(
        person_id="person_a",
        face_instance_id="face_a",
        description="穿灰色上衣的人",
        source_build_id="",
        input_kind="numbered_full_image",
        source_revision=2,
        model_name="vlm_face_description",
    )
    SqliteMemoryEvidenceRepository(migrations_ready_db).add(
        MemoryEvidence(
            id="ev_face_b",
            scope_id="album1",
            owner_kind="face_instance",
            owner_id="face_b",
            field_path="face.description",
            source_kind="model",
            source_modality="image",
            source_channel="vlm_face_description",
            value={"label": "P1", "description": "穿蓝色外套的人"},
            observation_revision=2,
            status="effective",
        )
    )
    return migrations_ready_db


def test_occurrences_choose_appearance_then_face_evidence(db: Database) -> None:
    rows = SqliteFaceRepository(db).list_occurrences(scope_id="album1")
    by_id = {row.face_instance_id: row for row in rows}
    assert by_id["face_a"].person_id == "person_a"
    assert by_id["face_a"].description == "穿灰色上衣的人"
    assert by_id["face_a"].description_source == "appearance"
    assert by_id["face_a"].identity_state == "candidate"
    assert by_id["face_a"].display_label.startswith("人物…")
    assert by_id["face_b"].person_id is None
    assert by_id["face_b"].description == "穿蓝色外套的人"
    assert by_id["face_b"].description_source == "face_evidence"
    assert by_id["face_b"].display_label.startswith("本图…")


def test_reject_assignment_only_removes_this_occurrence(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    instance = faces.reject_assignment("face_a", expected_cluster_id="cluster_a")

    assert instance.cluster_id is None
    assert instance.identity_state == "rejected"
    assert instance.rejected_entity_id == "person_a"
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM entity_mentions WHERE face_instance_id = 'face_a'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT member_count FROM face_clusters WHERE id = 'cluster_a'").fetchone()[0]
            == 0
        )
    remaining = faces.list_occurrences(scope_id="album1")
    assert [row.face_instance_id for row in remaining] == ["face_b"]


def test_confirm_assignment_allows_the_name_for_this_occurrence_only(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "UPDATE entities SET status = 'confirmed', canonical_name = '妈妈',"
            " name_state = 'confirmed' WHERE id = 'person_a'"
        )
    faces = SqliteFaceRepository(db)
    instance = faces.confirm_assignment("face_a", expected_cluster_id="cluster_a")
    assert instance.identity_state == "user_verified"

    by_id = {row.face_instance_id: row for row in faces.list_occurrences(scope_id="album1")}
    assert by_id["face_a"].display_label == "妈妈"
    assert by_id["face_b"].display_label.startswith("本图…")


def test_reject_assignment_cas_protects_stale_ui(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    with pytest.raises(ValueError, match="changed"):
        faces.reject_assignment("face_a", expected_cluster_id="cluster_other")
