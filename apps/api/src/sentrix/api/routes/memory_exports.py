"""图片记忆导出端点：只读快照、冲突清单与固定文件下载。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from sentrix.api.deps import get_container
from sentrix.api.schemas.memory_exports import (
    ExportFilePayload,
    MemoryExportCreateRequest,
    MemoryExportListResponse,
    MemoryExportPayload,
)
from sentrix.application.media.export_memories import ExportImageMemories, ExportRequest
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import MemoryExportSnapshot

router = APIRouter(tags=["memory-exports"])


def _export(container: Container) -> ExportImageMemories:
    return ExportImageMemories(
        assets=container.asset_repository,
        observations=container.observation_repository,
        places=container.asset_place_repository,
        faces=container.face_repository,
        evidence=container.memory_evidence,
        builds=container.memory_builds,
        snapshots=container.memory_exports,
        writer=container.memory_export_writer,
    )


def _payload(snapshot: MemoryExportSnapshot) -> MemoryExportPayload:
    return MemoryExportPayload(
        id=snapshot.id,
        scope_id=snapshot.scope_id,
        status=snapshot.status,
        schema_version=snapshot.schema_version,
        compat_mode=snapshot.compat_mode,
        selection=dict(snapshot.selection),
        manifest=dict(snapshot.manifest),
        files=[
            ExportFilePayload(name=item.name, sha256=item.sha256, size_bytes=item.size_bytes)
            for item in snapshot.files
        ],
        conflicts=list(snapshot.conflicts),
        counts=dict(snapshot.counts),
        created_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
        updated_at=snapshot.updated_at.isoformat() if snapshot.updated_at else None,
    )


@router.post("/memory-exports", response_model=MemoryExportPayload, status_code=201)
def create_memory_export(
    payload: MemoryExportCreateRequest,
    container: Container = Depends(get_container),
) -> MemoryExportPayload:
    snapshot = _export(container).execute(
        ExportRequest(
            scope_id=payload.scope_id,
            asset_ids=tuple(payload.asset_ids),
            compat_mode=payload.compat_mode,
            id_map=payload.id_map or None,
        )
    )
    return _payload(snapshot)


@router.get("/memory-exports", response_model=MemoryExportListResponse)
def list_memory_exports(
    scope_id: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    container: Container = Depends(get_container),
) -> MemoryExportListResponse:
    return MemoryExportListResponse(
        scope_id=scope_id,
        items=[_payload(item) for item in container.memory_exports.list_for_scope(scope_id, limit=limit)],
    )


@router.get("/memory-exports/{snapshot_id}", response_model=MemoryExportPayload)
def get_memory_export(snapshot_id: str, container: Container = Depends(get_container)) -> MemoryExportPayload:
    snapshot = container.memory_exports.get(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="export snapshot not found")
    return _payload(snapshot)


@router.get("/memory-exports/{snapshot_id}/files/{file_name}")
def download_memory_export_file(
    snapshot_id: str,
    file_name: str,
    container: Container = Depends(get_container),
) -> FileResponse:
    snapshot = container.memory_exports.get(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="export snapshot not found")
    allowed = {item.name for item in snapshot.files}
    if file_name not in allowed or snapshot.status != "ready":
        raise HTTPException(status_code=409, detail="export file is not available")
    path = container.memory_export_writer.path_for(snapshot_id, file_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="export file missing")
    media_type = "application/json" if file_name.endswith(".json") else "application/x-ndjson"
    return FileResponse(path, media_type=media_type, filename=file_name)
