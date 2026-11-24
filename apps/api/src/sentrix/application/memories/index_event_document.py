"""事件文档向量索引。

新命名空间 ``event_document`` 只放 active 事件正文。**单图事件若规范化文本与那张图
完全相同，直接复用观察向量，不额外嵌入**；多图事件按文本哈希去重后一次嵌入。
向量服务失败不影响事件正文与词法检索。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sentrix.application.retrieval.index_observation_text import compose_text
from sentrix.domain.media.ports import ObservationRepository
from sentrix.domain.memories.events import EventDocument
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import (
    TextEmbedder,
    VectorIndex,
    VectorIndexUnavailable,
    VectorRecord,
    VectorRepository,
)

log = logging.getLogger("sentrix.memories.index_event_document")

EVENT_SOURCE = "event_document"


@dataclass(frozen=True, slots=True)
class EventIndexOutcome:
    indexed: bool
    reused_observation_vector: bool = False
    reason: str = ""


def document_text(document: EventDocument) -> str:
    """多图事件的规范化文本；结构化人物/场景/地点按证据顺序组合。"""
    parts: list[str] = [document.title, document.short_summary, document.summary]
    parts.extend(item.text for item in document.content_items)
    for person in document.participants:
        if person.verified_name:
            parts.append(person.verified_name)
        parts.extend(person.descriptions)
    parts.extend(document.place.physical_locations)
    parts.extend(document.place.scene)
    return " ".join(dict.fromkeys(part.strip() for part in parts if part and part.strip())).strip()


class IndexEventDocument:
    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        index: VectorIndex | None = None,
        observations: ObservationRepository | None = None,
        text_vectors: VectorRepository | None = None,
    ) -> None:
        self._embedder = embedder
        self._vectors = vectors
        self._index = index
        self._observations = observations
        self._text_vectors = text_vectors
        self._cache: dict[str, tuple[float, ...]] = {}

    def execute(self, document: EventDocument) -> EventIndexOutcome:
        existing = self._vectors.get(
            space=scheme.EVENT_DOCUMENT_SPACE,
            source_type=EVENT_SOURCE,
            source_id=document.event_id,
            model_name=self._embedder.model_id,
        )
        text, reused = self._canonical_text(document)
        if not text:
            self._vectors.delete(
                space=scheme.EVENT_DOCUMENT_SPACE,
                source_type=EVENT_SOURCE,
                source_id=document.event_id,
            )
            self._delete_index(document.event_id)
            return EventIndexOutcome(indexed=False, reason="empty_text")
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        if (
            existing is not None
            and str(existing.metadata.get("text_hash") or "") == text_hash
            and str(existing.metadata.get("document_id") or "") == document.id
        ):
            self._mirror(existing)
            return EventIndexOutcome(indexed=True, reused_observation_vector=reused, reason="unchanged")
        vector = self._reuse_observation(document, text)
        reused = vector is not None
        if vector is None:
            vector = self._cache.get(text_hash)
        if vector is None:
            embedded = self._embedder.embed_query(text)
            if not embedded or len(embedded) != self._embedder.dimension:
                return EventIndexOutcome(indexed=False, reason="embedding_unavailable")
            vector = tuple(float(value) for value in embedded)
            self._cache[text_hash] = vector
        self._vectors.delete(
            space=scheme.EVENT_DOCUMENT_SPACE,
            source_type=EVENT_SOURCE,
            source_id=document.event_id,
        )
        record = VectorRecord(
            scope_id=document.scope_id,
            space=scheme.EVENT_DOCUMENT_SPACE,
            source_type=EVENT_SOURCE,
            source_id=document.event_id,
            model_name=self._embedder.model_id,
            vector=vector,
            metadata={
                "event_id": document.event_id,
                "document_id": document.id,
                "document_revision": str(document.revision),
                "input_hash": document.input_hash,
                "text_hash": text_hash,
                "source": "observation_reuse" if reused else "embedded",
            },
        )
        self._vectors.upsert(record)
        self._mirror(record)
        return EventIndexOutcome(indexed=True, reused_observation_vector=reused)

    def _canonical_text(self, document: EventDocument) -> tuple[str, bool]:
        if len(document.members) == 1 and self._observations is not None:
            observation = self._observations.get(document.members[0].observation_id)
            if observation is not None:
                person_text = "\n".join(
                    description for person in document.participants for description in person.descriptions
                )
                return compose_text(observation, person_text), True
        return document_text(document), False

    def _reuse_observation(self, document: EventDocument, text: str) -> tuple[float, ...] | None:
        if self._text_vectors is None or len(document.members) != 1:
            return None
        observation = (
            self._observations.get(document.members[0].observation_id)
            if self._observations is not None
            else None
        )
        if observation is None:
            return None
        person_text = "\n".join(
            description for person in document.participants for description in person.descriptions
        )
        if compose_text(observation, person_text) != text:
            return None
        record = self._text_vectors.get(
            space=scheme.SEMANTIC_SPACE,
            source_type="observation",
            source_id=observation.id,
            model_name=self._embedder.model_id,
        )
        return record.vector if record is not None else None

    def _mirror(self, record: VectorRecord) -> None:
        if self._index is None:
            return
        try:
            self._index.upsert([record])
        except VectorIndexUnavailable as error:
            log.warning("事件文档向量索引不可用（event=%s）: %s", record.source_id, error)

    def _delete_index(self, event_id: str) -> None:
        if self._index is None:
            return
        try:
            self._index.delete_sources([(EVENT_SOURCE, event_id)])
        except VectorIndexUnavailable as error:
            log.warning("事件文档旧索引清理失败（event=%s）: %s", event_id, error)
