"""Text tail recall, modality isolation, evidence lifetime, and atomic retry."""

import pytest

from sentrix.application.media.import_text import ImportTextMemories
from sentrix.application.retrieval.text_memories import IndexTextMemories, RetrieveTextMemories
from sentrix.domain.retrieval.text_memory import split_text
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.media_deletion import SqliteMediaDeletionRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.text_chunks import SqliteTextChunkRepository
from sentrix.infrastructure.persistence.repositories.text_memories import SqliteTextMemoryRepository


class Embedder:
    model_id = "isolated-text"
    dimension = 3
    calls = 0
    failed = False

    def embed_query(self, text):
        self.calls += 1
        if self.failed:
            return ()
        # The tail marker and an unrelated source have orthogonal embeddings.
        return (1.0, 0.0, 0.0) if "ZX742" in text else (0.0, 1.0, 0.0)


@pytest.fixture
def pipeline(migrations_ready_db, tmp_path):
    db = migrations_ready_db
    observations = SqliteObservationRepository(db)
    chunks = SqliteTextChunkRepository(db)
    model = Embedder()
    index = IndexTextMemories(chunks, model)
    importer = ImportTextMemories(
        repository=SqliteTextMemoryRepository(db),
        observations=observations,
        storage=FileSystemMediaStorage(tmp_path / "media"),
        lexical=SqliteLexicalIndex(
            db, observations, assets=SqliteAssetRepository(db), places=SqliteAssetPlaceRepository(db)
        ),
        index=index,
    )
    return db, observations, chunks, model, index, importer, RetrieveTextMemories(chunks, model)


def row(identifier, text, source="email", recorded="2025-05-06T12:00:00+00:00"):
    return {
        "external_id": identifier,
        "text": text,
        "recorded_at": recorded,
        "summary": "Shared introduction",
        "metadata": {"source_type": source},
    }


def test_long_tail_recall_is_filtered_before_limit_and_deduplicated(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    body = "Generic introduction. " * 180 + " Confirmation ZX742: payment is 23 pounds."
    result = importer.execute(
        "texts",
        [
            row("receipt", body),
            row("note", "ZX742 " * 200, "note"),
            row("older", "ZX742", "email", "2021-01-01"),
        ],
    )
    assert result["status"] == "completed"
    found = search.execute("ZX742 payment", "texts", source_type="email", after="2025-01-01", limit=1)
    assert not found.degraded and len(found.hits) == 1
    assert found.hits[0].external_id == "receipt" and found.hits[0].start > 512
    assert "ZX742" in found.hits[0].text
    assert not search.execute("ZX742", "other").hits
    assert chunks.coverage("texts", model.model_id) == (3, 3)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_vectors").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM text_vectors").fetchone()[0] == 0
    calls = model.calls
    assert importer.execute("texts", [row("receipt", body)])["skipped"] == 1
    assert model.calls == calls


