"""S7：脸级描述修正/撤回 HTTP 契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.infrastructure.persistence.repositories.occurrence_terms import (
    SqlitePersonOccurrenceIndex,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(client: TestClient) -> None:
    database = client.app.state.container.database
    with database.connect() as connection, connection:
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
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, created_at, identity_state)"
            " VALUES ('face_a', 'asset_a', 'obs_a', NULL, '[]', '[]', 0.9, 0.8, '{}', 'front',"
            " 'buffalo_l', 'w600k_r50', ?, 'local_only')",
            (NOW.isoformat(),),
        )
        connection.execute(
            "INSERT INTO memory_evidence(id, scope_id, owner_kind, owner_id, field_path, role,"
            " value_json, source_kind, source_channel, observation_revision, status, created_at,"
            " updated_at) VALUES ('ev_a', 'album1', 'face_instance', 'face_a',"
            " 'face.description', 'single_image_face', ?, 'model', 'vlm_face_description',"
            " 2, 'effective', ?, ?)",
            (
                json.dumps({"description": "穿灰色上衣的人"}, ensure_ascii=False),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    SqlitePersonOccurrenceIndex(database).rebuild("album1")


def test_correct_and_retract_face_description(client: TestClient) -> None:
    _seed(client)
    corrected = client.patch(
        "/api/v1/face-instances/face_a/description",
        json={
            "scope_id": "album1",
            "expected_revision": 2,
            "description": "穿蓝色上衣的人",
            "reason": "颜色写错",
        },
    )
    assert corrected.status_code == 200
    assert corrected.json()["source"] == "face_evidence"
    found = client.get(
        "/api/v1/people/occurrences",
        params={"scope_id": "album1", "description_query": "蓝色上衣"},
    )
    assert [item["face_instance_id"] for item in found.json()["occurrences"]] == ["face_a"]

    stale = client.patch(
        "/api/v1/face-instances/face_a/description",
        json={"scope_id": "album1", "expected_revision": 2, "description": "又改"},
    )
    assert stale.status_code == 409

    retracted = client.patch(
        "/api/v1/face-instances/face_a/description",
        json={"scope_id": "album1", "expected_revision": 3, "retract": True},
    )
    assert retracted.status_code == 200
    remaining = client.get(
        "/api/v1/people/occurrences",
        params={"scope_id": "album1", "description_query": "蓝色上衣"},
    )
    assert remaining.json()["occurrences"] == []
