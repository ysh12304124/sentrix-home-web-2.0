"""S3b：相似簇建议读取与拒绝的 HTTP 契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.infrastructure.persistence.database import Database

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(database: Database) -> None:
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        for cluster_id, embedding in (("cluster_a", [1.0, 0.0]), ("cluster_b", [0.9, 0.1])):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, 'album1', 'person', '待确认人物簇 · cluster-x', 'pending', '',"
                " 0.8, ?, ?)",
                (f"person_{cluster_id}", NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
                " representative_embedding_json, member_count, confidence, revision,"
                " created_at, updated_at)"
                " VALUES (?, 'album1', 'pending', ?, ?, 1, 0.8, 1, ?, ?)",
                (
                    cluster_id,
                    f"person_{cluster_id}",
                    json.dumps(embedding),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )


def test_merge_suggestions_are_manual_only_and_can_be_rejected(client: TestClient) -> None:
    database = client.app.state.container.database
    _seed(database)

    response = client.get("/api/v1/people/merge-suggestions", params={"scope_id": "album1"})
    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion["kind"] == "manual_review"
    assert suggestion["score"] > 0.99
    assert {suggestion["left"]["cluster_id"], suggestion["right"]["cluster_id"]} == {
        "cluster_a",
        "cluster_b",
    }

    rejected = client.post(
        "/api/v1/people/merge-suggestions/reject",
        json={
            "scope_id": "album1",
            "left_cluster_id": suggestion["left"]["cluster_id"],
            "right_cluster_id": suggestion["right"]["cluster_id"],
            "feature_version": suggestion["feature_version"],
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    remaining = client.get("/api/v1/people/merge-suggestions", params={"scope_id": "album1"})
    assert remaining.status_code == 200
    assert remaining.json()["suggestions"] == []
