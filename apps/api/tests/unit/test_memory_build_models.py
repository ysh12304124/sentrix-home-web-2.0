"""M1 DTO 的输入校验。非法阶段/状态必须在构造时就红，不等到落库。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import MemoryBuildTask, MemoryEvidence, ObservationBuild

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _build(**overrides: object) -> ObservationBuild:
    data = {
        "id": "build_1",
        "scope_id": "album1",
        "asset_id": "asset_a",
        "stage": "observation",
        "status": "running",
        "build_key": "key-1",
    }
    data.update(overrides)
    return ObservationBuild(**data)  # type: ignore[arg-type]


def test_build_accepts_known_stage_and_status() -> None:
    build = _build()
    assert build.stage == "observation"
    assert build.attempt == 1


@pytest.mark.parametrize("stage", ["unknown", "Observation", ""])
def test_build_rejects_unknown_stage(stage: str) -> None:
    with pytest.raises(ValueError, match="未知构建阶段"):
        _build(stage=stage)


@pytest.mark.parametrize("status", ["done", "ready", ""])
def test_build_rejects_unknown_status(status: str) -> None:
    with pytest.raises(ValueError, match="未知构建状态"):
        _build(status=status)


def test_build_rejects_negative_revision_and_zero_attempt() -> None:
    with pytest.raises(ValueError, match="observation_revision"):
        _build(observation_revision=-1)
    with pytest.raises(ValueError, match="attempt"):
        _build(attempt=0)


def test_evidence_validates_owner_and_status() -> None:
    with pytest.raises(ValueError, match="owner_kind"):
        MemoryEvidence(
            id="ev_1",
            scope_id="album1",
            owner_kind="person",
            owner_id="p1",
            field_path="caption",
            source_kind="model",
        )
    with pytest.raises(ValueError, match="证据状态"):
        MemoryEvidence(
            id="ev_1",
            scope_id="album1",
            owner_kind="asset",
            owner_id="a1",
            field_path="caption",
            source_kind="model",
            status="done",
        )
    with pytest.raises(ValueError, match="source_kind"):
        MemoryEvidence(
            id="ev_1",
            scope_id="album1",
            owner_kind="asset",
            owner_id="a1",
            field_path="caption",
            source_kind=" ",
        )


def test_task_validates_stage_status_and_attempts() -> None:
    with pytest.raises(ValueError, match="未知构建阶段"):
        MemoryBuildTask(
            id="task_1",
            scope_id="album1",
            asset_id="asset_a",
            stage="unknown",
            build_key="key-1",
            next_attempt_at=NOW,
        )
    with pytest.raises(ValueError, match="未知任务状态"):
        MemoryBuildTask(
            id="task_1",
            scope_id="album1",
            asset_id="asset_a",
            stage="observation",
            build_key="key-1",
            next_attempt_at=NOW,
            status="succeeded",
        )
    with pytest.raises(ValueError, match="attempts"):
        MemoryBuildTask(
            id="task_1",
            scope_id="album1",
            asset_id="asset_a",
            stage="observation",
            build_key="key-1",
            next_attempt_at=NOW,
            attempts=-1,
        )
