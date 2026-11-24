"""S3：人物出现描述检索、稳定 ID、跨 scope 与无效绑定拦截。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import MemoryEvidence
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)
from sentrix.infrastructure.persistence.repositories.occurrence_terms import (
    SqlitePersonOccurrenceIndex,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _space(connection, space_id: str) -> None:
    connection.execute(
        "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
        " VALUES (?, ?, 'household', 'active', ?, ?)",
        (space_id, space_id, NOW.isoformat(), NOW.isoformat()),
    )


def _photo(connection, scope_id: str, asset_id: str) -> None:
    connection.execute(
        "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
        " created_at, updated_at) VALUES (?, ?, ?, 'image', ?, 'processed', ?, ?)",
        (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW.isoformat(), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
        " VALUES (?, ?, ?, 'import', ?, ?)",
        (f"obs_{asset_id}", scope_id, asset_id, NOW.isoformat(), NOW.isoformat()),
    )


def seed(db: Database) -> None:
    with db.connect() as connection, connection:
        _space(connection, "album1")
        _space(connection, "album2")
        _photo(connection, "album1", "asset_a")
        _photo(connection, "album2", "asset_b")
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('person_a', 'album1', 'person', '待确认', 'pending', '', 0.8, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
            " member_count, confidence, revision, created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'pending', 'person_a', '[]', 1, 0.8, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for face_id, asset_id, cluster_id, state in (
            ("face_gray", "asset_a", "cluster_a", "candidate"),
            ("face_blue", "asset_a", None, "local_only"),
            ("face_other_scope", "asset_b", None, "local_only"),
        ):
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
                " embedding_model, embedding_version, created_at, identity_state)"
                " VALUES (?, ?, ?, ?, ?, ?, 0.9, 0.8, ?, 'front', 'buffalo_l',"
                " 'w600k_r50', ?, ?)",
                (
                    face_id,
                    asset_id,
                    f"obs_{asset_id}",
                    cluster_id,
                    json.dumps([0, 0, 10, 10]),
                    json.dumps([1.0, 0.0]),
                    json.dumps({}),
                    NOW.isoformat(),
                    state,
                ),
            )
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id, observation_id,"
            " asset_id, crop_bbox_json, clothing_json, confidence, model_name, status, created_at,"
            " updated_at, description_text, input_kind, source_revision, description_state)"
            " VALUES ('pae_gray', 'person_a', 'face_gray', 'obs_asset_a', 'asset_a', '[]', '[]',"
            " 0.0, 'vlm_face_description', 'observed', ?, ?, '穿灰色上衣的人',"
            " 'numbered_full_image', 3, 'effective')",
            (NOW.isoformat(), NOW.isoformat()),
        )
    SqliteMemoryEvidenceRepository(db).add(
        MemoryEvidence(
            id="ev_blue",
            scope_id="album1",
            owner_kind="face_instance",
            owner_id="face_blue",
            field_path="face.description",
            source_kind="model",
            source_modality="image",
            source_channel="vlm_face_description",
            value={"label": "P2", "description": "穿蓝色外套的人"},
            observation_revision=3,
            status="effective",
        )
    )
    SqliteMemoryEvidenceRepository(db).add(
        MemoryEvidence(
            id="ev_other",
            scope_id="album2",
            owner_kind="face_instance",
            owner_id="face_other_scope",
            field_path="face.description",
            source_kind="model",
            source_modality="image",
            source_channel="vlm_face_description",
            value={"label": "P1", "description": "穿灰色上衣的人"},
            observation_revision=1,
            status="effective",
        )
    )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    seed(migrations_ready_db)
    SqlitePersonOccurrenceIndex(migrations_ready_db).rebuild()
    return migrations_ready_db


def test_description_search_locates_face_and_local_only(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    rows = faces.list_occurrences(scope_id="album1", description_query="灰色上衣")
    assert [row.face_instance_id for row in rows] == ["face_gray"]
    assert rows[0].matched_fields == ("face_description",)
    assert rows[0].evidence_refs == ("pae_gray",)

    local = faces.list_occurrences(scope_id="album1", description_query="蓝色外套")
    assert [row.face_instance_id for row in local] == ["face_blue"]
    assert local[0].identity_state == "local_only"


def test_description_search_is_scope_isolated(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    assert [
        row.face_instance_id
        for row in faces.list_occurrences(scope_id="album1", description_query="灰色上衣")
    ] == ["face_gray"]
    assert [
        row.face_instance_id
        for row in faces.list_occurrences(scope_id="album2", description_query="灰色上衣")
    ] == ["face_other_scope"]


def test_identity_state_filter_and_stable_id(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    gray = faces.list_occurrences(
        scope_id="album1", description_query="face_blue", identity_state="local_only"
    )
    assert [row.face_instance_id for row in gray] == ["face_blue"]
    assert (
        faces.list_occurrences(
            scope_id="album1", description_query="face_blue", identity_state="user_verified"
        )
        == ()
    )


def test_stale_projection_is_revalidated_on_read(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    with db.connect() as connection, connection:
        connection.execute(
            "UPDATE person_appearance_evidence SET description_text = '穿蓝色上衣的人' WHERE id = 'pae_gray'"
        )
    # 投影仍是旧描述，读取按当前有效描述复核，旧词不再返回。
    assert faces.list_occurrences(scope_id="album1", description_query="灰色上衣") == ()


def test_reject_removes_occurrence_from_description_search(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    faces.reject_assignment("face_gray", expected_cluster_id="cluster_a")
    assert faces.list_occurrences(scope_id="album1", description_query="灰色上衣") == ()


def test_verified_name_is_indexed_only_after_confirmation(db: Database) -> None:
    faces = SqliteFaceRepository(db)
    with db.connect() as connection, connection:
        connection.execute(
            "UPDATE entities SET status = 'confirmed', canonical_name = '妈妈',"
            " name_state = 'confirmed' WHERE id = 'person_a'"
        )
    SqlitePersonOccurrenceIndex(db).refresh("face_gray")
    # 身份状态仍是 candidate：姓名不得进入检索投影。
    assert faces.list_occurrences(scope_id="album1", description_query="妈妈") == ()

    faces.confirm_assignment("face_gray", expected_cluster_id="cluster_a")
    rows = faces.list_occurrences(scope_id="album1", description_query="妈妈")
    assert [row.face_instance_id for row in rows] == ["face_gray"]
    assert "person_name" in rows[0].matched_fields
