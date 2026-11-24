"""S1：观察写回与构建/字段证据在同一个应用用例里打通。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.domain.media.models import ObservationEntity
from sentrix.domain.media.ports import ObservationSemantics
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class FakeObserver:
    model_id = "fake-cloud-vlm"
    prompt_hash = "prompt-1"
    protocol_version = "image-observation-v1"

    def observe(self, path):  # noqa: ANN001, ANN201
        return ObservationSemantics(
            caption="海边散步",
            short_caption="一家人在海边",
            detail_caption="一家三口沿着海边走，孩子拿着气球。",
            tags=("海边", "散步"),
            entities=(ObservationEntity(name="海边", entity_type="place"),),
            field_states={
                "caption": "present",
                "detail_caption": "present",
                "ocr_text": "present",
            },
            ocr_text="HELLO 123",
            place="海边",
            activity="散步",
            confidence=0.65,
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
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
    return migrations_ready_db


def test_observe_writes_fields_build_and_evidence(db: Database) -> None:
    observations = SqliteObservationRepository(db)
    builds = SqliteMemoryBuildRepository(db)
    evidence = SqliteMemoryEvidenceRepository(db)
    service = ObserveAsset(
        observer=FakeObserver(),
        observations=observations,
        builds=builds,
        evidence=evidence,
    )
    asset = SqliteAssetRepository(db).get("asset_a")
    observation = observations.get("obs_a")
    assert asset is not None and observation is not None

    result = service.observe(asset, observation)

    assert result is not None
    assert result.short_caption == "一家人在海边"
    assert result.detail_caption.startswith("一家三口")
    assert result.ocr_text == "HELLO 123"
    assert result.tags == ("海边", "散步")
    assert result.active_build_id

    build = builds.get(result.active_build_id)
    assert build is not None
    assert build.stage == "observation"
    assert build.status == "succeeded"
    assert build.model_name == "fake-cloud-vlm"
    assert build.prompt_hash == "prompt-1"
    assert build.params["field_states"]["detail_caption"] == "present"

    rows = evidence.list_by_build(build.id)
    paths = {row.field_path for row in rows}
    assert {
        "observations.short_caption",
        "observations.detail_caption",
        "observations.ocr_text",
        "observations.tags",
        "observations.entities",
    } <= paths
    assert all(row.owner_kind == "observation" for row in rows)
    assert all(row.source_modality == "image" for row in rows)
