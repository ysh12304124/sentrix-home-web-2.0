from __future__ import annotations

from dataclasses import dataclass

from sentrix.domain.documents.models import DocumentSearchHit
from sentrix.domain.documents.ports import DocumentRepository
from sentrix.domain.media.ports import AssetRepository
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import TextEmbedder, VectorRepository

DOCUMENT_CHUNK_SOURCE = "document_chunk"


@dataclass(frozen=True, slots=True)
class SearchDocumentsRequest:
    text: str
    scope_id: str | None = None
    limit: int = 10
    context_radius: int = 1


class SearchDocuments:
    def __init__(
        self,
        *,
        documents: DocumentRepository,
        assets: AssetRepository,
        embedder: TextEmbedder,
        vectors: VectorRepository,
    ) -> None:
        self._documents, self._assets = documents, assets
        self._embedder, self._vectors = embedder, vectors

    def execute(self, request: SearchDocumentsRequest) -> tuple[DocumentSearchHit, ...]:
        query = request.text.strip()
        if not query:
            return ()
        limit = max(1, request.limit)
        lexical = list(self._documents.lexical_search(query, scope_id=request.scope_id, limit=limit * 4))
        scores: dict[str, float] = {}
        channels: dict[str, set[str]] = {}
        for rank, (lexical_chunk, _raw) in enumerate(lexical, 1):
            scores[lexical_chunk.id] = scores.get(lexical_chunk.id, 0.0) + 1.0 / (60 + rank)
            channels.setdefault(lexical_chunk.id, set()).add("document_fts")
        try:
            vector = self._embedder.embed_query(query)
            vector_hits = (
                self._vectors.search_exact(
                    space=scheme.SEMANTIC_SPACE,
                    vector=vector,
                    limit=limit * 12,
                    scope_id=request.scope_id,
                    model_name=self._embedder.model_id,
                )
                if vector and len(vector) == self._embedder.dimension
                else ()
            )
        except Exception:
            # 文档 FTS 是独立的可用底线；向量侧车暂时不可用时不能拖垮全文检索。
            vector_hits = ()
        if vector_hits:
            rank = 0
            for hit in vector_hits:
                if hit.source_type != DOCUMENT_CHUNK_SOURCE:
                    continue
                rank += 1
                scores[hit.source_id] = scores.get(hit.source_id, 0.0) + 0.8 / (60 + rank)
                channels.setdefault(hit.source_id, set()).add("document_semantic")
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        per_document: dict[str, int] = {}
        results: list[DocumentSearchHit] = []
        for chunk_id in ordered:
            candidate = self._documents.chunk(chunk_id)
            if candidate is None or per_document.get(candidate.document_asset_id, 0) >= 3:
                continue
            asset = self._assets.get(candidate.document_asset_id)
            if asset is None:
                continue
            per_document[candidate.document_asset_id] = per_document.get(candidate.document_asset_id, 0) + 1
            locator = self._documents.locator_for_chunk(candidate)
            results.append(
                DocumentSearchHit(
                    document_asset_id=candidate.document_asset_id,
                    chunk_id=candidate.id,
                    file_name=asset.file_name,
                    text=candidate.text,
                    score=scores[candidate.id],
                    section_path=candidate.section_path,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                    source_locator=locator,
                    channels=tuple(sorted(channels[candidate.id])),
                    context=tuple(self._documents.neighbors(candidate, request.context_radius)),
                )
            )
            if len(results) >= limit:
                break
        return tuple(results)
