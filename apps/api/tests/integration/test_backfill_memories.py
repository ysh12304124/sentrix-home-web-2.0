"""S8：H1 确定性回填、H2 任务幂等/取消/执行。"""

from __future__ import annotations

from datetime import UTC, datetime

from sentrix.application.media.backfill_memories import (
    BackfillMemories,
    QueueMemoryRebuild,
    RunMemoryRebuildTasks,
)
from sentrix.application.memories.publish_event_document import PublishEventDocument
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.event_documents import (
    SqliteEventDocumentRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
    SqliteMemoryBuildTaskRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)
from sentrix.infrastructure.persistence.repositories.occurrence_terms import (
    SqlitePersonOccurrenceIndex,
)
from sentrix.infrastructure.persistence.repositories.participants import (
    SqliteEventParticipantRepository,
)
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(db: Database) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " captured_at, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
            " caption, short_caption, revision, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, '海边合影', '海边', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
    events = SqliteEventRepository(db)
    events.create(
        event_id="event_a",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="海边",
        admin_district="",
        activity="散步",
        summary="海边散步",
        confidence=0.8,
        aggregation_score=0.9,
        aggregation_breakdown={},
    )
    events.attach("event_a", "obs_a")


def _publisher(db: Database) -> PublishEventDocument:
    return PublishEventDocument(
        events=SqliteEventRepository(db),
        observations=SqliteObservationRepository(db),
        assets=SqliteAssetRepository(db),
        places=SqliteAssetPlaceRepository(db),
        faces=SqliteFaceRepository(db),
        participants=SqliteEventParticipantRepository(db),
        evidence=SqliteMemoryEvidenceRepository(db),
        documents=SqliteEventDocumentRepository(db),
    )


def test_h1_dry_run_then_backfills_without_model(migrations_ready_db: Database) -> None:
    _seed(migrations_ready_db)
    use_case = BackfillMemories(
        assets=SqliteAssetRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        events=SqliteEventRepository(migrations_ready_db),
        lexical=SqliteLexicalIndex(
            migrations_ready_db,
            SqliteObservationRepository(migrations_ready_db),
            assets=SqliteAssetRepository(migrations_ready_db),
            places=SqliteAssetPlaceRepository(migrations_ready_db),
        ),
        occurrences=SqlitePersonOccurrenceIndex(migrations_ready_db),
        publisher=_publisher(migrations_ready_db),
    )
    dry = use_case.execute("album1", dry_run=True)
    assert dry.dry_run is True
    assert dry.counts["native_images"] == 1
    assert dry.counts["model_calls"] == 0

    result = use_case.execute("album1", dry_run=False)
    assert result.counts["event_documents"] == 1
    assert result.counts["model_calls"] == 0
    document = SqliteEventDocumentRepository(migrations_ready_db).active_for_event("event_a")
    assert document is not None
    assert document.title


def test_h2_submit_is_idempotent_and_lexical_stage_runs(migrations_ready_db: Database) -> None:
    _seed(migrations_ready_db)
    tasks = SqliteMemoryBuildTaskRepository(migrations_ready_db)
    queue = QueueMemoryRebuild(
        assets=SqliteAssetRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        events=SqliteEventRepository(migrations_ready_db),
        documents=SqliteEventDocumentRepository(migrations_ready_db),
        tasks=tasks,
    )
    dry = queue.submit("album1", stages=["lexical_vector_index"], dry_run=True)
    assert dry.missing == {"lexical_vector_index": 1}
    assert dry.estimated_model_calls == 1

    first = queue.submit("album1", stages=["lexical_vector_index"], dry_run=False)
    second = queue.submit("album1", stages=["lexical_vector_index"], dry_run=False)
    assert first.tasks[0].id == second.tasks[0].id
    assert [item.stage for item in tasks.list_for_scope("album1")] == ["lexical_vector_index"]

    class FakeEmbedder:
        model_id = "text-model"
        dimension = 2

        def embed_query(self, text):
            return [1.0, 0.0]

    class FakeVectors:
        def upsert(self, record):
            return None

    runner = RunMemoryRebuildTasks(
        tasks=tasks,
        assets=SqliteAssetRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        events=SqliteEventRepository(migrations_ready_db),
        observer=None,
        storage=None,
        builds=SqliteMemoryBuildRepository(migrations_ready_db),
        evidence=SqliteMemoryEvidenceRepository(migrations_ready_db),
        lexical=SqliteLexicalIndex(
            migrations_ready_db,
            SqliteObservationRepository(migrations_ready_db),
            assets=SqliteAssetRepository(migrations_ready_db),
            places=SqliteAssetPlaceRepository(migrations_ready_db),
        ),
        embedder=FakeEmbedder(),
        vectors=FakeVectors(),
        publisher=_publisher(migrations_ready_db),
        occurrences=SqliteFaceRepository(migrations_ready_db),
    )
    run = runner.execute(limit=5)
    assert run.done == 1 and run.failed == 0
    assert tasks.list_for_scope("album1")[0].status == "done"

    assert tasks.cancel_scope("album1") == 0
