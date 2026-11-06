"""媒体导入与交付。只做 HTTP 映射，业务在 application 层。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from sentrix.api.deps import get_container
from sentrix.api.schemas.media import AssetListResponse, AssetSummary, ImportResponse
from sentrix.application.identity.enroll_face import EnrollFace, EnrollFaces
from sentrix.application.media.extract_video_scenes import ExtractVideoScenes, VideoSceneExtraction
from sentrix.application.media.import_assets import ImportAssets, ImportRequest, StagedImport
from sentrix.application.media.index_place import IndexAssetPlace
from sentrix.application.media.maintain_entities import MaintainObservationEntities
from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.application.memories.assign_events import AssignEvents, EventVisualSimilarity
from sentrix.application.memories.build_graph import BuildMemoryGraph
from sentrix.application.memories.maintain_facts import MaintainFacts
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.application.retrieval.index_visual import IndexAssetVisual
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import TranscriptionSegment
from sentrix.domain.media.scenes import SceneWindow
from sentrix.domain.memories.graph import GraphBuildStats

router = APIRouter(tags=["media"])
log = logging.getLogger("sentrix.api.media")
_video_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentrix-video")
_active_video_jobs: set[str] = set()
_active_video_jobs_lock = threading.Lock()
MAX_VIDEO_ATTEMPTS = 3
VIDEO_RETRY_DELAY_SECONDS = 2.0

PREVIEW_MAX_DIMENSION = 1600
PREVIEW_QUALITY = 85


def to_summary(asset: Asset) -> AssetSummary:
    return AssetSummary(
        id=asset.id,
        file_name=asset.file_name,
        media_type=asset.media_type.value,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        status=asset.status.value,
        captured_at=asset.captured_at.isoformat() if asset.captured_at else None,
        gps=(
            {"latitude": asset.captured_location.latitude, "longitude": asset.captured_location.longitude}
            if asset.captured_location
            else None
        ),
        # 关键帧血缘原样带出：普通照片是 None，派生帧是迁移 0010 那三列。1.0 的
        # ``SELECT *`` 顺带回了它们（``db.py:1505``），2.0 的窄投影不能少。
        derived_kind=asset.derived_kind,
        parent_asset_id=asset.parent_asset_id,
        source_timestamp_sec=asset.source_timestamp_sec,
    )


@router.post("/media/import", status_code=201)
async def import_media(
    file: UploadFile = File(...),
    scope_id: str = Form("home-default"),
    container: Container = Depends(get_container),
) -> ImportResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    request = ImportRequest(
        scope_id=scope_id,
        file_name=file.filename or "unnamed",
        content_type=file.content_type,
        content=content,
        defer_processing=False,
    )
    media_type = MediaType.from_upload(request.content_type, request.file_name)
    if media_type is MediaType.VIDEO:
        staged = build_import_assets(container, observe=False).stage(replace(request, defer_processing=True))
        asset = (
            staged.asset
            if isinstance(staged, StagedImport)
            else container.asset_repository.get(staged.asset_id)
        )
        if asset is None:
            raise HTTPException(status_code=500, detail="video asset could not be loaded")
        video_hash = asset.content_sha256 or ""
        job = container.video_repository.create_job(
            asset.id,
            video_hash,
            f"vjob_{uuid.uuid4().hex[:16]}",
            _video_config_hash(container),
        )
        job_id = str(job["job_id"])
        upload_stage = container.video_repository.stage(job_id, "UPLOAD")
        if upload_stage and upload_stage.get("status") != "SUCCEEDED":
            container.video_repository.update_stage(
                job_id,
                "UPLOAD",
                status="SUCCEEDED",
                progress=1.0,
                input_hash=video_hash,
                output_artifact=asset.path,
                finished_at=datetime.now(UTC).isoformat(),
            )
        probe = container.video_repository.probe(asset.id)
        probe_stage = container.video_repository.stage(job_id, "VIDEO_PROBE")
        if probe_stage and probe_stage.get("status") != "SUCCEEDED":
            probe_ready = probe is not None and probe.metadata is not None
            container.video_repository.update_stage(
                job_id,
                "VIDEO_PROBE",
                status="SUCCEEDED" if probe_ready else "FAILED",
                progress=1.0 if probe_ready else 0.0,
                input_hash=video_hash,
                output_artifact="assets.metadata_json.video_metadata" if probe_ready else "",
                error_code=None if probe_ready else "VIDEO-DECODE-001",
                error_message=None if probe_ready else "ffprobe did not return video metadata",
                finished_at=datetime.now(UTC).isoformat() if probe_ready else None,
            )
        schedule_video_job(container, job_id)
        return ImportResponse(
            asset_id=asset.id,
            observation_id=None,
            media_type="video",
            status="queued",
            deduplicated=not isinstance(staged, StagedImport),
            indexed=False,
            video_probed=probe is not None and probe.metadata is not None,
            job_id=job_id,
            job_status=str(job["status"]),
        )

    use_case = build_import_assets(container, observe=False)
    result = use_case.execute(request)
    refresh_memory_graph(container, scope_id)
    return ImportResponse(
        asset_id=result.asset_id,
        observation_id=result.observation_id,
        media_type=result.media_type.value,
        status=result.status.value,
        deduplicated=result.deduplicated,
        indexed=result.indexed,
        placed=result.placed,
        event_id=result.event_id,
        video_probed=result.video_probed,
        video_scenes=result.video_scenes,
        audio_probed=result.audio_probed,
    )


def schedule_video_job(container: Container, job_id: str) -> None:
    """Submit once per process; the repository claim is the cross-request/worker guard."""
    with _active_video_jobs_lock:
        if job_id in _active_video_jobs:
            return
        _active_video_jobs.add(job_id)
    _video_workers.submit(_run_video_job, container, job_id)


def resume_video_job(container: Container, job_id: str) -> dict[str, object]:
    job = container.video_repository.get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.get("status") in {"PROCESSING", "COMPLETED", "CANCELLED"}:
        return job
    for stage in container.video_repository.list_stages(job_id):
        if stage.get("status") in {"FAILED", "RUNNING", "RETRY_WAIT"}:
            container.video_repository.update_stage(
                job_id, str(stage["stage"]), status="RETRY_WAIT", error_code=None, error_message=None
            )
    container.video_repository.update_job(
        job_id, status="RETRY_WAIT", error_code=None, error_message=None, worker_id=None
    )
    schedule_video_job(container, job_id)
    refreshed = container.video_repository.get_job(job_id)
    return refreshed or job


def recover_video_jobs(container: Container) -> None:
    """Reclaim expired leases and jobs owned by a process that no longer exists."""
    stale_before = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    recovered = container.video_repository.recover_stale_jobs(stale_before)
    for job in container.video_repository.list_jobs():
        if job.get("status") != "PROCESSING":
            continue
        worker_id = str(job.get("worker_id") or "")
        try:
            owner_pid = int(worker_id.split(":", maxsplit=1)[0])
        except ValueError:
            continue
        if _process_is_alive(owner_pid):
            continue
        job_id = str(job["job_id"])
        for stage in container.video_repository.list_stages(job_id):
            if stage.get("status") == "RUNNING":
                container.video_repository.update_stage(job_id, str(stage["stage"]), status="RETRY_WAIT")
        container.video_repository.update_job(
            job_id,
            status="RETRY_WAIT",
            worker_id=None,
            error_code="VIDEO-WORKER-001",
            error_message="服务重启后从已保存的处理进度恢复",
        )
        recovered += 1
    if recovered:
        log.warning("video jobs recovered after process restart count=%d", recovered)
    for job in container.video_repository.list_jobs():
        if job.get("status") in {"QUEUED", "RETRY_WAIT"}:
            schedule_video_job(container, str(job["job_id"]))


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _video_config_hash(container: Container) -> str:
    settings = container.settings
    config = {
        "pipeline_version": "video-v1",
        "embedding_scheme": settings.embedding_scheme,
        "cross_modal_attention_enabled": settings.cross_modal_attention_enabled,
        "qwen_asr_model": settings.qwen_asr_model,
        "vlm_base_url": settings.vlm_base_url,
        "legacy_clip_url": settings.legacy_clip_url,
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()


def _run_video_job(container: Container, job_id: str) -> None:
    worker_id = f"{os.getpid()}:{threading.get_ident()}"
    try:
        if not container.video_repository.claim_job(job_id, worker_id):
            return
        job_started = time.perf_counter()
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(10):
                try:
                    container.video_repository.update_job(job_id, heartbeat_at=datetime.now(UTC).isoformat())
                except Exception:  # noqa: BLE001 — worker reports its main error path
                    log.exception("video job heartbeat failed job_id=%s", job_id)

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            job = container.video_repository.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            asset_id = str(job["asset_id"])
            asset = container.asset_repository.get(asset_id)
            if asset is None or not Path(asset.path).is_file():
                raise FileNotFoundError(f"source video artifact missing: {asset_id}")
            container.asset_repository.update_status(asset_id, AssetStatus.PROCESSING)

            def mark(
                stage: str, *, artifact: str, progress: float = 1.0, input_hash: str | None = None
            ) -> None:
                stage_fields: dict[str, object] = {
                    "status": "SUCCEEDED",
                    "progress": progress,
                    "output_artifact": artifact,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "error_code": None,
                    "error_message": None,
                }
                if input_hash is not None:
                    stage_fields["input_hash"] = input_hash
                container.video_repository.update_stage(
                    job_id,
                    stage,
                    **stage_fields,
                )
                container.video_repository.update_job(
                    job_id,
                    current_stage=stage,
                    overall_progress=_job_progress(container, job_id),
                    heartbeat_at=datetime.now(UTC).isoformat(),
                )

            upload_stage = container.video_repository.stage(job_id, "UPLOAD") or {}
            if upload_stage.get("status") != "SUCCEEDED":
                mark("UPLOAD", artifact=asset.path)
            probe_stage = container.video_repository.stage(job_id, "VIDEO_PROBE") or {}
            if probe_stage.get("status") != "SUCCEEDED":
                container.video_repository.update_job(job_id, current_stage="VIDEO_PROBE")
                probe = container.video_repository.probe(asset_id)
                if probe is None or probe.metadata is None:
                    raise RuntimeError("video probe artifact missing")
                mark("VIDEO_PROBE", artifact="assets.metadata_json.video_metadata")

            source_stage = container.video_repository.stage(job_id, "SOURCE_MEMORY") or {}
            source_memory_exists = bool(container.observation_repository.list_by_asset(asset_id))
            if source_stage.get("status") != "SUCCEEDED" or not source_memory_exists:
                container.video_repository.update_job(job_id, current_stage="SOURCE_MEMORY")
                container.video_repository.update_stage(
                    job_id,
                    "SOURCE_MEMORY",
                    status="RUNNING",
                    started_at=datetime.now(UTC).isoformat(),
                    attempt=int(str(source_stage.get("attempt", 0))) + 1,
                )
                if not container.observation_repository.list_by_asset(asset_id):
                    importer = build_import_assets(container, observe=False)
                    importer.resume(asset)
                    importer.commit_image(asset, importer.prepare_image(asset))
                container.asset_repository.update_status(asset_id, AssetStatus.PROCESSED)
                mark("SOURCE_MEMORY", artifact=f"sqlite:observations(asset_id={asset_id})")

            degraded = False
            speech_path = container.settings.data_dir / "video-jobs" / job_id / "speech.json"
            speech_stage = container.video_repository.stage(job_id, "SPEECH_TRANSCRIPTION") or {}
            if speech_stage.get("status") not in {"SUCCEEDED", "SKIPPED"}:
                container.video_repository.update_job(job_id, current_stage="SPEECH_TRANSCRIPTION")
                container.video_repository.update_stage(
                    job_id,
                    "SPEECH_TRANSCRIPTION",
                    status="RUNNING",
                    started_at=datetime.now(UTC).isoformat(),
                    attempt=int(str(speech_stage.get("attempt", 0))) + 1,
                )
                speech_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    segments = tuple(
                        container.audio_transcriber.transcribe_video(Path(asset.path))
                        if container.audio_transcriber is not None
                        else ()
                    )
                    payload = [
                        {
                            "start_sec": item.start_sec,
                            "end_sec": item.end_sec,
                            "text": item.text,
                            "speaker": item.speaker,
                        }
                        for item in segments
                    ]
                    speech_tmp = speech_path.with_suffix(".json.tmp")
                    with speech_tmp.open("w", encoding="utf-8") as output:
                        json.dump(payload, output, ensure_ascii=False)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(speech_tmp, speech_path)
                    speech_digest = hashlib.sha256(speech_path.read_bytes()).hexdigest()
                    mark("SPEECH_TRANSCRIPTION", artifact=str(speech_path), input_hash=speech_digest)
                except Exception as error:  # noqa: BLE001 — speech is an optional evidence channel
                    log.warning("video ASR degraded job_id=%s video_id=%s: %s", job_id, asset_id, error)
                    speech_tmp = speech_path.with_suffix(".json.tmp")
                    speech_tmp.write_text("[]", encoding="utf-8")
                    os.replace(speech_tmp, speech_path)
                    speech_digest = hashlib.sha256(speech_path.read_bytes()).hexdigest()
                    container.video_repository.update_stage(
                        job_id,
                        "SPEECH_TRANSCRIPTION",
                        status="SKIPPED",
                        progress=1.0,
                        output_artifact=str(speech_path),
                        input_hash=speech_digest,
                        error_code="VIDEO-ASR-001",
                        error_message=f"{type(error).__name__}: {error}",
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    segments = ()
                    degraded = True
            else:
                try:
                    speech_payload = json.loads(speech_path.read_text(encoding="utf-8"))
                    speech_digest = hashlib.sha256(speech_path.read_bytes()).hexdigest()
                    if speech_stage.get("input_hash") != speech_digest:
                        raise ValueError("speech artifact hash mismatch")
                    if not isinstance(speech_payload, list):
                        raise ValueError("speech artifact must be a list")
                    segments = tuple(
                        TranscriptionSegment(
                            start_sec=float(item["start_sec"]),
                            end_sec=float(item["end_sec"]),
                            text=str(item["text"]),
                            speaker=str(item.get("speaker", "")),
                        )
                        for item in speech_payload
                        if isinstance(item, dict)
                    )
                except Exception as error:  # noqa: BLE001 — invalid cache is rebuilt as the smallest stage
                    log.warning("speech checkpoint invalid; rerunning ASR job_id=%s: %s", job_id, error)
                    container.video_repository.update_stage(
                        job_id,
                        "SPEECH_TRANSCRIPTION",
                        status="RETRY_WAIT",
                        output_artifact="",
                        error_code="VIDEO-ASR-ARTIFACT-001",
                        error_message=str(error),
                    )
                    raise RuntimeError("speech checkpoint is missing or corrupt") from error
            if speech_stage.get("status") == "SKIPPED":
                degraded = True

            for stage_name in ("FRAME_SAMPLE_VSD", "EVENT_BUILD", "KEYFRAME_MEMORY"):
                current = container.video_repository.stage(job_id, stage_name) or {}
                if current.get("status") != "SUCCEEDED":
                    container.video_repository.update_stage(
                        job_id,
                        stage_name,
                        status="RUNNING",
                        started_at=datetime.now(UTC).isoformat(),
                        attempt=int(str(current.get("attempt", 0))) + 1,
                    )
            container.video_repository.update_job(job_id, current_stage="FRAME_SAMPLE_VSD")
            derived_frames = container.video_repository.list_derived(asset_id)
            frame_artifacts_valid = bool(derived_frames)
            for frame in derived_frames:
                frame_asset = container.asset_repository.get(frame.id)
                if frame_asset is None:
                    frame_artifacts_valid = False
                    break
                frame_path = Path(frame_asset.path)
                if not frame_path.is_file():
                    frame_artifacts_valid = False
                    break
                if frame_asset.content_sha256:
                    digest = hashlib.sha256(frame_path.read_bytes()).hexdigest()
                    if digest != frame_asset.content_sha256:
                        frame_artifacts_valid = False
                        break
            scene_artifacts_valid = (
                frame_artifacts_valid
                and len(container.event_repository.list_video_scenes(asset_id)) == len(derived_frames)
                and all(container.observation_repository.list_by_asset(frame.id) for frame in derived_frames)
            )
            stage_artifacts_complete = scene_artifacts_valid and all(
                (container.video_repository.stage(job_id, stage_name) or {}).get("status") == "SUCCEEDED"
                for stage_name in ("FRAME_SAMPLE_VSD", "EVENT_BUILD", "KEYFRAME_MEMORY")
            )
            if not stage_artifacts_complete:
                for stage_name in ("FRAME_SAMPLE_VSD", "EVENT_BUILD", "KEYFRAME_MEMORY"):
                    current = container.video_repository.stage(job_id, stage_name) or {}
                    if current.get("status") == "SUCCEEDED":
                        container.video_repository.update_stage(
                            job_id,
                            stage_name,
                            status="RUNNING",
                            started_at=datetime.now(UTC).isoformat(),
                            attempt=int(str(current.get("attempt", 0))) + 1,
                        )

            def checkpoint(stage: str, completed: int, total: int, scene_index: int) -> None:
                current = container.video_repository.stage(job_id, stage) or {}
                if current.get("status") == "SUCCEEDED":
                    return
                progress = completed / total if total else 1.0
                container.video_repository.update_stage(
                    job_id,
                    stage,
                    status="RUNNING",
                    progress=progress,
                    output_artifact=json.dumps(
                        {"completed": completed, "total": total, "last_scene_index": scene_index},
                        ensure_ascii=False,
                    ),
                )
                container.video_repository.update_job(
                    job_id,
                    current_stage=stage,
                    overall_progress=_job_progress(container, job_id),
                    heartbeat_at=datetime.now(UTC).isoformat(),
                )
                log.info(
                    "video job checkpoint job_id=%s video_id=%s stage=%s completed=%d total=%d scene=%d",
                    job_id,
                    asset_id,
                    stage,
                    completed,
                    total,
                    scene_index,
                )

            plan_path = container.settings.data_dir / "video-jobs" / job_id / "scene_windows.json"
            frame_stage = container.video_repository.stage(job_id, "FRAME_SAMPLE_VSD") or {}
            planned_windows: tuple[SceneWindow, ...] | None = None
            if plan_path.is_file():
                try:
                    plan_payload = plan_path.read_bytes()
                    plan_digest = hashlib.sha256(plan_payload).hexdigest()
                    if frame_stage.get("input_hash") == plan_digest:
                        raw_windows = json.loads(plan_payload)
                        planned_windows = tuple(
                            SceneWindow(
                                index=int(item["index"]),
                                start_sec=float(item["start_sec"]),
                                end_sec=float(item["end_sec"]),
                                selected_timestamp_sec=float(item["selected_timestamp_sec"]),
                            )
                            for item in raw_windows
                        )
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    planned_windows = None

            event_stage = container.video_repository.stage(job_id, "EVENT_BUILD") or {}
            resume_event_completed = 0
            if event_stage.get("status") != "SUCCEEDED":
                try:
                    checkpoint_payload = json.loads(str(event_stage.get("output_artifact", "{}")))
                    resume_event_completed = int(checkpoint_payload.get("completed", 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    resume_event_completed = 0

            def save_scene_plan(windows: Sequence[SceneWindow]) -> None:
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(
                    [
                        {
                            "index": window.index,
                            "start_sec": window.start_sec,
                            "end_sec": window.end_sec,
                            "selected_timestamp_sec": window.keyframe_timestamp_sec,
                        }
                        for window in windows
                    ],
                    ensure_ascii=False,
                ).encode("utf-8")
                temporary = plan_path.with_suffix(".json.tmp")
                with temporary.open("wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, plan_path)
                container.video_repository.update_stage(
                    job_id,
                    "FRAME_SAMPLE_VSD",
                    input_hash=hashlib.sha256(payload).hexdigest(),
                    output_artifact=str(plan_path),
                )

            if not stage_artifacts_complete:
                extraction = build_video_scenes(
                    container,
                    checkpoint=checkpoint,
                    resume_event_completed=resume_event_completed,
                ).execute(
                    asset_id,
                    transcription_segments=segments,
                    planned_windows=planned_windows,
                    on_scene_plan=save_scene_plan,
                )
                if extraction.reason:
                    raise RuntimeError(f"video scene processing did not complete: {extraction.reason}")
                container.video_repository.save_processing_timings(asset_id, extraction.timings)
                mark("FRAME_SAMPLE_VSD", artifact=f"sqlite:video_keyframes(parent_asset_id={asset_id})")
                mark("EVENT_BUILD", artifact=f"sqlite:events(source_asset_id={asset_id})")
                mark("KEYFRAME_MEMORY", artifact=f"sqlite:observations(video_keyframes={asset_id})")

            graph_stage = container.video_repository.stage(job_id, "MEMORY_GRAPH") or {}
            if graph_stage.get("status") != "SUCCEEDED":
                container.video_repository.update_job(job_id, current_stage="MEMORY_GRAPH")
                container.video_repository.update_stage(
                    job_id,
                    "MEMORY_GRAPH",
                    status="RUNNING",
                    started_at=datetime.now(UTC).isoformat(),
                    attempt=int(str(graph_stage.get("attempt", 0))) + 1,
                )
                graph_stats = refresh_memory_graph(container, asset.scope_id)
                if graph_stats is None:
                    degraded = True
                    container.video_repository.update_stage(
                        job_id,
                        "MEMORY_GRAPH",
                        status="SKIPPED",
                        progress=1.0,
                        error_code="VIDEO-MEMORY-GRAPH-001",
                        error_message="记忆图更新暂不可用，基础视频记忆已保留",
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                else:
                    mark("MEMORY_GRAPH", artifact=f"sqlite:memory_graph(scope_id={asset.scope_id})")
            container.video_repository.update_job(
                job_id,
                status="DEGRADED" if degraded else "COMPLETED",
                current_stage="DEGRADED" if degraded else "COMPLETED",
                overall_progress=1.0,
                worker_id=None,
                heartbeat_at=datetime.now(UTC).isoformat(),
                error_code=None,
                error_message=None,
            )
            container.video_repository.save_processing_seconds(
                asset_id, round(time.perf_counter() - job_started, 3)
            )
            container.asset_repository.update_status(asset_id, AssetStatus.PROCESSED)
            log.info("video job completed job_id=%s video_id=%s", job_id, asset_id)
        finally:
            stop.set()
            heartbeat_thread.join(timeout=1)
    except Exception as error:  # noqa: BLE001 — persist a user-safe error and retain artifacts
        job = container.video_repository.get_job(job_id)
        current_stage = str(job.get("current_stage", "UNKNOWN")) if job else "UNKNOWN"
        error_codes = {
            "UPLOAD": "VIDEO-UPLOAD-001",
            "VIDEO_PROBE": "VIDEO-DECODE-001",
            "SOURCE_MEMORY": "VIDEO-MEMORY-001",
            "SPEECH_TRANSCRIPTION": "VIDEO-ASR-001",
            "FRAME_SAMPLE_VSD": "VIDEO-FRAME-001",
            "EVENT_BUILD": "VIDEO-EVENT-001",
            "KEYFRAME_MEMORY": "VIDEO-MEMORY-001",
            "MEMORY_GRAPH": "VIDEO-INDEX-001",
        }
        error_code = error_codes.get(current_stage, "VIDEO-PROCESS-001")
        log.exception("video job failed job_id=%s stage=%s error_code=%s", job_id, current_stage, error_code)
        if job:
            container.asset_repository.update_status(str(job["asset_id"]), AssetStatus.FAILED)
        stage = container.video_repository.stage(job_id, current_stage)
        if stage and stage.get("status") == "RUNNING":
            container.video_repository.update_stage(
                job_id,
                current_stage,
                status="FAILED",
                error_code=error_code,
                error_message=f"{type(error).__name__}: {error}",
            )
        attempts = int(str(job.get("retry_count", 0))) if job else MAX_VIDEO_ATTEMPTS
        will_retry = attempts < MAX_VIDEO_ATTEMPTS
        container.video_repository.update_job(
            job_id,
            status="RETRY_WAIT" if will_retry else "FAILED",
            error_code=error_code,
            error_message=(
                "处理遇到暂时问题，系统正在重试；已完成步骤会保留。"
                if will_retry
                else "视频处理暂时中断，已完成步骤不会丢失。"
            ),
            worker_id=None,
            heartbeat_at=datetime.now(UTC).isoformat(),
        )
        if will_retry:
            log.warning(
                "video job retry scheduled job_id=%s stage=%s attempt=%d delay_seconds=%.1f",
                job_id,
                current_stage,
                attempts,
                VIDEO_RETRY_DELAY_SECONDS,
            )
            timer = threading.Timer(
                VIDEO_RETRY_DELAY_SECONDS,
                schedule_video_job,
                args=(container, job_id),
            )
            timer.daemon = True
            timer.start()
    finally:
        with _active_video_jobs_lock:
            _active_video_jobs.discard(job_id)


def _job_progress(container: Container, job_id: str) -> float:
    weights = {
        "UPLOAD": 0.05,
        "VIDEO_PROBE": 0.05,
        "SOURCE_MEMORY": 0.15,
        "SPEECH_TRANSCRIPTION": 0.08,
        "FRAME_SAMPLE_VSD": 0.25,
        "EVENT_BUILD": 0.2,
        "KEYFRAME_MEMORY": 0.25,
        "MEMORY_GRAPH": 0.05,
    }
    weighted_progress = sum(
        weights.get(str(row["stage"]), 0) * float(str(row["progress"]))
        for row in container.video_repository.list_stages(job_id)
    )
    return round(weighted_progress / sum(weights.values()), 4)


def build_memory_graph(container: Container) -> BuildMemoryGraph:
    """把 2.0 标准记忆端口接到独立图投影用例。"""
    return BuildMemoryGraph(
        assets=container.asset_repository,
        observations=container.observation_repository,
        events=container.event_repository,
        participants=container.event_participant_repository,
        entities=container.entity_repository,
        facts=container.fact_repository,
        relationships=container.relationship_repository,
        graph=container.memory_graph,
        causal_judge=container.causal_judge,
        vectors=container.vector_repository,
    )


def refresh_memory_graph(container: Container, scope_id: str) -> GraphBuildStats | None:
    """在写入链路结束后重建派生图；图失败不阻断权威记忆写入。"""
    try:
        return build_memory_graph(container).execute(scope_id)
    except Exception as error:  # noqa: BLE001 - 派生投影必须可降级
        log.warning("记忆图重建失败（scope=%s），保留主链路结果: %s", scope_id, error)
        return None


def build_import_assets(container: Container, *, observe: bool) -> ImportAssets:
    """把导入用例的协作者接起来。**只在这里接一次**。

    单张上传与整册导入共用同一条链：分开接的话，「整册少了词法投影」这类差异会以
    「单张能搜到、批量搜不到」的形式出现，而那种 bug 在两端各看一半时都看不出来。

    ``observe`` 控制要不要为每张图付视觉观察的模型调用（首轮 + 补全 + 中文化，
    最多 3 次）。单张上传走快路径（1.0 的 ``_commit_fast_image`` 同样时机），
    整册导入才接观察器。

    **视频抽帧链（S8b）也挂在这一处**，理由同上：单张上传与整册导入都必须建场景，
    分开接会让「整册导入的视频没有场景」以「单张有、批量没有」的形式出现。
    """
    # 关键帧走**另一条 import 实例**，而且固定 ``observe=True``：视频的语义整个都在
    # 关键帧上，跳过观察等于让视频一条语义都不进库——正是 S8a 要修的那种「收下了但
    # 什么都没发生」。裁决④要的是关键帧白得 observation，不是白得一张空观察。
    # 用独立实例而不是复用入参那个，是因为入参的 ``observe`` 由调用方按「一张照片
    # 值不值得三次模型调用」定的，与视频无关。
    keyframe_importer = _build_importer(container, observe=True)
    extractor = _build_video_scenes(container, keyframe_importer.process_image)
    return _build_importer(
        container,
        observe=observe,
        video_scene_extractor=extractor.execute,
    )


def build_video_scenes(
    container: Container,
    *,
    checkpoint: Callable[[str, int, int, int], None] | None = None,
    resume_event_completed: int = 0,
) -> ExtractVideoScenes:
    """独立的抽取链，给 ``POST /videos/{id}/reprocess`` 重跑用（S8b T514）。

    与导入时走的是同一个 ``_build_video_scenes``：重跑与首次导入是同一条链，
    差别只在重跑前先 ``cleanup_derivatives`` 清场。分开写一遍会让「重跑出来的场景
    与首次不一样」，而那种差异只有在对比两次结果时才看得出来。
    """
    return _build_video_scenes(
        container,
        _build_importer(container, observe=True).process_image,
        checkpoint=checkpoint,
        resume_event_completed=resume_event_completed,
    )


def _build_video_scenes(
    container: Container,
    process_image: Callable[..., object],
    *,
    checkpoint: Callable[[str, int, int, int], None] | None = None,
    resume_event_completed: int = 0,
) -> ExtractVideoScenes:
    return ExtractVideoScenes(
        assets=container.asset_repository,
        events=container.event_repository,
        places=container.asset_place_repository,
        video_repository=container.video_repository,
        keyframes=container.video_keyframe_extractor,
        process_image=lambda asset, event_id: process_image(asset, forced_event_id=event_id),
        scene_planner=container.video_scene_planner,
        transcriber=container.audio_transcriber,
        cross_modal_embedder=container.cross_modal_embedder,
        has_memory=lambda asset: bool(container.observation_repository.list_by_asset(asset.id)),
        checkpoint=checkpoint,
        resume_event_completed=resume_event_completed,
    )


def _build_importer(
    container: Container,
    *,
    observe: bool,
    video_scene_extractor: Callable[[str], VideoSceneExtraction | int] | None = None,
) -> ImportAssets:
    """``ImportAssets`` 的装配本体。两个调用方共用：导入本身，以及关键帧那条流水线。"""
    assigner = AssignEvents(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        places=container.asset_place_repository,
        visual_similarity=EventVisualSimilarity(
            vectors=container.vector_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
        ),
        # 事件打分的 person 分量（权重 0.20）的两个来源。装配点**只有这里**：
        # 单张上传与整册导入共用同一条链，分开接会让「整册少了人物桥」以
        # 「批量导入时事件分得更碎」的形式出现，而那种差异在两端口各看一半时看不出来。
        confirmed_people=container.entity_repository.confirmed_people_for_observation,
        confirmed_event_people=container.entity_repository.confirmed_people_for_event,
    )
    return ImportAssets(
        assets=container.asset_repository,
        observations=container.observation_repository,
        storage=container.media_storage,
        metadata=container.metadata_reader,
        resolve_capture_time=container.capture_time_resolver,
        visual_indexer=IndexAssetVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
        place_indexer=IndexAssetPlace(
            resolver=container.place_resolver, places=container.asset_place_repository
        ),
        observer=(
            ObserveAsset(
                observer=container.vlm_observer,
                observations=container.observation_repository,
            )
            if observe
            else None
        ),
        event_assigner=lambda observation: _event_id(assigner, observation),
        # 关键帧的强制归属（S8b T513）。**不受 ``observe`` 影响**：只要有人给了
        # ``forced_event_id`` 就需要它，而那一条路只有视频抽帧会走。
        event_attacher=lambda event_id, observation: container.event_repository.attach(
            event_id, observation.id
        ),
        face_enroller=_face_enroller(container),
        fact_maintainer=_fact_maintainer(container),
        text_indexer=_text_indexer(container),
        entity_extractor=_entity_extractor(container),
        lexical_index=container.lexical_index,
        # 记忆空间行（裁决一）。**只在这里接**：簇列表内连 ``memory_spaces``，
        # 少接这一处，全新部署导入一整册之后审阅页一个簇都不显示（第 97 / 103 条）。
        spaces=container.spaces,
        # 视频探测链（S8a T505）。**接在这里而不是路由里**：单张上传与整册导入共用
        # 这一处装配，分开接会让「整册导入的视频不读元数据」以「单张能读到、批量读不到」
        # 的形式出现——而那种差异在两端各看一半时都看不出来（与上面几处同一条理由）。
        video_metadata=container.video_metadata_reader,
        video_transcoder=container.video_transcoder,
        video_repository=container.video_repository,
        # 音频探测链（S8c T521）。**同样接在这一处**：单张上传与整册导入共用它，
        # 分开接会让「整册导入的音频不读元数据」以「单张能读到、批量读不到」的形式出现。
        audio_metadata=container.audio_metadata_reader,
        audio_repository=container.audio_repository,
        # 抽帧链（S8b T511–T513）。只有导入那条实例拿得到它；关键帧那条实例传 None，
        # 从根上杜绝「关键帧再去抽一层关键帧」的递归。
        video_scene_extractor=video_scene_extractor,
    )


def _face_enroller(container: Container) -> EnrollFaces:
    """人脸登记：检测 → 在线匹配。**两段式**（``EnrollFaces``）。

    **它必须排在事件归并之前**——1.0 在 ``pipeline.py:316-320`` 用注释钉过这件事：
    与已确认的人相像的脸会在这一步写下 ``entity_mentions``，而事件归并的
    ``person_score`` 读的正是它。顺序写在 ``ImportAssets.commit_image`` 里，
    这里只负责接。

    **能并行的是 ``prepare`` 那一次检测，不是匹配**：匹配读同 scope 的候选簇，
    而那些簇是同批前一张照片刚写进去的（``FaceEnroller`` 的 docstring）。

    权重没装的机器上 ``detect`` 返回空表，这条链路整个不生效——**不需要在这里判空**：
    让它自然地「没有脸」比在这里分叉出第二条路径更省事，也更难写错。
    """
    return EnrollFaces(
        detector=container.face_detector,
        enroll=EnrollFace(
            faces=container.face_repository,
            entities=container.entity_repository,
            vectors=container.vector_repository,
            assets=container.asset_repository,
        ),
    )


def _text_indexer(container: Container) -> Callable[[Observation, str | None], int]:
    """文本向量写入的接线。

    **排在事件归并之后**（``ImportAssets.execute`` 的顺序），所以入参带 ``event_id``——
    1.0 那条 ``episodic/event`` 行要的就是它。
    """
    index = IndexObservationText(embedder=container.text_embedder, vectors=container.text_vector_repository)

    def run(observation: Observation, event_id: str | None) -> int:
        return index.index(observation, event_id).rows

    return run


def _entity_extractor(container: Container) -> Callable[[Observation, str], tuple[str, ...]]:
    """非人物实体抽取的接线（S6 T127）。

    **排在事件判定之后**（``ImportAssets.execute`` 的顺序），所以入参带 ``event_id``——
    抽出来的地点/物件/时间要挂到那个事件上（``event_entities``）。与 1.0
    ``pipeline.py:336`` 的位置一致。
    """
    maintain = MaintainObservationEntities(
        entities=container.entity_repository,
        events=container.event_participant_repository,
        assets=container.asset_repository,
        relationships=container.relationship_repository,
    )

    def run(observation: Observation, event_id: str) -> tuple[str, ...]:
        return tuple(entity.id for entity in maintain.execute(observation, event_id=event_id).entities)

    return run


def _fact_maintainer(container: Container) -> Callable[[Observation], tuple[str, ...]]:
    """事实写入的接线。S5b 起两半都在（用户裁决④），S4 的「按设计为空」到此结束。

    1. **候选来源**：模型自报的 ``facts``，落库在 ``observations.facts_json``，读出来
       就是 ``Observation.facts``。1.0 的候选来自 ``analyze_image`` 的同一个数组
       （``model_clients.py:1130-1198``）。
    2. **门禁**：``confirmed_person_names`` 是 1.0 ``is_confirmed_person_name``
       （``db.py:1494-1503``）的集合形态——本 scope 内已确认人物的**本名**。

    **两个入参都不再有默认值**（Task 80 的同一口径）：门禁留一个恒空默认值的话，
    「忘了接线」与「真的没有已确认人物」在类型上完全一样，而事实恒空不报错、
    只是永远查不到。少写一个参数现在是 ``TypeError``。
    """
    maintain = MaintainFacts(
        facts=container.fact_repository,
        candidates=lambda observation: observation.facts,
        confirmed_people=container.entity_repository.confirmed_person_names,
    )
    return maintain.maintain


def _event_id(assigner: AssignEvents, observation: Observation) -> str:
    return assigner.execute(observation).event.id


@router.get("/assets")
def list_assets(
    # 默认与空串都表示**全部相册**：1.0 的 `list_assets(scope_id=None)` 是 `if scope_id:`
    # 才加过滤，而前端用 `""` 表达「全部」（`src/app.js` 的约定）。默认值从
    # `"home-default"` 改成 `""` 是**行为修正**——原来不传 scope 会查一个叫
    # home-default 的空间，而那个空间在 2.0 的空库里根本不存在。
    scope_id: str = "",
    limit: int = 200,
    container: Container = Depends(get_container),
) -> AssetListResponse:
    assets = container.asset_repository.list_by_scope(scope_id or None, min(limit, 1000))
    return AssetListResponse(items=[to_summary(asset) for asset in assets])


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, container: Container = Depends(get_container)) -> AssetSummary:
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return to_summary(asset)


def deliver_asset(asset_id: str, *, original: bool, container: Container) -> Response:
    """按 ``original`` 交付一张资产：``True`` 原文件，``False`` 服务端转码的 JPEG 预览。

    抽出来是因为两条路由共用同一份「怎么把一张已授权的图交出去」：``/assets/{id}/file``
    与助手侧的 ``/assistant/result-sets/{id}/photo``——1.0 的 ``asset_file``
    （``app.py:2450``）也是被这两处共用的。转码参数只留一份。
    """
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="media file missing on disk")
    if original:
        return FileResponse(path, media_type=asset.mime_type or "application/octet-stream")
    payload = container.preview_encoder.encode_jpeg(
        path, max_dimension=PREVIEW_MAX_DIMENSION, quality=PREVIEW_QUALITY
    )
    return Response(content=payload, media_type="image/jpeg")


def deliver_media(asset: Asset, container: Container) -> Response:
    """视频/音频的浏览器交付：**视频能转就交转码件，转不了与原片一律回落原文件**。

    1.0 ``app.py:2452-2458`` 的分流只有一句判据（``media_type == 'video' and not original
    and preview_path.is_file()``），其余一律回落原片。2.0 照搬，但把「转码件在哪」
    的读法收在 ``VideoRepository.probe`` 上——它与写入侧成对，是唯一知道
    ``metadata_json`` 里怎么放的地方。

    **音频走这里是因为它也没有服务端预览件**（S8c T521）：不转码（用户裁决②），
    交付的就是原文件，而 ``probe`` 对音频恒为 ``None``，所以自然落到最后的原片分支。
    把音频挂在这条路上，修掉的是与视频同一个 500——原先 ``/preview`` 无条件走
    ``PillowPreviewEncoder``，而 PIL 既打不开视频也打不开录音。

    **原片分支补了 ``path.exists()`` 检查**（``deliver_asset`` 一直有）：两条路
    交付同一个文件时，缺失文件应当是同一个 404。少了它，音频 ``/file``（走的正是这条
    路）会把「文件不在磁盘上」变成一个 500。
    """
    probe = container.video_repository.probe(asset.id)
    preview_path = Path(probe.browser_preview_path) if probe and probe.browser_preview_path else None
    if preview_path is not None and preview_path.is_file():
        return FileResponse(
            preview_path,
            media_type="video/mp4",
            filename=f"{Path(asset.file_name).stem}-preview.mp4",
            headers={"Cache-Control": "private, max-age=86400", "X-Sentrix-Source": "browser-preview"},
        )
    # 没有转码件（H.264 不用转 / 转码失败 / 还没探测过 / 音频）一律交原片：
    # 浏览器放不放得了是它的事，服务端不做第二次判断。
    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="media file missing on disk")
    return FileResponse(
        path,
        media_type=asset.mime_type or "application/octet-stream",
        filename=asset.file_name,
    )


def _self_served_or_none(asset_id: str, container: Container) -> Asset | None:
    """交付物**就是文件本身**的资产：视频（有转码件时给转码件）与音频（永远给原片）。

    图片不在内——它有服务端渲染的 JPEG 预览，走 ``PillowPreviewEncoder``。
    """
    asset = container.asset_repository.get(asset_id)
    if asset is None or asset.media_type not in (MediaType.VIDEO, MediaType.AUDIO):
        return None
    return asset


@router.get("/assets/{asset_id}/file")
def get_asset_file(
    asset_id: str,
    original: bool = False,
    container: Container = Depends(get_container),
) -> Response:
    """``?original=1`` 拿原片；不传时**视频**给浏览器预览件（1.0 ``app.py:2448-2478``）。

    音频与图片这条路上 ``original`` 是空操作：音频本来就只有原片可交
    （``deliver_media`` 对音频恒回落原文件），图片的 ``/file`` 一直是原文件
    （契约测试钉着「交出去的字节与传上来的一模一样」），预览在 ``/preview``。
    不为了对称去改图片——改了会让 HEIC 从「原图直出」变成「转码件直出」，那是另一个决定。
    """
    asset = _self_served_or_none(asset_id, container)
    if asset is not None and not original:
        return deliver_media(asset, container)
    return deliver_asset(asset_id, original=True, container=container)


@router.get("/assets/{asset_id}/preview")
def get_asset_preview(asset_id: str, container: Container = Depends(get_container)) -> Response:
    asset = _self_served_or_none(asset_id, container)
    if asset is not None:
        return deliver_media(asset, container)
    return deliver_asset(asset_id, original=False, container=container)
