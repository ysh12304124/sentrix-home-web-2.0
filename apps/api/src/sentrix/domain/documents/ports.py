from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sentrix.domain.documents.models import DocumentChunk, DocumentElement, ParsedDocument


class DocumentParser(Protocol):
    def parse(
        self,
        *,
        asset_id: str,
        scope_id: str,
        path: Path,
        file_name: str,
        content_type: str | None,
        revision: int,
    ) -> ParsedDocument: ...


class DocumentRepository(Protocol):
    def active_revision(self, asset_id: str) -> int | None: ...
    def replace(
        self,
        *,
        asset_id: str,
        format: str,
        revision: int,
        parser_version: str,
        elements: Sequence[DocumentElement],
        chunks: Sequence[DocumentChunk],
    ) -> None: ...
    def fail(self, *, asset_id: str, format: str, error: str, parser_version: str) -> None: ...
    def chunks_for_document(self, asset_id: str) -> Sequence[DocumentChunk]: ...
    def chunk(self, chunk_id: str) -> DocumentChunk | None: ...
    def neighbors(self, chunk: DocumentChunk, radius: int = 1) -> Sequence[DocumentChunk]: ...
    def locator_for_chunk(self, chunk: DocumentChunk) -> str: ...
    def lexical_search(
        self, text: str, *, scope_id: str | None, limit: int
    ) -> Sequence[tuple[DocumentChunk, float]]: ...
