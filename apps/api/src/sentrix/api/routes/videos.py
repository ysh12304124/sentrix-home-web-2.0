"""视频端点（S8a T504）：源片详情 / 场景列表 / 场景详情 / 重跑。

**只做 HTTP 映射**：读取在仓储（``EventRepository.list_video_scenes`` 与
``VideoRepository.list_derived``），业务在 ``application/media/import_assets.py``。

响应形状照 1.0 ``backend/app.py:1448-1481`` 四条路由，**前缀不同**：2.0 的 v1 路由挂在
``/api/v1`` 下（``main.py`` 的 ``APIRouter(prefix="/api/v1")``），1.0 是 ``/api``。

**S8b 起 ``scenes`` 有内容**：建场景是 T512/T513 的事（``ExtractVideoScenes``），
写侧与读侧现在都在。四条路由的形状自 S8a 起没动过——读侧先于写侧落地，正是
「场景成型后能不能读回来」那条验收要先有读法（``s8-deviations.md`` §0）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from sentrix.api.deps import get_container
from sentrix.api.routes.media import _video_config_hash, resume_video_job
from sentrix.api.schemas.videos import (
    KeyframeAssetPayload,
    SourceVideoPayload,
    SpeechSegmentPayload,
    VideoDetailResponse,
    VideoJobResponse,
    VideoMetadataPayload,
    VideoPayload,
    VideoReprocessResponse,
    VideoSceneDetailResponse,
    VideoScenePayload,
    VideoScenesResponse,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import Asset, AssetStatus, DerivedAsset, MediaType, VideoMetadata
from sentrix.domain.media.ports import VideoProbe
from sentrix.domain.media.scenes import VIDEO_SCENE_SOURCE_TYPE
from sentrix.domain.memories.events import EventRecord

router = APIRouter(tags=["videos"])

# 重跑的响应里那个 ``status``。S8a 回的是 1.0 的阶段名 ``video-queued``——那时它真的
# 只是排队，没有抽取链会来接走；S8b 起这条链是**同步跑完**的（见 ``reprocess_video``），
# 所以如实回它跑完的状态。
REPROCESS_STATUS = AssetStatus.PROCESSED.value


def _metadata_payload(metadata: VideoMetadata | None) -> VideoMetadataPayload | None:
    if metadata is None:
        return None
    return VideoMetadataPayload(
        captured_at=metadata.captured_at,
        latitude=metadata.latitude,
        longitude=metadata.longitude,
        captured_location=metadata.captured_location,
        duration_sec=metadata.duration_sec,
        fps=metadata.fps,
        width=metadata.width,
        height=metadata.height,
        codec=metadata.codec,
        rotation=metadata.rotation,
        device=metadata.device,
        creation_source=metadata.creation_source,
        frame_count=metadata.frame_count,
    )


def _video_payload(asset: Asset, probe: VideoProbe | None) -> VideoPayload:
    location = asset.captured_location
    return VideoPayload(
        id=asset.id,
        scope_id=asset.scope_id,
        file_name=asset.file_name,
        media_type=asset.media_type.value,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        status=asset.status.value,
        captured_at=asset.captured_at.isoformat() if asset.captured_at else None,
        captured_location=location.as_text() if location else None,
        gps=({"latitude": location.latitude, "longitude": location.longitude} if location else None),
        metadata=_metadata_payload(probe.metadata if probe else None),
        browser_preview_path=probe.browser_preview_path if probe else "",
        browser_preview_error=probe.browser_preview_error if probe else "",
        probe_error=probe.probe_error if probe else "",
        processing_seconds=probe.processing_seconds if probe else None,
        processing_timings=probe.processing_timings if probe else {},
    )


def _scene_payload(
    event: EventRecord, keyframe_timestamps_sec: list[float] | None = None
) -> VideoScenePayload:
    raw_segments = event.source_metadata.get("speech_segments", [])
    speech_segments = (
        [
            {
                "start_sec": segment["start_sec"],
                "end_sec": segment["end_sec"],
                "text": segment["text"],
            }
            for segment in raw_segments
            if isinstance(segment, dict)
            and isinstance(segment.get("start_sec"), (int, float))
            and isinstance(segment.get("end_sec"), (int, float))
            and isinstance(segment.get("text"), str)
        ]
        if isinstance(raw_segments, list)
        else []
    )
    return VideoScenePayload(
        id=event.id,
        scope_id=event.scope_id,
        title=event.title,
        event_type=event.event_type,
        time_start=event.time_start.isoformat() if event.time_start else None,
        time_end=event.time_end.isoformat() if event.time_end else None,
        place=event.place,
        activity=event.activity,
        summary=event.summary,
        confidence=event.confidence,
        status=event.status,
        source_type=event.source_type,
        source_asset_id=event.source_asset_id,
        source_scene_index=event.source_scene_index,
        source_start_sec=event.source_start_sec,
        source_end_sec=event.source_end_sec,
        speech_segments=[SpeechSegmentPayload(**segment) for segment in speech_segments],
        keyframe_timestamps_sec=sorted(keyframe_timestamps_sec or []),
    )


def _video_scenes_payload(asset_id: str, container: Container) -> list[VideoScenePayload]:
    events = container.event_repository.list_video_scenes(asset_id)
    frame_times: dict[int, list[float]] = {}
    for derived in container.video_repository.list_derived(asset_id):
        if (
            derived.derived_kind == "video_keyframe"
            and derived.source_scene_index is not None
            and derived.source_timestamp_sec is not None
        ):
            frame_times.setdefault(derived.source_scene_index, []).append(derived.source_timestamp_sec)
    return [
        _scene_payload(
            event,
            frame_times.get(event.source_scene_index, []) if event.source_scene_index is not None else [],
        )
        for event in events
    ]


def _keyframe_payload(derived: DerivedAsset) -> KeyframeAssetPayload:
    return KeyframeAssetPayload(
        id=derived.id,
        file_name=derived.file_name,
        media_type=derived.media_type,
        status=derived.status,
        captured_at=derived.captured_at,
        captured_location=derived.captured_location,
        parent_asset_id=derived.parent_asset_id,
        derived_kind=derived.derived_kind,
        source_timestamp_sec=derived.source_timestamp_sec,
        source_frame_index=derived.source_frame_index,
        source_scene_index=derived.source_scene_index,
    )


def _require_video(asset_id: str, container: Container) -> Asset:
    """取一个**视频**资产，否则 404。1.0 三条读路由的同一个判据（``app.py:1450`` 等）。"""
    asset = container.asset_repository.get(asset_id)
    if asset is None or asset.media_type is not MediaType.VIDEO:
        raise HTTPException(status_code=404, detail="video asset not found")
    return asset


@router.get("/videos/{asset_id}")
def get_video(asset_id: str, container: Container = Depends(get_container)) -> VideoDetailResponse:
    asset = _require_video(asset_id, container)
    return VideoDetailResponse(
        video=_video_payload(asset, container.video_repository.probe(asset_id)),
        scenes=_video_scenes_payload(asset_id, container),
    )


@router.get("/videos/{asset_id}/scenes")
def get_video_scenes(asset_id: str, container: Container = Depends(get_container)) -> VideoScenesResponse:
    _require_video(asset_id, container)
    return VideoScenesResponse(
        video_asset_id=asset_id,
        scenes=_video_scenes_payload(asset_id, container),
    )


@router.get("/video-scenes/{scene_id}")
def get_video_scene(scene_id: str, container: Container = Depends(get_container)) -> VideoSceneDetailResponse:
    event = container.event_repository.get(scene_id)
    if event is None or event.source_type != VIDEO_SCENE_SOURCE_TYPE:
        raise HTTPException(status_code=404, detail="video scene not found")
    # 派生物按 ``(source_asset_id, source_scene_index)`` 找——迁移 0010 为这对键
    # 各建了一个索引，正是「跳回原片」这条链的落点。
    keyframes = container.video_repository.list_derived(
        event.source_asset_id, scene_index=event.source_scene_index
    )
    source_video = container.asset_repository.get(event.source_asset_id)
    source_probe = container.video_repository.probe(event.source_asset_id) if source_video else None
    source_meta = source_probe.metadata if source_probe else None
    return VideoSceneDetailResponse(
        event=_scene_payload(
            event,
            [item.source_timestamp_sec for item in keyframes if item.source_timestamp_sec is not None],
        ),
        keyframe_assets=[_keyframe_payload(item) for item in keyframes],
        source_video=(
            SourceVideoPayload(
                id=source_video.id,
                file_name=source_video.file_name,
                media_type=source_video.media_type.value,
                mime_type=source_video.mime_type,
                status=source_video.status.value,
                captured_at=(source_video.captured_at.isoformat() if source_video.captured_at else None),
                captured_location=(
                    source_video.captured_location.as_text() if source_video.captured_location else None
                ),
                size_bytes=source_video.size_bytes,
                duration_sec=source_meta.duration_sec if source_meta else 0.0,
            )
            if source_video
            else None
        ),
    )


@router.post("/videos/{asset_id}/reprocess", status_code=202)
def reprocess_video(asset_id: str, container: Container = Depends(get_container)) -> VideoReprocessResponse:
    """兼容旧重处理端点：复用源视频和成功阶段，异步恢复失败/未完成阶段。"""
    asset = _require_video(asset_id, container)
    job = container.video_repository.get_job_for_asset(asset_id)
    if job is None:
        job = container.video_repository.create_job(
            asset_id,
            asset.content_sha256 or "",
            f"vjob_{uuid.uuid4().hex[:16]}",
            _video_config_hash(container),
        )
        probe = container.video_repository.probe(asset_id)
        for stage_name, artifact in (("UPLOAD", asset.path),):
            container.video_repository.update_stage(
                str(job["job_id"]),
                stage_name,
                status="SUCCEEDED",
                progress=1.0,
                input_hash=asset.content_sha256 or "",
                output_artifact=artifact,
            )
        container.video_repository.update_stage(
            str(job["job_id"]),
            "VIDEO_PROBE",
            status="SUCCEEDED" if probe is not None and probe.metadata is not None else "FAILED",
            progress=1.0 if probe is not None and probe.metadata is not None else 0.0,
            input_hash=asset.content_sha256 or "",
            output_artifact="assets.metadata_json.video_metadata"
            if probe is not None and probe.metadata is not None
            else "",
            error_code=None if probe is not None and probe.metadata is not None else "VIDEO-DECODE-001",
            error_message=(
                None
                if probe is not None and probe.metadata is not None
                else "ffprobe did not return video metadata"
            ),
        )
    job_id = str(job["job_id"])
    if job.get("status") not in {"PROCESSING", "COMPLETED"}:
        job = resume_video_job(container, job_id)
    return VideoReprocessResponse(
        accepted=True, asset_id=asset_id, status=str(job.get("status", "QUEUED")), job_id=job_id
    )


def _job_response(job_id: str, container: Container) -> VideoJobResponse:
    job = container.video_repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="video job not found")
    asset = container.asset_repository.get(str(job["asset_id"]))
    stages = [
        {
            **stage,
            "error_message": (
                "视频处理暂时中断，已完成步骤不会丢失。" if stage.get("error_message") else None
            ),
        }
        for stage in container.video_repository.list_stages(job_id)
    ]
    return VideoJobResponse(
        job_id=str(job["job_id"]),
        asset_id=str(job["asset_id"]),
        file_name=asset.file_name if asset else str(job["asset_id"]),
        status=str(job["status"]),
        current_stage=str(job["current_stage"]),
        overall_progress=float(str(job["overall_progress"])),
        retry_count=int(str(job["retry_count"])),
        error_code=str(job["error_code"]) if job.get("error_code") else None,
        error_message=str(job["error_message"]) if job.get("error_message") else None,
        stages=stages,
    )


@router.get("/video-jobs/{job_id}")
def get_video_job(job_id: str, container: Container = Depends(get_container)) -> VideoJobResponse:
    return _job_response(job_id, container)


@router.get("/video-jobs")
def list_video_jobs(
    scope_id: str = "", container: Container = Depends(get_container)
) -> list[VideoJobResponse]:
    return [
        _job_response(str(job["job_id"]), container)
        for job in container.video_repository.list_jobs(scope_id or None)
    ]


@router.post("/video-jobs/{job_id}/resume", status_code=202)
def resume_video_processing(job_id: str, container: Container = Depends(get_container)) -> VideoJobResponse:
    try:
        resume_video_job(container, job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="video job not found") from error
    return _job_response(job_id, container)


@router.post("/video-jobs/{job_id}/retry", status_code=202)
def retry_video_processing(job_id: str, container: Container = Depends(get_container)) -> VideoJobResponse:
    return resume_video_processing(job_id, container)