def test_edit_invalidates_old_chunks_and_delete_cascades(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    aid = importer.execute("texts", [row("receipt", "old ZX742")])["items"][0]["asset_id"]
    observation = obs.list_by_asset(aid)[0]
    with db.connect() as conn, conn:
        conn.execute(
            "UPDATE observations SET text_content='replacement',source_text='replacement',"
            "revision=revision+1 WHERE id=?",
            (observation.id,),
        )
    assert not search.execute("ZX742", "texts").hits
    assert search.execute("ZX742", "texts").reason == "text_index_incomplete"
    with pytest.raises(RuntimeError, match="changed"):
        index.index(observation)
    current = obs.get(observation.id)
    index.index(current)
    assert search.execute("replacement", "texts").hits[0].revision == current.revision
    assert SqliteMediaDeletionRepository(db).delete_asset(aid)
    assert not search.execute("replacement", "texts").hits
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM text_memory_chunks").fetchone()[0] == 0


def test_embedding_failure_keeps_body_and_retry_is_visible(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    model.failed = True
    result = importer.execute("texts", [row("receipt", "ZX742")])
    assert result["status"] == "partial" and result["errors"]
    assert obs.list_by_asset(result["items"][0]["asset_id"])[0].text_content == "ZX742"
    assert search.execute("ZX742", "texts").degraded
    model.failed = False
    result = importer.execute("texts", [row("receipt", "ZX742")])
    assert result["status"] == "completed" and result["skipped"] == 1
    assert search.execute("ZX742", "texts").hits


def test_chunks_cover_every_character_and_preserve_offsets():
    body = "邮件正文与 emoji 🍊\n" * 500
    spans = split_text(body)
    assert spans[0][0] == 0 and spans[-1][1] == len(body)
    assert all(body[start:end] == text and len(text) <= 256 for start, end, text in spans)
    assert all(right[0] < left[1] for left, right in zip(spans, spans[1:], strict=False))


def test_tool_reads_matching_tail_and_cites_only_authorized_live_sources(pipeline):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from sentrix.application.assistant.tools import AssistantToolbox
    from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository

    db, obs, chunks, model, index, importer, search = pipeline
    body = "Generic introduction. " * 180 + " Confirmation ZX742: payment is 23 pounds."
    importer.execute("texts", [row("receipt", body)])

    class NoMedia:
        def execute(self, *args, **kwargs):
            raise AssertionError("Text retrieval must not invoke image/video retrieval")

    box = AssistantToolbox(
        retrieval=NoMedia(),
        assets=SqliteAssetRepository(db),
        result_sets=SqliteResultSetRepository(db),
        people=SimpleNamespace(),
        memory_facts=SimpleNamespace(),
        faces=SimpleNamespace(),
        observations=obs,
        photo_inspector=SimpleNamespace(),
        person_crops=SimpleNamespace(),
        text_retrieval=search,
    )
    result = box.execute("search_text_memories", {"query": "ZX742"}, scope_id="texts", now=datetime.now(UTC))
    preview = result.model_visible["preview"][0]
    rs = result.observation["result_set_id"]
    stored = SqliteResultSetRepository(db).get(rs)
    assert stored.text_offsets
    read = box.execute(
        "read_source_text",
        {"result_set_id": rs, "asset_handle": preview["handle"]},
        scope_id="texts",
        now=datetime.now(UTC),
    )
    assert read.ok and "ZX742" in read.model_visible["text"]
    assert read.model_visible["offset"] == preview["offset"] > 512
    citations = box.text_citations([preview["citation_handle"]] * 2, [rs], scope_id="texts")
    assert len(citations) == 1 and citations[0]["evidence_id"] == "receipt"
    assert box.text_citations(["receipt"], [rs], scope_id="texts")[0]["evidence_id"] == "receipt"
    assert not box.text_citations(["not-returned"], [rs], scope_id="texts")
    assert box.text_citations([preview["citation_handle"]], [rs], scope_id="other") == []
    assert box.text_citations([preview["citation_handle"]], [], scope_id="texts") == []
    assert SqliteMediaDeletionRepository(db).delete_asset(citations[0]["asset_id"])
    assert box.text_citations([preview["citation_handle"]], [rs], scope_id="texts") == []


def test_text_pool_reranks_before_six_handle_limit(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    importer.execute("texts", [row(f"source-{i}", f"ZX742 payment detail {i}") for i in range(12)])

    class Ranker:
        model_id = "test-cross-encoder"

        def rerank(self, query, documents):
            assert len(documents) == 12
            return [100.0 if "detail 11" in d else 0.0 for d in documents]

    ranked = RetrieveTextMemories(chunks, model, reranker=Ranker()).execute("ZX742 payment", "texts")
    assert len(ranked.candidates) == 12
    assert len(ranked.hits) == 6
    assert ranked.hits[0].external_id == "source-11"
    assert ranked.rerank_status == "test-cross-encoder"

    class Broken:
        model_id = "unavailable"

        def rerank(self, query, documents):
            return []

    degraded = RetrieveTextMemories(chunks, model, reranker=Broken()).execute("ZX742", "texts")
    assert len(degraded.hits) == 6
    assert degraded.degraded and degraded.reason == "text_reranker_unavailable"


def test_fts_is_source_level_uses_full_body_and_applies_date_before_limit(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    importer.execute(
        "texts",
        [
            row(
                "target",
                "Date: 2025-05-06\nSubject: Access token\n"
                + "Unrelated introduction. " * 80
                + " UniqueOrchid deadline: Friday 17:00.",
            ),
            row("other", "UniqueOrchid " * 100, "note"),
        ],
    )
    chunks.prepare_sources("texts")
    hits = chunks.lexical("UniqueOrchid deadline", "texts", model.model_id, source_type="email", limit=1)
    assert len(hits) == 1
    assert not chunks.lexical("UniqueOrchid", "texts", model.model_id, after="2026-01-01", limit=1)
    result = search.execute("UniqueOrchid deadline", "texts", source_type="email/document")
    assert "target" in [h.external_id for h in result.hits]
    assert dict(result.channels)["fts_bm25"]
    with pytest.raises(ValueError, match="source_type"):
        search.execute("UniqueOrchid", "texts", source_type="email AND document")
    # Date-only upper bounds include the entire last day.
    assert search.execute("UniqueOrchid", "texts", source_type="email", before="2025-05-06").hits


def test_source_relations_follow_live_revision_scope_and_deletion(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    a = importer.execute(
        "texts",
        [
            row("first", "Subject: Trip Garden Planning\nBooking reference: A123456."),
            row("followup", "Subject: Re: Trip Garden Planning\nBooking reference: A123456."),
            row("unrelated", "Subject: Winter Coding Meeting\nDifferent topic."),
        ],
    )["items"]
    importer.execute("other", [row("foreign", "Booking reference: A123456.")])
    seed = a[0]["asset_id"]
    linked = a[1]["asset_id"]
    assert [r.asset_id for r in chunks.related("texts", [seed], model.model_id)] == [linked]
    observation = obs.list_by_asset(linked)[0]
    with db.connect() as conn, conn:
        conn.execute(
            "UPDATE observations SET revision=revision+1,source_text='Removed reference' WHERE id=?",
            (observation.id,),
        )
    assert not chunks.related("texts", [seed], model.model_id)
    assert not chunks.lexical("A123456", "texts", model.model_id, limit=10)[1:]
    index.index(obs.get(observation.id))
    assert not chunks.related("texts", [seed], model.model_id)
    seed_observation_id = obs.list_by_asset(seed)[0].id
    assert SqliteMediaDeletionRepository(db).delete_asset(seed)
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM text_source_fts WHERE observation_id=?", (seed_observation_id,)
            ).fetchone()[0]
            == 0
        )
    assert not chunks.lexical("A123456", "texts", model.model_id)


def test_question_time_context_reaches_text_reranker(pipeline):
    db, obs, chunks, model, index, importer, search = pipeline
    importer.execute("texts", [row("target", "Subject: Seasonal dinner\nDinner ticket cost: 20 pounds.")])

    class Ranker:
        model_id = "context-test"

        def rerank(self, query, documents):
            assert "Today is May 6, 2025" in query
            assert "Source date: 2025-05-06" in documents[0]
            return [1.0] * len(documents)

    result = RetrieveTextMemories(chunks, model, reranker=Ranker()).execute(
        "dinner ticket cost", "texts", question="Today is May 6, 2025. How much is dinner?"
    )
    assert result.rerank_status == "context-test"


def test_legacy_projection_backfill_is_concurrent_and_does_not_reembed(pipeline):
    from concurrent.futures import ThreadPoolExecutor

    db, obs, chunks, model, index, importer, search = pipeline
    importer.execute(
        "texts",
        [
            row("one", "Subject: Distributed Planning Meeting\nBooking reference: A123456."),
            row("two", "Subject: Re: Distributed Planning Meeting\nBooking reference: A123456."),
        ],
    )
    embedded = model.calls
    with db.connect() as conn, conn:
        conn.execute("DELETE FROM text_source_fts")
        conn.execute("DELETE FROM text_source_projection")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(chunks.prepare_sources, ["texts"] * 8))
    assert model.calls == embedded
    assert len(chunks.lexical("A123456", "texts", model.model_id)) == 2
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM text_source_fts").fetchone()[0] == 2
