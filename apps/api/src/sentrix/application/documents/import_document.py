from __future__ import annotations

import hashlib
import mimetypes
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentrix.domain.documents.ports import DocumentParser, DocumentRepository
from sentrix.domain.media.models import Asset, AssetStatus, MediaType
from sentrix.domain.media.ports import AssetRepository, MediaStorage
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import TextEmbedder, VectorRecord, VectorRepository

DOCUMENT_CHUNK_SOURCE = "document_chunk"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImportDocumentRequest:
    scope_id: str
    file_name: str
    content_type: str | None
    content: bytes


@dataclass(frozen=True, slots=True)
class ImportDocumentResult:
    asset_id: str
    deduplicated: bool
    status: str
    format: str
    revision: int
    element_count: int
    chunk_count: int
    vector_count: int
    error: str = ""


class ImportDocument:
    """Dedicated document path: source Asset -> structured text -> document-only indices."""

    def __init__(
        self,
        *,
        assets: AssetRepository,
        storage: MediaStorage,
        documents: DocumentRepository,
        parser: DocumentParser,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._assets, self._storage, self._documents = assets, storage, documents
        self._parser, self._embedder, self._vectors, self._clock = parser, embedder, vectors, clock

    def execute(self, request: ImportDocumentRequest) -> ImportDocumentResult:
        if not request.content:
            raise ValueError("empty document")
        if len(request.content) > MAX_DOCUMENT_BYTES:
            raise ValueError("文档超过 50 MiB 限制")
        suffix = Path(request.file_name).suffix.lower()
        if suffix == ".doc":
            raise ValueError("旧式 .doc 暂不支持，请先转换为 .docx")
        if suffix not in {".txt", ".docx", ".pdf"}:
            raise ValueError(f"不支持的文档格式: {suffix or '无扩展名'}")
        digest = hashlib.sha256(request.content).hexdigest()
        existing = self._assets.find_by_hash(request.scope_id, digest)
        if existing is not None:
            if existing.media_type is not MediaType.TEXT:
                raise ValueError("相同内容已作为非文档资产存在，拒绝混入文档索引")
            revision = self._documents.active_revision(existing.id)
            chunks = self._documents.chunks_for_document(existing.id) if revision else ()
            if revision is not None:
                return ImportDocumentResult(
                    existing.id, True, "processed", suffix[1:], revision, 0, len(chunks), 0
                )
            asset = existing
        else:
            now = self._clock()
            asset_id = f"asset_{uuid.uuid4().hex[:12]}"
            path = self._storage.store(asset_id, request.file_name, request.content)
            asset = Asset(
                id=asset_id,
                scope_id=request.scope_id,
                file_name=request.file_name,
                media_type=MediaType.TEXT,
                path=str(path),
                mime_type=self._mime(suffix, request.content_type),
                size_bytes=len(request.content),
                status=AssetStatus.PROCESSING,
                content_sha256=digest,
                captured_at=now,
                captured_location=None,
                created_at=now,
                updated_at=now,
            )
            self._assets.add(asset)
        revision = (self._documents.active_revision(asset.id) or 0) + 1
        try:
            parsed = self._parser.parse(
                asset_id=asset.id,
                scope_id=asset.scope_id,
                path=Path(asset.path),
                file_name=asset.file_name,
                content_type=request.content_type,
                revision=revision,
            )
            self._documents.replace(
                asset_id=asset.id,
                format=parsed.format,
                revision=revision,
                parser_version=parsed.parser_version,
                elements=parsed.elements,
                chunks=parsed.chunks,
            )
            self._assets.update_status(asset.id, AssetStatus.PROCESSED)
        except Exception as error:
            self._documents.fail(
                asset_id=asset.id,
                format=suffix[1:],
                error=str(error),
                parser_version="sentrix-document-parser/1",
            )
            self._assets.update_status(asset.id, AssetStatus.FAILED)
            return ImportDocumentResult(
                asset.id, existing is not None, "failed", suffix[1:], revision, 0, 0, 0, str(error)
            )

        self._vectors.delete_for_asset(source_type=DOCUMENT_CHUNK_SOURCE, asset_id=asset.id)
        vector_count = 0
        for chunk in parsed.chunks:
            try:
                vector = self._embedder.embed_query(chunk.text)
                if not vector or len(vector) != self._embedder.dimension:
                    continue
                self._vectors.upsert(
                    VectorRecord(
                        scope_id=chunk.scope_id,
                        space=scheme.SEMANTIC_SPACE,
                        source_type=DOCUMENT_CHUNK_SOURCE,
                        source_id=chunk.id,
                        model_name=self._embedder.model_id,
                        vector=tuple(vector),
                        metadata={
                            "asset_id": asset.id,
                            "revision": revision,
                            "page_start": chunk.page_start,
                            "page_end": chunk.page_end,
                            "section_path": chunk.section_path,
                        },
                    )
                )
                vector_count += 1
            except Exception:
                # FTS and authoritative chunks stay usable when the embedding sidecar
                # is temporarily unavailable. vector_count exposes the degradation.
                continue
        return ImportDocumentResult(
            asset.id,
            existing is not None,
            "processed",
            parsed.format,
            revision,
            len(parsed.elements),
            len(parsed.chunks),
            vector_count,
        )

    @staticmethod
    def _mime(suffix: str, provided: str | None) -> str:
        canonical = {
            ".txt": "text/plain",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pdf": "application/pdf",
        }
        return (
            canonical.get(suffix)
            or provided
            or mimetypes.guess_type("x" + suffix)[0]
            or "application/octet-stream"
        )
