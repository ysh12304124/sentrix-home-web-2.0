from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from sentrix.api.deps import get_container
from sentrix.api.schemas.documents import (
    DocumentContextChunk,
    DocumentImportResponse,
    DocumentSearchItem,
    DocumentSearchResponse,
)
from sentrix.application.documents.import_document import ImportDocument, ImportDocumentRequest
from sentrix.application.documents.search_documents import SearchDocuments, SearchDocumentsRequest
from sentrix.bootstrap.container import Container

router = APIRouter(prefix="/documents", tags=["documents"])


def build_import_document(container: Container) -> ImportDocument:
    return ImportDocument(
        assets=container.asset_repository,
        storage=container.media_storage,
        documents=container.document_repository,
        parser=container.document_parser,
        embedder=container.text_embedder,
        vectors=container.text_vector_repository,
    )


def build_search_documents(container: Container) -> SearchDocuments:
    return SearchDocuments(
        documents=container.document_repository,
        assets=container.asset_repository,
        embedder=container.text_embedder,
        vectors=container.text_vector_repository,
    )


@router.post("/import", response_model=DocumentImportResponse, status_code=201)
async def import_document(
    file: UploadFile = File(...),
    scope_id: str = Form("home-default"),
    container: Container = Depends(get_container),
) -> DocumentImportResponse:
    content = await file.read()
    try:
        result = build_import_document(container).execute(
            ImportDocumentRequest(
                scope_id=scope_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type,
                content=content,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentImportResponse(**dataclasses.asdict(result))


@router.get("/search", response_model=DocumentSearchResponse)
def search_documents(
    q: str = Query(..., min_length=1, max_length=500),
    scope_id: str = "home-default",
    limit: int = Query(10, ge=1, le=50),
    context_radius: int = Query(1, ge=0, le=2),
    container: Container = Depends(get_container),
) -> DocumentSearchResponse:
    hits = build_search_documents(container).execute(
        SearchDocumentsRequest(
            text=q,
            scope_id=scope_id,
            limit=limit,
            context_radius=context_radius,
        )
    )
    return DocumentSearchResponse(
        query=q,
        items=[
            DocumentSearchItem(
                document_asset_id=hit.document_asset_id,
                chunk_id=hit.chunk_id,
                file_name=hit.file_name,
                text=hit.text,
                score=hit.score,
                section_path=hit.section_path,
                page_start=hit.page_start,
                page_end=hit.page_end,
                source_locator=hit.source_locator,
                channels=list(hit.channels),
                context=[
                    DocumentContextChunk(
                        chunk_id=chunk.id,
                        ordinal=chunk.ordinal,
                        text=chunk.text,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                    )
                    for chunk in hit.context
                ],
            )
            for hit in hits
        ],
    )
