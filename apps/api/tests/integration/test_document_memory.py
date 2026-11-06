from __future__ import annotations

import io
import zipfile
from pathlib import Path

from sentrix.application.documents.import_document import ImportDocument, ImportDocumentRequest
from sentrix.application.documents.search_documents import SearchDocuments, SearchDocumentsRequest
from sentrix.bootstrap.container import MIGRATIONS_DIR
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.documents.parsers import DeterministicDocumentParser
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.documents import SqliteDocumentRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository


class KeywordEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        if "预算" in text or "成本" in text or "budget" in lowered or "cost" in lowered:
            return [1.0, 0.0]
        return [0.0, 1.0] if lowered.strip() else []


class BrokenEmbedder(KeywordEmbedder):
    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding sidecar unavailable")


def _docx() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>项目计划</w:t></w:r></w:p>
      <w:p><w:r><w:t>预算为二十万元。</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


def _build(tmp_path: Path):
    database = Database(tmp_path / "sentrix.sqlite3", MIGRATIONS_DIR)
    database.migrate()
    assets = SqliteAssetRepository(database)
    documents = SqliteDocumentRepository(database)
    vectors = SqliteVectorRepository(database)
    embedder = KeywordEmbedder()
    importer = ImportDocument(
        assets=assets,
        storage=FileSystemMediaStorage(tmp_path / "media"),
        documents=documents,
        parser=DeterministicDocumentParser(),
        embedder=embedder,
        vectors=vectors,
    )
    search = SearchDocuments(documents=documents, assets=assets, embedder=embedder, vectors=vectors)
    return database, importer, search


def test_txt_import_storage_fts_semantic_context_and_isolation(tmp_path: Path) -> None:
    database, importer, search = _build(tmp_path)
    result = importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="plan.txt",
            content_type="text/plain",
            content="第一章\n\n项目预算为二十万元。\n\n交付时间是十二月。".encode(),
        )
    )

    assert result.status == "processed"
    assert result.chunk_count > 0
    assert result.vector_count == result.chunk_count
    lexical = search.execute(SearchDocumentsRequest(text="交付时间", scope_id="docs"))
    semantic = search.execute(SearchDocumentsRequest(text="项目成本", scope_id="docs"))
    assert lexical and lexical[0].document_asset_id == result.asset_id
    assert semantic and "预算" in semantic[0].text
    assert lexical[0].context

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        sources = {row[0] for row in connection.execute("SELECT DISTINCT source_type FROM memory_vectors")}
        assert sources == {"document_chunk"}
        assert (
            connection.execute("SELECT COUNT(*) FROM document_chunk_fts").fetchone()[0] == result.chunk_count
        )


def test_docx_preserves_heading_table_and_deduplicates(tmp_path: Path) -> None:
    database, importer, search = _build(tmp_path)
    request = ImportDocumentRequest(
        scope_id="docs",
        file_name="plan.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content=_docx(),
    )
    first = importer.execute(request)
    second = importer.execute(request)
    assert first.status == "processed"
    assert second.deduplicated is True
    assert second.asset_id == first.asset_id
    hits = search.execute(SearchDocumentsRequest(text="金额", scope_id="docs"))
    assert hits and hits[0].section_path == "项目计划"
    with database.connect() as connection:
        kinds = {row[0] for row in connection.execute("SELECT kind FROM document_elements")}
        assert {"heading", "paragraph", "table"} <= kinds
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1


def test_rejects_unknown_file_without_entering_other_modalities(tmp_path: Path) -> None:
    _database, importer, _search = _build(tmp_path)
    try:
        importer.execute(
            ImportDocumentRequest(
                scope_id="docs",
                file_name="voice.mp3",
                content_type="audio/mpeg",
                content=b"audio",
            )
        )
    except ValueError as error:
        assert "不支持" in str(error)
    else:
        raise AssertionError("audio must not enter document import")


def test_pdf_text_is_searchable_with_page_locator(tmp_path: Path) -> None:
    _database, importer, search = _build(tmp_path)
    result = importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="budget.pdf",
            content_type="application/pdf",
            content=_pdf("Mars budget is 200000 yuan."),
        )
    )
    assert result.status == "processed"
    hits = search.execute(SearchDocumentsRequest(text="Mars", scope_id="docs"))
    assert hits and hits[0].page_start == 1
    assert '"page": 1' in hits[0].source_locator


def test_long_pdf_element_is_split_to_the_document_chunk_limit(tmp_path: Path) -> None:
    database, importer, _search = _build(tmp_path)
    result = importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="long.pdf",
            content_type="application/pdf",
            content=_pdf("evidence " * 900),
        )
    )

    chunks = SqliteDocumentRepository(database).chunks_for_document(result.asset_id)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 2200 for chunk in chunks)
    assert {chunk.page_start for chunk in chunks} == {1}


def test_fts_search_survives_embedding_outage(tmp_path: Path) -> None:
    database, importer, _search = _build(tmp_path)
    result = importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="fallback.txt",
            content_type="text/plain",
            content="系统代号是北斗。".encode(),
        )
    )
    assets = SqliteAssetRepository(database)
    documents = SqliteDocumentRepository(database)
    search = SearchDocuments(
        documents=documents,
        assets=assets,
        embedder=BrokenEmbedder(),
        vectors=SqliteVectorRepository(database),
    )

    hits = search.execute(SearchDocumentsRequest(text="北斗", scope_id="docs"))

    assert hits and hits[0].document_asset_id == result.asset_id
    assert hits[0].channels == ("document_fts",)


def test_fts_scores_partial_token_matches_for_a_long_question(tmp_path: Path) -> None:
    """Long questions must not require every stopword to occur in one chunk."""
    database, importer, _search = _build(tmp_path)
    importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="generic.txt",
            content_type="text/plain",
            content=b"What are the results of the report as of the year?",
        )
    )
    result = importer.execute(
        ImportDocumentRequest(
            scope_id="docs",
            file_name="amd.txt",
            content_type="text/plain",
            content=b"AMD sells Ryzen processors and Radeon graphics products.",
        )
    )
    search = SearchDocuments(
        documents=SqliteDocumentRepository(database),
        assets=SqliteAssetRepository(database),
        embedder=BrokenEmbedder(),
        vectors=SqliteVectorRepository(database),
    )

    hits = search.execute(
        SearchDocumentsRequest(
            text="What are the major products and services that AMD sells as of FY22?",
            scope_id="docs",
        )
    )

    assert hits and hits[0].document_asset_id == result.asset_id
    assert hits[0].channels == ("document_fts",)
