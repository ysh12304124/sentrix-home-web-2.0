"""事件文档搜索：事件 FTS + ``event_document`` 向量空间，命中展开真实成员。

事件命中不重写成最后一张图的命中；返回 active 文档版本、命中字段/证据与全部成员，
并单独标出实际命中的成员。旧文档/已撤回文档不会被返回。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.memories.events import EventDocument, EventDocumentMember
from sentrix.domain.memories.ports import EventDocumentRepository
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import (
    TextEmbedder,
    VectorHit,
    VectorIndex,
    VectorIndexUnavailable,
    VectorRepository,
)

log = logging.getLogger("sentrix.memories.search_events")

EVENT_SOURCE = "event_document"


@dataclass(frozen=True, slots=True)
class EventSearchResult:
    event_id: str
    document: EventDocument
    matched_fields: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    channels: tuple[str, ...]
    score: float
    matched_observation_ids: tuple[str, ...]
    all_members: tuple[EventDocumentMember, ...]


class SearchEventDocuments:
    def __init__(
        self,
        *,
        documents: EventDocumentRepository,
        embedder: TextEmbedder | None = None,
        index: VectorIndex | None = None,
        vectors: VectorRepository | None = None,
    ) -> None:
        self._documents = documents
        self._embedder = embedder
        self._index = index
        self._vectors = vectors

    def execute(self, *, query: str, scope_id: str, limit: int = 20) -> tuple[EventSearchResult, ...]:
        text = query.strip()
        if not text:
            return ()
        lex = {
            item.event_id: item
            for item in self._documents.search_terms(
                text, scope_id=scope_id or None, limit=max(limit * 4, 20)
            )
        }
        vector_scores: dict[str, float] = {}
        if self._embedder is not None:
            vector = self._embedder.embed_query(text)
            if vector and len(vector) == self._embedder.dimension:
                hits = self._vector_hits(tuple(float(value) for value in vector), scope_id, limit)
                for hit in hits:
                    if hit.source_type != EVENT_SOURCE:
                        continue
                    vector_scores[hit.source_id] = max(
                        vector_scores.get(hit.source_id, 0.0), float(hit.score)
                    )
        event_ids = list(dict.fromkeys([*lex, *vector_scores]))
        documents = self._documents.active_for_events(event_ids)
        results: list[EventSearchResult] = []
        for event_id in event_ids:
            document = documents.get(event_id)
            if document is None:
                target = self._documents.redirect_target(event_id)
                document = self._documents.active_for_event(target) if target is not None else None
                if document is None:
                    continue
                event_id = document.event_id
            lexical = lex.get(event_id)
            channels = tuple(
                name
                for name, present in (
                    ("event_fts", lexical is not None),
                    ("event_vector", event_id in vector_scores),
                )
                if present
            )
            score = float(lexical.score if lexical is not None else 0.0) + float(
                vector_scores.get(event_id, 0.0)
            )
            matched_obs = tuple(lexical.content_observation_ids) if lexical is not None else ()
            results.append(
                EventSearchResult(
                    event_id=event_id,
                    document=document,
                    matched_fields=tuple(lexical.matched_fields) if lexical is not None else (),
                    evidence_refs=tuple(lexical.evidence_refs) if lexical is not None else (),
                    channels=channels,
                    score=score,
                    matched_observation_ids=matched_obs,
                    all_members=document.members,
                )
            )
        results.sort(key=lambda item: (-item.score, item.event_id))
        return tuple(results[: max(1, int(limit))])

    def _vector_hits(self, vector: tuple[float, ...], scope_id: str, limit: int) -> Sequence[VectorHit]:
        if self._index is not None:
            try:
                return self._index.search(
                    space=scheme.EVENT_DOCUMENT_SPACE,
                    vector=vector,
                    limit=max(limit * 2, 20),
                    scope_id=scope_id or None,
                    model_name=self._embedder.model_id if self._embedder is not None else None,
                )
            except VectorIndexUnavailable as error:
                log.warning("事件向量索引不可用，退回权威存储: %s", error)
        if self._vectors is None:
            return ()
        return self._vectors.search_exact(
            space=scheme.EVENT_DOCUMENT_SPACE,
            vector=vector,
            limit=max(limit * 2, 20),
            scope_id=scope_id or None,
            model_name=self._embedder.model_id if self._embedder is not None else None,
        )
