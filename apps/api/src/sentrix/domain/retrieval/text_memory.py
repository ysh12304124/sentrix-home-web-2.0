"""Text-only chunks and storage contract; source IDs survive chunking and fusion."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TextChunk:
    asset_id: str
    observation_id: str
    revision: int
    external_id: str
    source_type: str
    start: int
    end: int
    text: str
    vector: tuple[float, ...] = ()
    title: str = ""
    recorded_at: str = ""


@dataclass(frozen=True, slots=True)
class TextRelation:
    seed_asset_id: str
    asset_id: str
    kind: str


class TextChunkRepository(Protocol):
    def prepare_sources(self, scope_id: str) -> None: ...
    def lexical(
        self,
        query: str,
        scope_id: str,
        model_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
        limit: int = 64,
    ) -> Sequence[tuple[str, float]]: ...
    def related(
        self,
        scope_id: str,
        seeds: Sequence[str],
        model_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
    ) -> Sequence[TextRelation]: ...
    def similarities(
        self, chunks: Sequence[TextChunk], vector: Sequence[float], model_id: str
    ) -> Sequence[float]: ...
    def coverage(
        self, scope_id: str, model_id: str, *, source_type: str = "", after: str = "", before: str = ""
    ) -> tuple[int, int]: ...
    def indexed(self, observation_id: str, revision: int, model_id: str) -> bool: ...
    def replace(
        self, observation_id: str, revision: int, model_id: str, chunks: Sequence[TextChunk]
    ) -> None: ...

    def read(
        self,
        scope_id: str,
        model_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
    ) -> Sequence[TextChunk]: ...


@dataclass(frozen=True, slots=True)
class TextSearchResult:
    hits: tuple[TextChunk, ...]
    degraded: bool = False
    reason: str = ""
    candidates: tuple[TextChunk, ...] = ()
    rerank_status: str = "rrf"
    relations: tuple[TextRelation, ...] = ()
    channels: tuple[tuple[str, tuple[str, ...]], ...] = ()


def split_text(text: str, width: int = 256, overlap: int = 48) -> tuple[tuple[int, int, str], ...]:
    """Bounded character windows with overlap; never truncate the source body."""
    if not 0 <= overlap < width:
        raise ValueError("overlap must be smaller than width")
    result = []
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        result.append((start, end, text[start:end]))
        if end == len(text):
            break
        start = end - overlap
    return tuple(result)
