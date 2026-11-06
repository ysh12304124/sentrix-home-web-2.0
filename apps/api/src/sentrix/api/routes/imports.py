from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from sentrix.api.deps import get_container
from sentrix.api.routes.documents import build_import_document
from sentrix.api.routes.media import build_import_assets, refresh_memory_graph, to_import_response
from sentrix.api.schemas.documents import DocumentImportResponse
from sentrix.api.schemas.imports import UnifiedImportResponse
from sentrix.application.documents.import_document import ImportDocumentRequest
from sentrix.application.imports import UploadRoute, classify_upload
from sentrix.application.media.import_assets import ImportRequest
from sentrix.bootstrap.container import Container

router = APIRouter(tags=["imports"])


@router.post("/import", response_model=UnifiedImportResponse, status_code=201)
async def import_file(
    file: UploadFile = File(...),
    scope_id: str = Form("home-default"),
    container: Container = Depends(get_container),
) -> UnifiedImportResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    file_name = file.filename or "unnamed"
    try:
        route = classify_upload(file_name, file.content_type, content)
        if route is UploadRoute.DOCUMENT:
            document_result = build_import_document(container).execute(
                ImportDocumentRequest(
                    scope_id=scope_id,
                    file_name=file_name,
                    content_type=file.content_type,
                    content=content,
                )
            )
            return UnifiedImportResponse(
                routed_to="document",
                result=DocumentImportResponse(**dataclasses.asdict(document_result)),
            )

        media_result = build_import_assets(container, observe=False).execute(
            ImportRequest(
                scope_id=scope_id,
                file_name=file_name,
                content_type=file.content_type,
                content=content,
            )
        )
        refresh_memory_graph(container, scope_id)
        return UnifiedImportResponse(routed_to="media", result=to_import_response(media_result))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
