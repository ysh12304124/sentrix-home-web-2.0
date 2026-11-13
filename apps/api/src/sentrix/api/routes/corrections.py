"""记忆修正、审计与派生同步的 HTTP 接线。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from functools import wraps
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from sentrix.api.deps import get_container
from sentrix.api.errors import InvalidRequestError, NotFoundError
from sentrix.api.routes.media import build_memory_graph
from sentrix.application.memories.sync_corrections import SYNC_LOCK, ReindexCorrectedMemories, SyncCorrections
from sentrix.bootstrap.container import Container

router = APIRouter(tags=["memory-corrections"])


def synchronizer(container: Container) -> SyncCorrections:
    reindex = ReindexCorrectedMemories(
        container.observation_repository,
        container.event_repository,
        container.lexical_index,
        container.text_embedder,
        container.text_vector_repository,
        container.memory_corrections,
    ).reindex

    def graph(scope: str) -> None:
        stats = build_memory_graph(container).execute(scope)
        if stats.causal_status == "degraded":
            raise RuntimeError("因果边同步降级，请重试")

    return SyncCorrections(container.memory_corrections, reindex, graph)


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
                else:
                    data = container.memory_corrections.read(kind, target)
                    scope = str(data["scope_id"]) if data else ""
            if not scope:
                return function(*args, **kwargs)
            with SYNC_LOCK:
                with container.memory_corrections.track(scope, kind, target):
                    result = function(*args, **kwargs)
                synchronizer(container).execute(scope)
            return result

        wrapped.__dict__["__signature__"] = signature
        return wrapped

    return decorate


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    changes: dict[str, str | list[str] | None]
    reason: str = Field(default="", max_length=2000)


class CorrectionResponse(BaseModel):
    id: str
    scope_id: str
    revision: int
    sync_status: str
    error: str


class CorrectionRecords(BaseModel):
    items: list[dict[str, Any]]


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
        if key in {"objects", "clothing"}:
            if not isinstance(value, list):
                raise InvalidRequestError("objects/clothing 必须为列表")
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
