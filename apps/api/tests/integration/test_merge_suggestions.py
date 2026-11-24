"""S3b：相似簇人工建议、负向决策与版本失效。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.application.identity.merge_suggestions import (
    MAX_PER_CLUSTER,
    ReadMergeSuggestions,
    RejectMergeSuggestion,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.merge_suggestions import (
    SqlitePersonMergeSuggestionRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _cluster(connection, scope_id: str, cluster_id: str, embedding: list[float], revision: int = 1) -> None:
    connection.execute(
        "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
        " confidence, created_at, updated_at)"
        " VALUES (?, ?, 'person', '待确认人物簇 · cluster-x', 'pending', '', 0.8, ?, ?)",
        (f"person_{cluster_id}", scope_id, NOW.isoformat(), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
        " member_count, confidence, revision, created_at, updated_at)"
        " VALUES (?, ?, 'pending', ?, ?, 1, 0.8, ?, ?, ?)",
        (
            cluster_id,
            scope_id,
            f"person_{cluster_id}",
            json.dumps(embedding),
            revision,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
        " VALUES (?, ?, ?, 'image', ?, ?, ?)",
        (
            f"asset_{cluster_id}",
            scope_id,
            f"{cluster_id}.jpg",
            f"/tmp/{cluster_id}.jpg",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
        " VALUES (?, ?, ?, 'import', ?, ?)",
        (f"obs_{cluster_id}", scope_id, f"asset_{cluster_id}", NOW.isoformat(), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
        " embedding_json, detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
        " embedding_version, created_at, identity_state)"
        " VALUES (?, ?, ?, ?, '[]', ?, 0.9, 0.8, '{}', 'front', 'buffalo_l', 'w600k_r50', ?,"
        " 'candidate')",
        (
            f"face_{cluster_id}",
            f"asset_{cluster_id}",
            f"obs_{cluster_id}",
            cluster_id,
            json.dumps(embedding),
            NOW.isoformat(),
        ),
    )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album2', 'album2', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        _cluster(connection, "album1", "cluster_a", [1.0, 0.0])
        _cluster(connection, "album1", "cluster_b", [0.9, 0.1])
        _cluster(connection, "album2", "cluster_c", [1.0, 0.0])
    return migrations_ready_db


def test_suggestions_only_pair_similar_clusters_in_the_same_scope(db: Database) -> None:
    repository = SqlitePersonMergeSuggestionRepository(db)
    result = ReadMergeSuggestions(suggestions=repository).execute(scope_id="album1")

    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    assert {suggestion.left.cluster_id, suggestion.right.cluster_id} == {
        "cluster_a",
        "cluster_b",
    }
    assert suggestion.score > 0.99
    assert suggestion.left.display_label.startswith("待确认人物…")


def test_rejection_suppresses_until_the_feature_version_changes(db: Database) -> None:
    repository = SqlitePersonMergeSuggestionRepository(db)
    read = ReadMergeSuggestions(suggestions=repository)
    first = read.execute(scope_id="album1").suggestions[0]

    RejectMergeSuggestion(suggestions=repository).execute(
        scope_id="album1",
        left_cluster_id=first.left.cluster_id,
        right_cluster_id=first.right.cluster_id,
        feature_version=first.feature_version,
    )
    assert read.execute(scope_id="album1").suggestions == ()

    with db.connect() as connection, connection:
        connection.execute("UPDATE face_clusters SET revision = revision + 1 WHERE id = 'cluster_b'")
    assert len(read.execute(scope_id="album1").suggestions) == 1


def test_each_cluster_is_capped(db: Database) -> None:
    with db.connect() as connection, connection:
        for index in range(7):
            _cluster(connection, "album1", f"cluster_extra_{index}", [1.0, 0.01 * index])
    repository = SqlitePersonMergeSuggestionRepository(db)
    result = ReadMergeSuggestions(suggestions=repository).execute(scope_id="album1", limit=50)
    counts: dict[str, int] = {}
    for item in result.suggestions:
        counts[item.left.cluster_id] = counts.get(item.left.cluster_id, 0) + 1
        counts[item.right.cluster_id] = counts.get(item.right.cluster_id, 0) + 1
    assert max(counts.values()) <= MAX_PER_CLUSTER
