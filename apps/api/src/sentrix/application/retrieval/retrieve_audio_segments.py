"""Direct retrieval for timestamped ASR segments.

Unlike ``RetrieveTextAnn``, this use case deliberately never groups hits by
``asset_id``: an ASR segment is the evidence unit, while an Asset remains only
its provenance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import TextEmbedder, VectorRepository

SOURCE = "audio_segment"
_FETCH_FLOOR = 100


@dataclass(frozen=True, slots=True)
class AudioSegmentSearchRequest:
    text: str
    scope_id: str | None = None
    limit: int = 5


@dataclass(frozen=True, slots=True)
class AudioSegmentSearchHit:
    segment_id: str
    asset_id: str
    score: float
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class AudioSegmentSearchResult:
    hits: Sequence[AudioSegmentSearchHit] = field(default_factory=tuple)


class RetrieveAudioSegments:
    """Recall concrete ASR segments from the semantic vector space.

    ``VectorRepository`` has no source-type filter, so this intentionally
    over-fetches before filtering.  It is read-only and does not alter the
    existing observation/text channel.
    """

    def __init__(self, *, embedder: TextEmbedder, vectors: VectorRepository) -> None:
        self._embedder = embedder
        self._vectors = vectors

    def execute(self, request: AudioSegmentSearchRequest) -> AudioSegmentSearchResult:
        query_text = request.text.strip()
        if not query_text:
            return AudioSegmentSearchResult()
        vector = self._embedder.embed_query(query_text)
        if not vector or len(vector) != self._embedder.dimension:
            return AudioSegmentSearchResult()
        limit = max(1, request.limit)
        candidates = self._vectors.search_exact(
            space=scheme.SEMANTIC_SPACE,
            vector=vector,
            limit=max(_FETCH_FLOOR, limit * 20),
            scope_id=request.scope_id,
            model_name=self._embedder.model_id,
        )
        hits: list[AudioSegmentSearchHit] = []
        for candidate in candidates:
            if candidate.source_type != SOURCE:
                continue
            metadata = candidate.metadata
            asset_id = str(metadata.get("asset_id") or "")
            try:
                start_ms, end_ms = int(metadata["start_ms"]), int(metadata["end_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            hits.append(
                AudioSegmentSearchHit(
                    candidate.source_id,
                    asset_id,
                    candidate.score,
                    start_ms,
                    end_ms,
                )
            )
            if len(hits) >= limit:
                break
        return AudioSegmentSearchResult(tuple(hits))
