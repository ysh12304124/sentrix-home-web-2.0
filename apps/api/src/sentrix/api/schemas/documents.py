from __future__ import annotations

from pydantic import BaseModel


class DocumentImportResponse(BaseModel):
    asset_id: str
    deduplicated: bool
    status: str
    format: str
    revision: int
    element_count: int
    chunk_count: int
    vector_count: int
    error: str = ""


class DocumentContextChunk(BaseModel):
    chunk_id: str
    ordinal: int
    text: str
    page_start: int | None = None
    page_end: int | None = None


class DocumentSearchItem(BaseModel):
    document_asset_id: str
    chunk_id: str
    file_name: str
    text: str
    score: float
    section_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    source_locator: str
    channels: list[str]
    context: list[DocumentContextChunk]


class DocumentSearchResponse(BaseModel):
    query: str
    items: list[DocumentSearchItem]
