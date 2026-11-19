"""The single text-memory endpoint; source labels are provenance, not pipeline kinds."""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sentrix.api.deps import get_container
from sentrix.api.errors import InvalidRequestError, NotFoundError
from sentrix.application.memories.sync_corrections import SYNC_LOCK
from sentrix.bootstrap.container import Container
from sentrix.bootstrap.import_wiring import build_memory_graph, build_text_import

router = APIRouter(tags=["text-memory"])


class TextImportRequest(BaseModel):
    scope_id: str = Field(min_length=1, max_length=200)
    items: list[dict[str, object]] = Field(min_length=1, max_length=500)
    rebuild_graph: bool = True


def import_items(payload: TextImportRequest, container: Container) -> dict:
    if container.spaces.get(payload.scope_id) is None:
        raise NotFoundError("请先创建明确的记忆相册")
    with SYNC_LOCK:
        try:
            result = build_text_import(container).execute(payload.scope_id, payload.items)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        result["graph_status"] = "deferred"
        if payload.rebuild_graph:
            try:
                stats = build_memory_graph(container).execute(payload.scope_id)
                result["graph_status"] = "completed" if stats.causal_status != "degraded" else "partial"
            except Exception as exc:
                result["graph_status"] = "partial"
                result["graph_error"] = str(exc)
            if result["graph_status"] == "partial":
                result["status"] = "partial"
    return result


@router.post("/text/import")
def import_text(payload: TextImportRequest, container: Container = Depends(get_container)) -> dict:
    return import_items(payload, container)


@router.get("/text/{asset_id}")
def text_detail(asset_id: str, container: Container = Depends(get_container)) -> dict:
    asset = container.asset_repository.get(asset_id)
    if asset is None or asset.media_type.value != "text":
        raise NotFoundError("文本不存在")
    observations = container.observation_repository.list_by_asset(asset_id)
    if not observations:
        raise NotFoundError("文本记忆未构建")
    observation = max(observations, key=lambda o: (o.revision, o.updated_at))
    return {
        "asset_id": asset_id,
        "scope_id": asset.scope_id,
        "memory_type": "text",
        "observation": asdict(observation),
        "index_state": container.text_memory_repository.find(asset.scope_id, observation.external_id),
    }
