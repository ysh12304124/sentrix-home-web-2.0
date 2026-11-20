"""Email authority, graph, retrieval and correction/deletion are one verifiable chain."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentrix.api.deps import get_container
from sentrix.api.errors import domain_error_handler
from sentrix.api.routes import corrections, emails, text, timeline
from sentrix.application.media.delete_asset import DeleteAsset
from sentrix.bootstrap.import_wiring import build_memory_graph
from sentrix.domain.errors import DomainError
from sentrix.domain.media.email import email_asset_id
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.text_memory import TextMemoryItem, text_asset_id
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.corrections import SqliteMemoryCorrections
from sentrix.infrastructure.persistence.repositories.emails import SqliteEmailRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.family import SqliteFamilyGraphRepository
from sentrix.infrastructure.persistence.repositories.media_deletion import SqliteMediaDeletionRepository
from sentrix.infrastructure.persistence.repositories.memory_graph import SqliteMemoryGraphRepository
from sentrix.infrastructure.persistence.repositories.participants import SqliteEventParticipantRepository
from sentrix.infrastructure.persistence.repositories.relationships import SqliteRelationshipRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.text_chunks import SqliteTextChunkRepository
from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

SCOPE = "email-test"
MAIL = {
    "id": "email202502110016",
    "timestamp": "2025-02-11 21:35:00",
    "short_summary": "Reservation at Fancett's on February 21, 2025 for two guests.",
    "detail": "Date: 2025-02-11\nReservation at Fancett's on February 21, 2025 at 1:15 PM. Code AB123.",
}


class Embedder:
    model_id = "test-text"
    dimension = 3
    failed = False

    def embed_query(self, text):
        return () if self.failed else (1.0, 0.0, 0.0)


class ExternalIndex:
    def delete_sources(self, sources):
        self.deleted = sources


@pytest.fixture
def setup(migrations_ready_db, tmp_path):
    db = migrations_ready_db
    assets, observations = SqliteAssetRepository(db), SqliteObservationRepository(db)
    entities = SqliteEntityRepository(db)
    c = SimpleNamespace(
        model_profile=None,
        database=db,
        asset_repository=assets,
        observation_repository=observations,
        email_repository=SqliteEmailRepository(db),
        spaces=SqliteSpaceRepository(db),
        media_storage=FileSystemMediaStorage(tmp_path / "media"),
        lexical_index=SqliteLexicalIndex(
            db, observations, assets=assets, places=SqliteAssetPlaceRepository(db)
        ),
        text_embedder=Embedder(),
        text_chunks=SqliteTextChunkRepository(db),
        text_vector_repository=SqliteTextVectorRepository(db),
        vector_repository=SqliteVectorRepository(db),
        memory_graph=SqliteMemoryGraphRepository(db),
        event_repository=SqliteEventRepository(db),
        event_participant_repository=SqliteEventParticipantRepository(db),
        entity_repository=entities,
        family_graph_repository=SqliteFamilyGraphRepository(db, entities),
        fact_repository=SqliteFactRepository(db),
        relationship_repository=SqliteRelationshipRepository(db),
        causal_judge=None,
        memory_corrections=SqliteMemoryCorrections(db),
        media_deletion=SqliteMediaDeletionRepository(db),
        vector_index=ExternalIndex(),
        asset_place_repository=SqliteAssetPlaceRepository(db),
    )
    c.spaces.ensure(SCOPE, SCOPE, "benchmark")
    c.text_memory_repository = c.email_repository
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.dependency_overrides[get_container] = lambda: c
    for router in (emails.router, text.router, corrections.router, timeline.router):
        app.include_router(router, prefix="/api/v1")
    return c, TestClient(app)


def imported(setup):
    c, client = setup
    response = client.post("/api/v1/emails/import", json={"scope_id": SCOPE, "items": [MAIL]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", response.text
    return c, client, email_asset_id(SCOPE, MAIL["id"])


def test_email_import_api_text_index_timeline_and_idempotence(setup):
    c, client, aid = imported(setup)
    record = c.email_repository.find(SCOPE, MAIL["id"])
    observation = c.observation_repository.get(record["observation_id"])
    assert observation.text_content == MAIL["detail"] and observation.ocr_text == ""
    assert observation.external_id == MAIL["id"]
    assert observation.source_type == "text"
    assert observation.source_metadata["source_type"] == "email"
    assert c.asset_repository.get(aid).captured_at.tzinfo is None
    assert c.event_repository.get(record["event_id"]).time_start.day == 11
    assert c.lexical_index.search_fts("AB123", scope_id=SCOPE, limit=6)
    assert c.text_chunks.indexed(observation.id, observation.revision, "test-text")
    assert c.text_vector_repository.count(space="semantic") == 0
    again = client.post("/api/v1/emails/import", json={"scope_id": SCOPE, "items": [MAIL]}).json()
    assert again["imported"] == 0 and again["skipped"] == 1
    with c.database.connect() as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert {r[0] for r in db.execute("SELECT node_type FROM memory_graph_nodes")} == {"asset", "event"}
        assert not db.execute(
            "SELECT 1 FROM memory_graph_edges WHERE relation='event_temporal_next'"
        ).fetchone()
    detail = client.get("/api/v1/events/" + record["event_id"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["time_start"].startswith("2025-02-11")
    assert client.get("/api/v1/emails/" + aid).json()["observation"]["text_content"] == MAIL["detail"]


def test_email_validation_and_conflict_do_not_silently_overwrite(setup):
    c, client, aid = imported(setup)
    changed = {**MAIL, "detail": "Changed source content"}
    result = client.post("/api/v1/emails/import", json={"scope_id": SCOPE, "items": [changed]})
    assert result.status_code == 400
    result = client.post("/api/v1/emails/import", json={"scope_id": SCOPE, "items": [MAIL, MAIL]})
    assert result.status_code == 400
    assert client.get("/api/v1/emails/" + aid).json()["observation"]["text_content"] == MAIL["detail"]


def test_email_index_failure_is_visible_and_retry_is_idempotent(setup):
    c, client = setup
    c.text_embedder.failed = True
    payload = {"scope_id": SCOPE, "items": [MAIL]}
    result = client.post("/api/v1/emails/import", json=payload).json()
    assert result["status"] == "partial" and result["errors"]
    assert c.lexical_index.search_fts("AB123", scope_id=SCOPE, limit=6)
    c.text_embedder.failed = False
    result = client.post("/api/v1/emails/import", json=payload).json()
    assert result["status"] == "completed" and result["skipped"] == 1


def test_text_import_repairs_orphaned_email_asset_rows(setup):
    c, client = setup
    item = TextMemoryItem.parse(
        {
            "external_id": MAIL["id"],
            "recorded_at": MAIL["timestamp"],
            "summary": MAIL["short_summary"],
            "text": MAIL["detail"],
            "metadata": {"source_type": "email"},
        }
    )
    aid = text_asset_id(SCOPE, item.external_id)
    path = c.media_storage.store(aid, item.external_id + ".txt", item.body.encode())
    # Reproduce historical partial imports where an asset and import-state row survived,
    # but its observation/event rows did not (legacy SQLite writes did not enable FKs).
    with sqlite3.connect(c.database.path) as db:
        db.execute(
            "INSERT INTO assets(id,scope_id,file_name,media_type,path,mime_type,size_bytes,status,"
            "content_sha256,created_at,updated_at) VALUES(?,?,?,'text',?,'text/plain',?,'processed',?,?,?)",
            (
                aid,
                SCOPE,
                item.external_id + ".txt",
                str(path),
                len(item.body.encode()),
                item.fingerprint,
                "2025-02-11T21:35:00",
                "2025-02-11T21:35:00",
            ),
        )
        db.execute(
            "INSERT INTO text_import_state(asset_id,observation_id,event_id,indexed_revision) "
            "VALUES(?,?,?,1)",
            (aid, "obs_" + aid, "evt_" + aid),
        )

    response = client.post(
        "/api/v1/emails/import",
        json={"scope_id": SCOPE, "items": [MAIL], "rebuild_graph": False},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed", response.text
    assert result["imported"] == 1 and result["skipped"] == 0
    assert c.observation_repository.get("obs_" + aid) is not None
    assert c.event_repository.get("evt_" + aid) is not None
    with c.database.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def add_photo(c, identifier, day):
    now = datetime(2025, 2, day, 13, 30)
    path = c.media_storage.store(identifier, identifier + ".jpg", b"fixture")
    c.asset_repository.add(
        Asset(
            identifier,
            SCOPE,
            identifier + ".jpg",
            MediaType.IMAGE,
            str(path),
            "image/jpeg",
            7,
            AssetStatus.PROCESSED,
            identifier,
            now,
            None,
            now,
            now,
        )
    )
    obs = Observation("obs-" + identifier, SCOPE, identifier, "import", now, 1.0, now, now, caption="Lunch")
    c.observation_repository.add(obs)
    c.event_repository.create(
        event_id="event-" + identifier,
        scope_id=SCOPE,
        time_start=now,
        time_end=now,
        place="Fancett",
        admin_district="",
        activity="lunch",
        summary="Lunch at Fancett",
        confidence=1.0,
        aggregation_score=1.0,
        aggregation_breakdown={},
    )
    c.event_repository.attach("event-" + identifier, obs.id)


def test_email_graph_uses_mentioned_date_and_retracts_after_correction(setup):
    c, client, aid = imported(setup)
    add_photo(c, "sending-day", 11)
    add_photo(c, "meal-day", 21)
    build_memory_graph(c).execute(SCOPE)
    hits = c.memory_graph.expand_assets([aid], scope_id=SCOPE, max_hops=4, limit=12)
    assert any(h.asset_id == "meal-day" and "text_refers_to_event" in h.relations for h in hits)
    assert not any(h.asset_id == "sending-day" and "text_refers_to_event" in h.relations for h in hits)
    record = c.email_repository.find(SCOPE, MAIL["id"])
    response = client.patch(
        "/api/v1/memories/observation/" + record["observation_id"],
        json={
            "revision": 1,
            "changes": {"caption": "Cancelled reservation", "text_content": "No reservation is active."},
            "reason": "verification",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["sync_status"] == "synced", response.text
    assert c.event_repository.get(record["event_id"]).summary == "Cancelled reservation"
    hits = c.memory_graph.expand_assets([aid], scope_id=SCOPE, max_hops=4, limit=12)
    assert not any("text_refers_to_event" in h.relations for h in hits)
    assert not c.lexical_index.search_fts("AB123", scope_id=SCOPE, limit=6)


def test_email_event_correction_synchronises_timestamp_and_summary(setup):
    c, client, aid = imported(setup)
    record = c.email_repository.find(SCOPE, MAIL["id"])
    response = client.patch(
        "/api/v1/memories/event/" + record["event_id"],
        json={
            "revision": 1,
            "changes": {"time_start": "2025-02-12T09:00:00", "summary": "Revised mail summary"},
            "reason": "timestamp correction",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["sync_status"] == "synced", response.text
    obs = c.observation_repository.get(record["observation_id"])
    assert obs.caption == "Revised mail summary" and obs.captured_at.day == 12
    assert c.asset_repository.get(aid).captured_at.day == 12
    event = c.event_repository.get(record["event_id"])
    assert event.time_start == event.time_end and event.time_start.day == 12
    state = c.email_repository.find(SCOPE, MAIL["id"])
    assert state["indexed_revision"] == obs.revision


def test_body_only_correction_rebuilds_text_summary_and_observation_together(setup):
    from sentrix.domain.assistant.models import ModelReply

    c, client, aid = imported(setup)
    record = c.email_repository.find(SCOPE, MAIL["id"])

    class Model:
        def complete(self, messages, tools):
            assert "No reservation remains" in messages[1].content
            return ModelReply(
                content='{"title":"Cancelled", "summary":"No reservation remains", '
                '"activity":"update", "event_type":"text_record"}'
            )

    c.chat_model = Model()
    response = client.patch(
        "/api/v1/memories/observation/" + record["observation_id"],
        json={
            "revision": 1,
            "changes": {"text_content": "No reservation remains"},
        },
    )
    assert response.status_code == 200 and response.json()["sync_status"] == "synced", response.text
    observation = c.observation_repository.get(record["observation_id"])
    assert (
        observation.caption == c.event_repository.get(record["event_id"]).summary == "No reservation remains"
    )
    assert c.email_repository.find(SCOPE, MAIL["id"])["indexed_revision"] == observation.revision
    assert c.asset_repository.get(aid) is not None


def test_email_id_rejects_path_traversal_before_any_write(setup):
    c, client = setup
    response = client.post(
        "/api/v1/emails/import", json={"scope_id": SCOPE, "items": [{**MAIL, "id": "../email1"}]}
    )
    assert response.status_code == 400
    assert c.email_repository.find(SCOPE, "../email1") is None


def test_generic_text_and_email_share_authority_and_keep_source_metadata(setup):
    c, client = setup
    rows = [
        {
            "external_id": "note-1",
            "text": "The appointment confirmation code is TX945.",
            "metadata": {"source_type": "note"},
        },
        {
            "external_id": "email-unified",
            "text": "The appointment confirmation code is EM412.",
            "metadata": {"source_type": "email"},
        },
        {"text": "No source or timestamp is required for a text memory."},
    ]
    response = client.post("/api/v1/text/import", json={"scope_id": SCOPE, "items": rows})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    for item in response.json()["items"]:
        detail = client.get("/api/v1/text/" + item["asset_id"]).json()
        assert detail["memory_type"] == "text"
        assert detail["observation"]["source_type"] == "text"
        assert detail["observation"]["text_content"]
        assert detail["observation"]["captured_at"] is None
        assert c.asset_repository.get(item["asset_id"]).captured_at is None
    repeated = client.post("/api/v1/text/import", json={"scope_id": SCOPE, "items": rows}).json()
    assert repeated["skipped"] == 3 and repeated["imported"] == 0
    assert c.lexical_index.search_fts("TX945", scope_id=SCOPE, limit=6)
    assert c.lexical_index.search_fts("EM412", scope_id=SCOPE, limit=6)


def test_text_english_subjects_are_not_drowned_by_common_words_or_latin_bigrams(setup):
    from sentrix.application.retrieval.text_memories import RetrieveTextMemories

    c, client = setup
    rows = [
        {
            "external_id": "rare-topic",
            "text": "Zephyrium TA receives 150 pounds.",
            "metadata": {"source_type": "email"},
        }
    ]
    rows += [
        {
            "external_id": f"general-{i}",
            "text": "The salary payment is updated. Data station.",
            "metadata": {"source_type": "note"},
        }
        for i in range(10)
    ]
    result = client.post("/api/v1/text/import", json={"scope_id": SCOPE, "items": rows}).json()
    correct = result["items"][0]["asset_id"]
    hits = (
        RetrieveTextMemories(c.text_chunks, c.text_embedder)
        .execute("Zephyrium TA payment salary", SCOPE, source_type="email")
        .hits
    )
    assert hits[0].asset_id == correct
    assert {
        h.asset_id
        for h in RetrieveTextMemories(c.text_chunks, c.text_embedder)
        .execute("TA", SCOPE, source_type="email", limit=20)
        .hits
    } == {correct}
    hits = RetrieveTextMemories(c.text_chunks, c.text_embedder).execute(
        "Zephyrium salary", SCOPE, source_type="email"
    )
    assert {hit.asset_id for hit in hits.hits} == {correct}


def test_generic_text_graph_correction_and_delete_use_the_same_pipeline(setup):
    c, client = setup
    add_photo(c, "meal-day", 21)
    row = {
        "external_id": "note-appointment",
        "recorded_at": "2025-02-11T09:00:00",
        "text": MAIL["detail"],
        "title": MAIL["short_summary"],
        "metadata": {"source_type": "note"},
    }
    result = client.post("/api/v1/text/import", json={"scope_id": SCOPE, "items": [row]}).json()
    aid = result["items"][0]["asset_id"]
    paths = c.memory_graph.expand_assets([aid], scope_id=SCOPE, max_hops=4, limit=12)
    assert any("text_refers_to_event" in h.relations and h.asset_id == "meal-day" for h in paths)
    record = c.email_repository.find(SCOPE, row["external_id"])
    response = client.patch(
        "/api/v1/memories/observation/" + record["observation_id"],
        json={"revision": 1, "changes": {"caption": "Cancelled", "text_content": "No appointment remains."}},
    )
    assert response.json()["sync_status"] == "synced", response.text
    paths = c.memory_graph.expand_assets([aid], scope_id=SCOPE, max_hops=4, limit=12)
    assert not any("text_refers_to_event" in h.relations for h in paths)
    deleted = DeleteAsset(c.media_deletion, c.media_storage, c.vector_index).execute(aid)
    assert deleted.deleted and not deleted.warnings
    assert c.asset_repository.get("meal-day") is not None
    assert client.get("/api/v1/text/" + aid).status_code == 404


def test_text_file_album_staging_uses_shared_importer_without_image_processing(setup, tmp_path):
    from sentrix.application.media.import_assets import ImportAssets, ImportRequest, ImportResult
    from sentrix.bootstrap.import_wiring import _import_text_file

    c, client = setup
    path = tmp_path / "note.txt"
    path.write_text("Text album staging code is TXSTAGE718.")

    def no_visual(*args, **kwargs):
        raise AssertionError("Text must never reach the image processor")

    importer = ImportAssets(
        assets=c.asset_repository,
        observations=c.observation_repository,
        storage=c.media_storage,
        metadata=SimpleNamespace(read=no_visual),
        spaces=c.spaces,
        text_importer=lambda request: _import_text_file(c, request),
    )
    result = importer.stage(
        ImportRequest(
            scope_id=SCOPE,
            file_name="note.txt",
            content_type="text/plain",
            content=b"",
            source_path=path,
            defer_processing=True,
        )
    )
    assert isinstance(result, ImportResult) and result.indexed and not result.deduplicated
    detail = client.get("/api/v1/text/" + result.asset_id).json()
    assert detail["observation"]["source_metadata"]["source_type"] == "document"
    assert "TXSTAGE718" in detail["observation"]["text_content"]
    repeated = importer.stage(
        ImportRequest(
            scope_id=SCOPE, file_name="note.txt", content_type="text/plain", content=b"", source_path=path
        )
    )
    assert repeated.asset_id == result.asset_id and repeated.deduplicated


@pytest.mark.parametrize(
    "row",
    [
        {"text": ""},
        {"text": 12},
        {"text": "body", "metadata": []},
        {"text": "body", "recorded_at": "nonsense"},
        {"text": "body", "external_id": "../escape"},
    ],
)
def test_unified_text_validation_rejects_invalid_batches_before_writing(setup, row):
    c, client = setup
    r = client.post("/api/v1/text/import", json={"scope_id": SCOPE, "items": [{"text": "valid"}, row]})
    assert r.status_code == 400
    with c.database.connect() as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 0


def test_email_normal_delete_cleans_all_owned_projections_and_preserves_photos(setup):
    c, client, aid = imported(setup)
    add_photo(c, "meal-day", 21)
    build_memory_graph(c).execute(SCOPE)
    original_path = c.asset_repository.get(aid).path
    result = DeleteAsset(c.media_deletion, c.media_storage, c.vector_index).execute(aid)
    assert result and result.deleted and not result.warnings
    assert c.asset_repository.get("meal-day") is not None
    from pathlib import Path

    assert not Path(original_path).exists()
    with c.database.connect() as db:
        assert not db.execute("SELECT 1 FROM email_import_state").fetchone()
        assert not db.execute('SELECT 1 FROM observations WHERE source_type="email"').fetchone()
        assert not db.execute("SELECT 1 FROM text_vectors").fetchone()
        assert not db.execute("SELECT 1 FROM observation_search_terms WHERE asset_id=?", (aid,)).fetchone()
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert client.get("/api/v1/emails/" + aid).status_code == 404
