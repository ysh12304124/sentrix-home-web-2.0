"""构建版本与阶段任务的 SQLite 行为：幂等、scope、状态不回退。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import MemoryBuildTask, ObservationBuild
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
    SqliteMemoryBuildTaskRepository,
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


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    seed(migrations_ready_db)
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqliteMemoryBuildRepository:
    return SqliteMemoryBuildRepository(db)


def _build(**overrides: object) -> ObservationBuild:
    data = {
        "id": "build_1",
        "scope_id": "album1",
        "asset_id": "asset_a",
        "observation_id": "obs_a",
        "observation_revision": 3,
        "stage": "observation",
        "status": "running",
        "build_key": "album1:asset_a:observation:hash-1",
        "input_hash": "hash-1",
        "model_name": "qwen3.8-flash-next-iq3_xxs",
        "protocol_version": "image-observation-v1",
        "params": {"max_side": 1280},
        "input_transform": {"exif_transpose": True, "annotated": True},
    }
    data.update(overrides)
    return ObservationBuild(**data)  # type: ignore[arg-type]


def test_create_is_idempotent_by_build_key(repo: SqliteMemoryBuildRepository) -> None:
    first, created = repo.create_or_get(_build())
    second, created_again = repo.create_or_get(_build(id="build_2"))
    assert created is True
    assert created_again is False
    assert first.id == second.id


def test_get_and_list_and_latest(repo: SqliteMemoryBuildRepository) -> None:
    build, _ = repo.create_or_get(_build())
    assert repo.get(build.id) is not None
    assert [item.id for item in repo.list_for_asset("asset_a")] == [build.id]
    assert repo.latest_for_stage("asset_a", "observation") is not None
    assert repo.latest_for_stage("asset_a", "detection") is None


def test_status_can_complete_but_not_regress(repo: SqliteMemoryBuildRepository) -> None:
    build, _ = repo.create_or_get(_build())
    done = repo.update_status(build.id, status="succeeded", finished_at=NOW)
    assert done is not None and done.status == "succeeded"
    with pytest.raises(ValueError, match="cannot regress"):
        repo.update_status(build.id, status="running")
    assert repo.update_status("missing", status="failed") is None


def test_build_rejects_scope_and_observation_mismatch(repo: SqliteMemoryBuildRepository) -> None:
    with pytest.raises(ValueError, match="scope mismatch"):
        repo.create_or_get(_build(scope_id="album2"))
    with pytest.raises(ValueError, match="does not belong"):
        repo.create_or_get(_build(observation_id="obs_b"))


def test_task_enqueue_is_idempotent_and_due_filtered(db: Database) -> None:
    repo = SqliteMemoryBuildTaskRepository(db)
    task = MemoryBuildTask(
        id="task_1",
        scope_id="album1",
        asset_id="asset_a",
        stage="observation",
        build_key="key-1",
        next_attempt_at=NOW,
    )
    first = repo.enqueue(task)
    second = repo.enqueue(
        MemoryBuildTask(
            id="task_2",
            scope_id="album1",
            asset_id="asset_a",
            stage="observation",
            build_key="key-1",
            next_attempt_at=NOW,
        )
    )
    assert first.id == second.id
    assert repo.get(first.id) is not None
    assert [item.id for item in repo.list_due(limit=10, now=NOW)] == [first.id]
    assert repo.list_due(limit=10, now=datetime(2026, 11, 24, 11, 0, tzinfo=UTC)) == ()
