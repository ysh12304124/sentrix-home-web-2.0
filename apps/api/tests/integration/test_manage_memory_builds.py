"""S0 盘点：legacy/legacy_partial/v3 状态与关键帧分流。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.application.media.manage_memory_builds import ManageMemoryBuilds
from sentrix.domain.media.models import MemoryBuildTask, ObservationBuild
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
    SqliteMemoryBuildTaskRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _insert_asset(
    db: Database,
    asset_id: str,
    *,
    media_type: str = "image",
    derived_kind: str = "",
    parent_asset_id: str = "",
    caption: str = "",
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, derived_kind,"
            " parent_asset_id, created_at, updated_at)"
            " VALUES (?, 'album1', ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                f"{asset_id}.jpg",
                media_type,
                f"/tmp/{asset_id}.jpg",
                derived_kind,
                parent_asset_id or None,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        if caption:
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, caption,"
                " created_at, updated_at) VALUES (?, 'album1', ?, 'vlm', ?, ?, ?)",
                (f"obs_{asset_id}", asset_id, caption, NOW.isoformat(), NOW.isoformat()),
            )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    _insert_asset(migrations_ready_db, "legacy_a")
    _insert_asset(migrations_ready_db, "partial_a", caption="海边")
    _insert_asset(migrations_ready_db, "video_a", media_type="video")
    _insert_asset(
        migrations_ready_db,
        "frame_a",
        derived_kind="keyframe",
        parent_asset_id="video_a",
        caption="关键帧",
    )
    _insert_asset(migrations_ready_db, "v3_a", caption="完整")
    _insert_asset(migrations_ready_db, "v3_partial_a", caption="部分")
    builds = SqliteMemoryBuildRepository(migrations_ready_db)
    for asset_id, status in (("v3_a", "succeeded"), ("v3_partial_a", "partial")):
        builds.create_or_get(
            ObservationBuild(
                id=f"build_{asset_id}",
                scope_id="album1",
                asset_id=asset_id,
                observation_id=f"obs_{asset_id}",
                stage="observation",
                status=status,
                build_key=f"key-{asset_id}",
            )
        )
    SqliteMemoryBuildTaskRepository(migrations_ready_db).enqueue(
        MemoryBuildTask(
            id="task_a",
            scope_id="album1",
            asset_id="partial_a",
            stage="observation",
            build_key="key-task",
            next_attempt_at=NOW,
        )
    )
    return migrations_ready_db


@pytest.fixture
def use_case(db: Database) -> ManageMemoryBuilds:
    return ManageMemoryBuilds(
        assets=SqliteAssetRepository(db),
        observations=SqliteObservationRepository(db),
        builds=SqliteMemoryBuildRepository(db),
        evidence=SqliteMemoryEvidenceRepository(db),
        tasks=SqliteMemoryBuildTaskRepository(db),
    )


def test_inventory_classifies_legacy_and_v3(use_case: ManageMemoryBuilds) -> None:
    inventory = use_case.inventory("album1", limit=100)
    by_asset = {state.asset_id: state for state in inventory.assets}
    assert by_asset["legacy_a"].status == "legacy"
    assert by_asset["partial_a"].status == "legacy_partial"
    assert by_asset["v3_a"].status == "v3_complete"
    assert by_asset["v3_partial_a"].status == "v3_partial"
    assert by_asset["partial_a"].pending_tasks == 1
    assert by_asset["frame_a"].media_kind == "video_keyframe"
    assert "video_a" not in by_asset
    assert inventory.counts["total"] == 5
    assert inventory.counts["video_keyframe"] == 1
    assert inventory.counts["v3_complete"] == 1


def test_inventory_does_not_leak_other_scope(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album2', 'album2', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    use_case = ManageMemoryBuilds(
        assets=SqliteAssetRepository(db),
        observations=SqliteObservationRepository(db),
        builds=SqliteMemoryBuildRepository(db),
        evidence=SqliteMemoryEvidenceRepository(db),
        tasks=SqliteMemoryBuildTaskRepository(db),
    )
    assert use_case.inventory("album2").assets == ()
