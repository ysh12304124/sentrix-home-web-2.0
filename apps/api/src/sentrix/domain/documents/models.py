from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DocumentElement:
    id: str
    document_asset_id: str
    revision: int
    ordinal: int
    kind: str
    text: str
    source_locator: str
    parser_version: str
    page_no: int | None = None
    section_path: str = ""
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    document_asset_id: str
    scope_id: str
    revision: int
    ordinal: int
    text: str
    start_element_id: str
    end_element_id: str
    section_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    token_count: int = 0


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    format: str
    parser_version: str
    elements: tuple[DocumentElement, ...]
    chunks: tuple[DocumentChunk, ...]


@dataclass(frozen=True, slots=True)
class DocumentSearchHit:
    document_asset_id: str
    chunk_id: str
    file_name: str
    text: str
    score: float
    section_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    source_locator: str = ""
    channels: tuple[str, ...] = ()
    context: tuple[DocumentChunk, ...] = field(default_factory=tuple)
