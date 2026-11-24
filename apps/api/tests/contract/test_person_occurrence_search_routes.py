"""S3：人物出现描述查询与 /search 描述/人物 ID 契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.occurrence_terms import (
    SqlitePersonOccurrenceIndex,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(database: Database) -> None:
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " captured_at, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
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
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
            " member_count, confidence, revision, created_at, updated_at)"
            " VALUES ('cluster_a', 'album1', 'pending', 'person_a', '[]', 1, 0.8, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            " embedding_version, created_at, identity_state)"
            " VALUES ('face_a', 'asset_a', 'obs_a', 'cluster_a', ?, ?, 0.9, 0.8, ?, 'front',"
            " 'buffalo_l', 'w600k_r50', ?, 'candidate')",
            (
                json.dumps([0, 0, 10, 10]),
                json.dumps([1.0, 0.0]),
                json.dumps({}),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id, observation_id,"
            " asset_id, crop_bbox_json, clothing_json, confidence, model_name, status, created_at,"
            " updated_at, description_text, input_kind, source_revision, description_state)"
            " VALUES ('pae_a', 'person_a', 'face_a', 'obs_a', 'asset_a', '[]', '[]', 0.0,"
            " 'vlm_face_description', 'observed', ?, ?, '穿灰色上衣的人',"
            " 'numbered_full_image', 2, 'effective')",
            (NOW.isoformat(), NOW.isoformat()),
        )
    SqlitePersonOccurrenceIndex(database).rebuild("album1")


def test_occurrence_description_query_and_search_channel(client: TestClient) -> None:
    database = client.app.state.container.database
    _seed(database)

    response = client.get(
        "/api/v1/people/occurrences",
        params={"scope_id": "album1", "description_query": "灰色上衣"},
    )
    assert response.status_code == 200
    occurrences = response.json()["occurrences"]
    assert len(occurrences) == 1
    assert occurrences[0]["face_instance_id"] == "face_a"
    assert occurrences[0]["matched_fields"] == ["face_description"]
    assert occurrences[0]["evidence_refs"] == ["pae_a"]

    search = client.get("/api/v1/search", params={"description": "灰色上衣", "scope_id": "album1"})
    assert search.status_code == 200
    body = search.json()
    assert [item["asset"]["id"] for item in body["items"]] == ["asset_a"]
    hit = body["items"][0]
    assert hit["channels"] == {"person_occurrence": 1}
    assert hit["matched_fields"] == ["face_description"]
    assert hit["matched_occurrences"][0]["face_instance_id"] == "face_a"


def test_search_accepts_explicit_person_id_without_free_text(client: TestClient) -> None:
    database = client.app.state.container.database
    _seed(database)

    response = client.get("/api/v1/search", params={"person_id": "person_a", "scope_id": "album1"})
    assert response.status_code == 200
    body = response.json()
    assert [item["asset"]["id"] for item in body["items"]] == ["asset_a"]
    assert body["items"][0]["channels"] == {"person": 1}


def test_occurrence_query_rejects_unknown_identity_state(client: TestClient) -> None:
    database = client.app.state.container.database
    _seed(database)

    response = client.get(
        "/api/v1/people/occurrences",
        params={"scope_id": "album1", "identity_state": "definitely_not_real"},
    )
    assert response.status_code == 400
