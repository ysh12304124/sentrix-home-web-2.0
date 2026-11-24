"""S2：允许 pending 的 face 级描述入口，仍校验 face→cluster→entity 归属。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import (
    SqliteAppearanceRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def seed(db: Database, *, person_id: str = "person_a", cluster_entity: str = "person_a") -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('person_a', 'album1', 'person', '未确认姓名人物', 'pending', '', 0.8, ?, ?)",
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
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
            " member_count, confidence, revision, created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'pending', ?, '[]', 1, 0.8, 1, ?, ?)",
            (cluster_entity, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            " embedding_version, created_at)"
            " VALUES ('face_a', 'asset_a', 'obs_a', 'cluster_a', ?, ?, 0.9, 0.8, ?, 'front',"
            " 'buffalo_l', 'w600k_r50', ?)",
            (json.dumps([0, 0, 10, 10]), json.dumps([1.0, 0.0]), json.dumps({}), NOW.isoformat()),
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    seed(migrations_ready_db)
    return migrations_ready_db


def test_pending_person_can_receive_a_face_description(db: Database) -> None:
    repo = SqliteAppearanceRepository(db)
    record = repo.record_description(
        person_id="person_a",
        face_instance_id="face_a",
        description="穿灰色上衣的人",
        source_build_id="",
        input_kind="numbered_full_image",
        source_revision=3,
        model_name="vlm_face_description",
    )

    assert record.description_text == "穿灰色上衣的人"
    assert record.input_kind == "numbered_full_image"
    assert record.source_revision == 3
    assert record.status == "observed"
    assert record.description_state == "effective"


def test_description_rejects_a_face_that_belongs_to_another_person(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('person_b', 'album1', 'person', '另一个人', 'pending', '', 0.8, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "UPDATE face_clusters SET entity_id = 'person_b' WHERE id = 'cluster_a'"
        )
    repo = SqliteAppearanceRepository(db)
    with pytest.raises(ValueError, match="does not currently belong"):
        repo.record_description(
            person_id="person_a",
            face_instance_id="face_a",
            description="不应写入",
            source_build_id="",
            input_kind="numbered_full_image",
            source_revision=1,
            model_name="vlm_face_description",
        )
