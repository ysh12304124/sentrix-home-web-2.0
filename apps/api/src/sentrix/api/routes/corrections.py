"""记忆修正、审计与派生同步的 HTTP 接线。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from functools import wraps
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from sentrix.api.deps import get_container
from sentrix.api.errors import InvalidRequestError, NotFoundError
from sentrix.application.memories.sync_corrections import SYNC_LOCK, wake_correction_sync
from sentrix.bootstrap.container import Container
from sentrix.bootstrap.import_wiring import build_correction_sync as synchronizer

router = APIRouter(tags=["memory-corrections"])


def memory_write(kind: str, key: str) -> Callable[..., Any]:
    """包裹已有用户修正入口；请求/响应契约保持，审计任务先于副作用持久化。"""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(function, eval_str=True)

        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            container: Container = bound.arguments["container"]
            payload = bound.arguments.get("payload")
            scope = str(getattr(payload, "scope_id", "") or "")
            target = str(bound.arguments.get(key, "") or getattr(payload, key, "") or "")
            if not scope:
                if kind == "face":
                    record = container.face_repository.get_cluster(target)
                    scope = record.scope_id if record else ""
                elif kind in {"entity", "family"}:
                    record = container.entity_repository.get(target)
                    scope = record.scope_id if record else ""
                else:
                    data = container.memory_corrections.read(kind, target)
                    scope = str(data["scope_id"]) if data else ""
            if not scope:
                return function(*args, **kwargs)
            # 派生同步（记忆图、因果判定、向量）交给后台执行器：它要十几秒，请求只负责
            # 写权威数据与登记持久任务。前端拿 ``sync_job_id`` 查进度。
            with container.memory_corrections.track(scope, kind, target) as job_id:
                result = function(*args, **kwargs)
            wake_correction_sync()
            model_fields = getattr(type(result), "model_fields", {})
            if {"sync_status", "sync_error"}.issubset(model_fields):
                update: dict[str, Any] = {"sync_status": "pending", "sync_error": ""}
                if "sync_job_id" in model_fields:
                    update["sync_job_id"] = job_id
                result = result.model_copy(update=update)
            return result

        wrapped.__dict__["__signature__"] = signature
        return wrapped

    return decorate


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    changes: dict[str, str | list[object] | None]
    reason: str = Field(default="", max_length=2000)


class CorrectionResponse(BaseModel):
    id: str
    scope_id: str
    revision: int
    sync_status: str
    error: str


class CorrectionRecords(BaseModel):
    items: list[dict[str, Any]]


class NameSyncStatus(BaseModel):
    id: str
    status: Literal["pending", "synced", "failed"]
    error: str


@router.get("/memories/corrections/name-sync")
def latest_name_sync(entity_id: str, container: Container = Depends(get_container)) -> NameSyncStatus | None:
    status = container.memory_corrections.latest_name_sync(entity_id)
    return NameSyncStatus.model_validate(status) if status is not None else None


@router.get("/memories/corrections/jobs/{job_id}")
def correction_job(job_id: str, container: Container = Depends(get_container)) -> NameSyncStatus:
    """一条修正同步任务的进度（关系、归属、改名、确认身份都用它查）。"""
    status = container.memory_corrections.sync_job(job_id)
    if status is None:
        raise NotFoundError("sync job not found")
    return NameSyncStatus.model_validate(status)


@router.post("/memories/corrections/name-sync/{job_id}/retry", status_code=202)
def retry_name_sync(job_id: str, container: Container = Depends(get_container)) -> NameSyncStatus:
    status = container.memory_corrections.retry_name_sync(job_id)
    if status is None:
        raise NotFoundError("name sync job not found")
    wake_correction_sync()
    return NameSyncStatus.model_validate(status)


@router.post("/memories/corrections/jobs/{job_id}/retry", status_code=202)
def retry_correction_job(job_id: str, container: Container = Depends(get_container)) -> NameSyncStatus:
    """让一条失败或在退避中的同步任务立即重新入队，由后台执行器处理。"""
    status = container.memory_corrections.retry_name_sync(job_id)
    if status is None:
        raise NotFoundError("sync job not found")
    wake_correction_sync()
    return NameSyncStatus.model_validate(status)


@router.get("/memories/corrections", response_model=CorrectionRecords)
def history(
    scope_id: str, limit: int = Query(50, ge=1, le=200), container: Container = Depends(get_container)
) -> CorrectionRecords:
    return CorrectionRecords(items=list(container.memory_corrections.records(scope_id, limit)))


@router.post("/memories/corrections/retry", response_model=dict[str, str])
def retry(scope_id: str = "", container: Container = Depends(get_container)) -> dict[str, str]:
    scopes = [scope_id] if scope_id else container.memory_corrections.pending_scopes()
    return {
        "error": "; ".join(error for scope in scopes if (error := synchronizer(container).execute(scope)))
    }


@router.get("/memories/{kind}/{target_id}", response_model=dict[str, Any])
def read_memory(kind: str, target_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    record = container.memory_corrections.read(kind, target_id)
    if record is None:
        raise NotFoundError("memory not found")
    return record


@router.get("/assets/{asset_id}/memories", response_model=CorrectionRecords)
def asset_memories(asset_id: str, container: Container = Depends(get_container)) -> CorrectionRecords:
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise NotFoundError("asset not found")
    derived = container.video_repository.list_derived(asset_id) if asset.media_type.value == "video" else []
    assets = [asset, *[child for item in derived if (child := container.asset_repository.get(item.id))]]
    observations = [o for item in assets for o in container.observation_repository.list_by_asset(item.id)]
    return CorrectionRecords(items=[asdict(o) for o in observations])


@router.patch("/memories/{kind}/{target_id}", response_model=CorrectionResponse)
def correct_memory(
    kind: str, target_id: str, payload: CorrectionRequest, container: Container = Depends(get_container)
) -> CorrectionResponse:
    changes: dict[str, object] = dict(payload.changes)
    for key, value in changes.items():
        if key in {"objects", "clothing", "tags", "entities"}:
            if not isinstance(value, list):
                raise InvalidRequestError("objects/clothing/tags/entities 必须为列表")
        elif key in {"time_start", "time_end"}:
            if value is not None:
                if not isinstance(value, str):
                    raise InvalidRequestError("时间必须为 ISO 字符串")
                try:
                    datetime.fromisoformat(value)
                except ValueError as error:
                    raise InvalidRequestError("时间格式错误") from error
        elif not isinstance(value, str):
            raise InvalidRequestError("文本字段必须为字符串")
    if kind == "entity" and "canonical_name" in changes:
        entity = container.entity_repository.get(target_id)
        if entity is not None and entity.entity_type == "person":
            raise InvalidRequestError("人物改名请使用人物页面，以保留身份与别名历史")
    with SYNC_LOCK:
        try:
            result = container.memory_corrections.correct(
                kind, target_id, payload.revision, changes, payload.reason
            )
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        scope = str(result["scope_id"])
        sync_error = synchronizer(container).execute(scope)
        latest = container.memory_corrections.read(kind, target_id)
    return CorrectionResponse(
        id=str(result["id"]),
        scope_id=scope,
        revision=int(str(latest["revision"] if latest else result["revision"])),
        sync_status="pending" if sync_error else "synced",
        error=sync_error,
    )
