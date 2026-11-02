"""回忆首页序列。只做 HTTP 映射，选片与排序在 application 层。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.schemas.memories import RotationCard, RotationResponse, RotationViewedRequest
from sentrix.application.memories.read_rotation import ReadRotation
from sentrix.bootstrap.container import Container

router = APIRouter(tags=["memories"])


def _read_rotation(container: Container) -> ReadRotation:
    return ReadRotation(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        videos=container.video_repository,
        rotations=container.memory_rotation,
        jobs=container.import_jobs,
    )


@router.get("/memories/rotation")
def rotation(
    scope_id: str = Query(default=""),
    cursor: int | None = Query(default=None, ge=0),
    container: Container = Depends(get_container),
) -> RotationResponse:
    try:
        page = _read_rotation(container).execute(scope_id or None, cursor=cursor)
    except Exception as error:  # noqa: BLE001 - 数据暂不可读不等于“没有回忆”
        raise HTTPException(status_code=503, detail="回忆数据暂不可读取，正在恢复") from error

    media = [
        RotationCard(
            event_id=item.event_id,
            asset_id=item.asset_id,
            media_kind=item.media_kind,
            title=item.title,
            summary=item.summary,
            time_label=item.time_label,
            place_label=item.place_label,
            related_asset_count=item.related_asset_count,
            poster_url=f"/api/v1/assets/{item.asset_id}/poster"
            if item.media_kind == "video"
            else f"/api/v1/assets/{item.asset_id}/preview",
            play_url=f"/api/v1/assets/{item.asset_id}/file" if item.media_kind == "video" else None,
        )
        for item in page.media
    ]
    return RotationResponse(
        sequence_id=page.sequence_id,
        day=page.day,
        cursor=page.cursor,
        has_more=page.has_more,
        empty_reason=page.empty_reason,
        media=media,
    )


@router.post("/memories/rotation/viewed", status_code=204)
def viewed(payload: RotationViewedRequest, container: Container = Depends(get_container)) -> None:
    _read_rotation(container).mark_viewed(payload.sequence_id, payload.event_id)
