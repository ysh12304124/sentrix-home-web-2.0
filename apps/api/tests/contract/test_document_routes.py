import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentrix.api.routes import documents as document_routes
from sentrix.api.routes import imports as import_routes
from sentrix.api.routes import media as media_routes
from sentrix.bootstrap.container import MIGRATIONS_DIR
from sentrix.domain.media.models import AssetStatus, MediaType
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.documents.parsers import DeterministicDocumentParser
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.documents import SqliteDocumentRepository
from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository


class FakeTextEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if text.strip() else []


def _docx_bytes() -> bytes:
    """Small valid DOCX fixture for exercising the actual upload route."""
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>项目计划</w:t></w:r></w:p>
      <w:p><w:r><w:t>预算为二十万元。</w:t></w:r></w:p>
    </w:body></w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture
def document_client(tmp_path: Path) -> TestClient:
    database = Database(tmp_path / "sentrix.sqlite3", MIGRATIONS_DIR)
    database.migrate()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        database=database,
        asset_repository=SqliteAssetRepository(database),
        media_storage=FileSystemMediaStorage(tmp_path / "media"),
        document_repository=SqliteDocumentRepository(database),
        document_parser=DeterministicDocumentParser(),
        text_embedder=FakeTextEmbedder(),
        vector_repository=SqliteVectorRepository(database),
        text_vector_repository=SqliteTextVectorRepository(database),
    )
    app.include_router(document_routes.router, prefix="/api/v1")
    app.include_router(import_routes.router, prefix="/api/v1")
    app.include_router(media_routes.router, prefix="/api/v1")
    return TestClient(app)


def test_document_import_and_search_are_isolated_from_media_observations(document_client: TestClient) -> None:
    client = document_client
    payload = "会议纪要\n\n项目代号是海鸥。\n\n预算为二十万元。".encode()
    imported = client.post(
        "/api/v1/documents/import",
        files={"file": ("minutes.txt", payload, "text/plain")},
        data={"scope_id": "document-scope"},
    )
    assert imported.status_code == 201
    body = imported.json()
    assert body["status"] == "processed"
    assert body["chunk_count"] >= 1

    response = client.get(
        "/api/v1/documents/search",
        params={"q": "海鸥", "scope_id": "document-scope"},
    )
    assert response.status_code == 200
    hit = response.json()["items"][0]
    assert hit["document_asset_id"] == body["asset_id"]
    assert "海鸥" in hit["text"]
    assert "document_fts" in hit["channels"]

    original = client.get(f"/api/v1/assets/{body['asset_id']}/file")
    assert original.status_code == 200
    assert original.content == payload
    with client.app.state.container.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM observations WHERE asset_id = ?", (body["asset_id"],)
            ).fetchone()[0]
            == 0
        )


def test_document_endpoint_rejects_audio(document_client: TestClient) -> None:
    client = document_client
    response = client.post(
        "/api/v1/documents/import",
        files={"file": ("voice.mp3", b"not audio", "audio/mpeg")},
        data={"scope_id": "document-scope"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("file_name", ["notes.txt", "report.docx", "paper.pdf"])
def test_documents_are_rejected_by_media_import(document_client: TestClient, file_name: str) -> None:
    response = document_client.post(
        "/api/v1/media/import",
        files={"file": (file_name, b"document", "application/octet-stream")},
        data={"scope_id": "document-scope"},
    )

    assert response.status_code == 400
    assert "/api/v1/documents/import" in response.json()["detail"]


def test_unified_import_routes_document_to_document_pipeline(document_client: TestClient) -> None:
    response = document_client.post(
        "/api/v1/import",
        files={"file": ("notes.txt", "统一入口文档内容。".encode(), "text/plain")},
        data={"scope_id": "document-scope"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["routed_to"] == "document"
    assert body["result"]["format"] == "txt"
    asset_id = body["result"]["asset_id"]
    with document_client.app.state.container.database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM documents WHERE asset_id = ?", (asset_id,)).fetchone()[0]
            == 1
        )


def test_unified_import_routes_docx_to_document_pipeline(document_client: TestClient) -> None:
    response = document_client.post(
        "/api/v1/import",
        files={
            "file": (
                "plan.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"scope_id": "document-scope"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["routed_to"] == "document"
    assert body["result"]["format"] == "docx"
    asset_id = body["result"]["asset_id"]

    searched = document_client.get(
        "/api/v1/documents/search",
        params={"q": "预算", "scope_id": "document-scope"},
    )
    assert searched.status_code == 200
    assert any(item["document_asset_id"] == asset_id for item in searched.json()["items"])
    with document_client.app.state.container.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents WHERE asset_id = ?", (asset_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM observations WHERE asset_id = ?", (asset_id,)).fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM observations WHERE asset_id = ?", (asset_id,)
            ).fetchone()[0]
            == 0
        )


def test_unified_import_rejects_conflicting_document_metadata(document_client: TestClient) -> None:
    response = document_client.post(
        "/api/v1/import",
        files={"file": ("fake.jpg", b"%PDF-1.4\n", "image/jpeg")},
        data={"scope_id": "document-scope"},
    )

    assert response.status_code == 400
    assert "冲突" in response.json()["detail"]


def test_unified_import_routes_media_to_existing_media_pipeline(
    document_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeMediaImporter:
        def execute(self, request):  # noqa: ANN001, ANN201 - narrow HTTP wiring fake
            captured["request"] = request
            return SimpleNamespace(
                asset_id="asset_media",
                observation_id="obs_media",
                media_type=MediaType.IMAGE,
                status=AssetStatus.PROCESSED,
                deduplicated=False,
                indexed=True,
                placed=False,
                event_id=None,
                video_probed=False,
                video_scenes=0,
                audio_probed=False,
                audio_transcription_status=None,
            )

    monkeypatch.setattr(import_routes, "build_import_assets", lambda *_args, **_kwargs: FakeMediaImporter())
    monkeypatch.setattr(import_routes, "refresh_memory_graph", lambda *_args, **_kwargs: None)

    response = document_client.post(
        "/api/v1/import",
        files={"file": ("photo.jpg", b"jpeg bytes", "image/jpeg")},
        data={"scope_id": "media-scope"},
    )

    assert response.status_code == 201
    assert response.json()["routed_to"] == "media"
    assert response.json()["result"]["asset_id"] == "asset_media"
    assert captured["request"].file_name == "photo.jpg"
