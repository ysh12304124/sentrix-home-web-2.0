"""S5：事件 FTS、单图事件向量复用与事件命中成员展开。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sentrix.application.memories.index_event_document import IndexEventDocument
from sentrix.application.memories.search_events import SearchEventDocuments
from sentrix.domain.memories.events import (
    EventContentItem,
    EventCoverage,
    EventDocument,
    EventDocumentMember,
    EventPersonRef,
    EventPlaceProjection,
    EventTimeProjection,
)
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorHit, VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.event_documents import (
    SqliteEventDocumentRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

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
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, '海边合影', '海边合影', 2, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
    SqliteEventRepository(db).create(
        event_id="event_a",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="海边",
        admin_district="",
        activity="聚餐",
        summary="春节聚餐",
        confidence=0.8,
        aggregation_score=0.9,
        aggregation_breakdown={},
    )


def _document(title: str = "春节聚餐", description: str = "穿灰色上衣的人") -> EventDocument:
    return EventDocument(
        id="",
        scope_id="album1",
        event_id="event_a",
        revision=0,
        status="staged",
        doc_version="event-doc-v1",
        title=title,
        short_summary=title,
        summary="春节聚餐",
        summary_source="model",
        summary_state="model_generated_unverified",
        source_modalities=("image",),
        content_items=(
            EventContentItem(
                item_id="obs_a",
                text="海边合影",
                observation_id="obs_a",
                asset_id="asset_a",
                observation_revision=2,
                evidence_refs=("observation:obs_a",),
            ),
        ),
        members=(
            EventDocumentMember(
                observation_id="obs_a",
                asset_id="asset_a",
                media_type="image",
                observation_revision=2,
                captured_at=NOW,
                capture_precision="datetime",
                timezone_state="offset_only",
                scene="海边",
                physical_place="浙江省杭州市",
                text="海边合影",
                text_state="available",
                person_refs=(
                    EventPersonRef(
                        person_id="person_mom",
                        display_label="妈妈",
                        verified_name="妈妈",
                        identity_states=("user_verified",),
                        descriptions=(description,),
                        face_instance_ids=("face_a",),
                        occurrence_count=1,
                        pending=False,
                    ),
                ),
                evidence_refs=("observation:obs_a",),
            ),
        ),
        participants=(
            EventPersonRef(
                person_id="person_mom",
                display_label="妈妈",
                verified_name="妈妈",
                identity_states=("user_verified",),
                descriptions=(description,),
                face_instance_ids=("face_a",),
                occurrence_count=1,
                pending=False,
            ),
        ),
        time=EventTimeProjection(
            capture_start=NOW,
            capture_end=NOW,
            precision="datetime",
            timezone_state="absolute",
            comparable=True,
            member_dates=("2026-11-24",),
            occurrence_time_state="unknown",
            display_time="2026-11-24",
        ),
        place=EventPlaceProjection(
            scene=("海边",),
            physical_locations=("浙江省杭州市",),
            display_place="浙江省杭州市",
        ),
        coverage=EventCoverage(included_members=1),
        dependencies={"schema_version": "event-doc-v1"},
        input_hash=title,
    )


def test_publish_writes_and_replaces_event_fts_terms(migrations_ready_db: Database) -> None:
    _seed(migrations_ready_db)
    repository = SqliteEventDocumentRepository(migrations_ready_db)
    repository.publish(_document())
    by_title = repository.search_terms("聚餐", scope_id="album1", limit=10)
    assert [item.event_id for item in by_title] == ["event_a"]
    assert "summary" in by_title[0].matched_fields
    by_content = repository.search_terms("海边合影", scope_id="album1", limit=10)
    assert by_content[0].content_observation_ids == ("obs_a",)
    by_person = repository.search_terms("妈妈", scope_id="album1", limit=10)
    assert "participant" in by_person[0].matched_fields

    second = replace(
        _document("新的标题"), summary="新的摘要", short_summary="新的标题", input_hash="new-hash"
    )
    repository.publish(second)
    assert repository.search_terms("聚餐", scope_id="album1", limit=10) == ()


class FakeVectors:
    def __init__(self, stored: dict[tuple[str, str, str], VectorRecord] | None = None) -> None:
        self.stored = stored or {}

    def get(self, *, space, source_type, source_id, model_name=None):  # noqa: ANN001
        return self.stored.get((space, source_type, source_id))

    def delete(self, *, space, source_type, source_id, model_name=None):  # noqa: ANN001
        keys = [key for key in self.stored if key[:3] == (space, source_type, source_id)]
        for key in keys:
            self.stored.pop(key)
        return len(keys)

    def upsert(self, record: VectorRecord) -> None:
        self.stored[(record.space, record.source_type, record.source_id)] = record


class FakeIndex:
    def __init__(self) -> None:
        self.records: list[VectorRecord] = []

    def upsert(self, records):  # noqa: ANN001
        self.records.extend(records)
        return len(records)

    def delete_sources(self, sources):  # noqa: ANN001
        return None


class FakeEmbedder:
    model_id = "text-model"
    dimension = 2

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [1.0, 0.0]


def test_single_image_event_reuses_observation_vector_without_embedding() -> None:
    observation_vector = VectorRecord(
        scope_id="album1",
        space=scheme.SEMANTIC_SPACE,
        source_type="observation",
        source_id="obs_a",
        model_name="text-model",
        vector=(0.6, 0.8),
        metadata={},
    )
    text_vectors = FakeVectors({(scheme.SEMANTIC_SPACE, "observation", "obs_a"): observation_vector})
    vectors = FakeVectors()
    index = FakeIndex()
    embedder = FakeEmbedder()

    class Observations:
        def get(self, observation_id):  # noqa: ANN001
            from sentrix.domain.media.models import Observation

            return Observation(
                id="obs_a",
                scope_id="album1",
                asset_id="asset_a",
                source_type="import",
                caption="海边",
                captured_at=NOW,
                confidence=0.9,
                revision=2,
                created_at=NOW,
                updated_at=NOW,
            )

    outcome = IndexEventDocument(
        embedder=embedder,
        vectors=vectors,
        index=index,
        observations=Observations(),
        text_vectors=text_vectors,
    ).execute(_document())

    assert outcome.indexed is True and outcome.reused_observation_vector is True
    assert embedder.calls == []
    record = vectors.stored[(scheme.EVENT_DOCUMENT_SPACE, "event_document", "event_a")]
    assert record.vector == (0.6, 0.8)
    assert record.metadata["source"] == "observation_reuse"
    assert index.records[0].source_id == "event_a"


def test_event_search_merges_fts_and_vector_and_expands_members(migrations_ready_db: Database) -> None:
    _seed(migrations_ready_db)
    documents = SqliteEventDocumentRepository(migrations_ready_db)
    documents.publish(_document())

    class SearchingIndex(FakeIndex):
        def search(self, *, space, vector, limit, scope_id=None, model_name=None):  # noqa: ANN001
            if space != scheme.EVENT_DOCUMENT_SPACE:
                return ()
            return (
                VectorHit(
                    source_id="event_a",
                    scope_id="album1",
                    source_type="event_document",
                    model_name="text-model",
                    score=0.91,
                    metadata={"document_id": "doc"},
                ),
            )

    results = SearchEventDocuments(
        documents=documents,
        embedder=FakeEmbedder(),
        index=SearchingIndex(),
    ).execute(query="海边合影", scope_id="album1")

    assert len(results) == 1
    hit = results[0]
    assert set(hit.channels) == {"event_fts", "event_vector"}
    assert hit.matched_observation_ids == ("obs_a",)
    assert [member.observation_id for member in hit.all_members] == ["obs_a"]
