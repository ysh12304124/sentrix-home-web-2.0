"""媒体导入与交付。只做 HTTP 映射，业务在 application 层。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from sentrix.api.deps import get_container
from sentrix.api.schemas.import_jobs import UploadAcceptedResponse
from sentrix.api.schemas.media import AssetListResponse, AssetSummary, ImportResponse
from sentrix.application.media.import_assets import ImportRequest
from sentrix.bootstrap.container import Container
from sentrix.bootstrap.import_wiring import (
    build_import_assets,
    build_video_scenes,
    refresh_memory_graph,
)
from sentrix.domain.media.import_jobs import (
    IdempotencyConflict,
    ImportJob,
    UploadBusy,
    UploadQuotaExceeded,
)
from sentrix.domain.media.models import Asset, MediaType

router = APIRouter(tags=["media"])
log = logging.getLogger("sentrix.api.media")

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

    use_case = build_import_assets(container, observe=False)
    result = use_case.execute(
        ImportRequest(
            scope_id=scope_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type,
            content=content,
        )
    )
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


_UPLOAD_CHUNK = 1024 * 1024
_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sniff_media_type(head: bytes) -> MediaType | None:
    """按文件头判定类型；文件名与上传方 content-type 只作提示。"""
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaType.IMAGE
    if head[:6] in {b"GIF87a", b"GIF89a"}:
        return MediaType.IMAGE
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return MediaType.IMAGE
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return MediaType.VIDEO
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return MediaType.AUDIO
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif"}:
            return MediaType.IMAGE
        if brand in {b"M4A ", b"M4B "}:
            return MediaType.AUDIO
        return MediaType.VIDEO
    if head.startswith(b"ID3") or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return MediaType.AUDIO
    if head.startswith((b"OggS", b"fLaC")):
        return MediaType.AUDIO
    return None


def _upload_limit(media: MediaType, container: Container) -> int:
    if media is MediaType.IMAGE:
        return container.settings.upload_photo_max_bytes
    return container.settings.upload_video_max_bytes


@router.post("/media/uploads", status_code=202)
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    scope_id: str = Form("home-default"),
    container: Container = Depends(get_container),
) -> UploadAcceptedResponse:
    """流式接收一个文件。只有原件落盘且作业入库后才返回 202。"""
    settings = container.settings
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key 缺失或过长")
    if not _SCOPE_PATTERN.fullmatch(scope_id):
        raise HTTPException(status_code=400, detail="scope_id 不合法")
    file_name = file.filename or "unnamed"
    declared = MediaType.from_upload(file.content_type, file_name)
    try:
        declared_size = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared_size = 0
    if declared_size > settings.upload_video_max_bytes:
        raise HTTPException(status_code=413, detail="文件超过单文件上限")

    expected = declared_size or settings.upload_video_max_bytes
    try:
        free_bytes = shutil.disk_usage(settings.data_dir).free
    except OSError:
        free_bytes = 0
    try:
        reservation = container.import_jobs.reserve_upload(
            expected,
            quota_bytes=settings.upload_quota_bytes,
            max_inflight=settings.upload_max_inflight,
            free_bytes=free_bytes,
            min_free_bytes=settings.upload_min_free_bytes,
        )
    except UploadBusy as error:
        raise HTTPException(status_code=429, detail="同时上传的文件已达到上限，请稍后重试") from error
    except UploadQuotaExceeded as error:
        raise HTTPException(status_code=507, detail="存储配额或磁盘空间不足，暂停接收新上传") from error

    staging_path = container.media_storage.staging_dir / f"{reservation}.part"
    final_path: Path | None = None
    accepted = False
    digest = hashlib.sha256()
    size = 0
    media: MediaType | None = None
    try:
        with staging_path.open("wb") as handle:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                if media is None:
                    media = _sniff_media_type(chunk) or declared
                if media is MediaType.TEXT:
                    raise HTTPException(status_code=415, detail="仅支持照片、视频和音频")
                size += len(chunk)
                if size > _upload_limit(media, container):
                    raise HTTPException(status_code=413, detail="文件超过该媒体类型的大小上限")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if size == 0:
            raise HTTPException(status_code=400, detail="空文件")
        if media is None:
            media = declared

        asset_id = f"asset_{uuid.uuid4().hex[:12]}"
        final_path = container.media_storage.move_from_path(asset_id, file_name, staging_path)
        now = datetime.now(UTC)
        job = ImportJob(
            id=f"job_{uuid.uuid4().hex[:16]}",
            asset_id=asset_id,
            scope_id=scope_id,
            # 真实接收序号在 accept() 的写锁事务里分配。
            receive_seq=0,
            file_name=file_name,
            media_type=media.value,
            content_sha256=digest.hexdigest(),
            size_bytes=size,
            status="accepted",
            stage="accepted",
            attempts=0,
            error_category="",
            error_message="",
            idempotency_key=idempotency_key,
            accepted_at=now,
            updated_at=now,
            original_path=str(final_path),
        )
        try:
            stored, created = container.import_jobs.accept(
                job,
                reservation_id=reservation,
                quota_bytes=settings.upload_quota_bytes,
            )
        except IdempotencyConflict as error:
            raise HTTPException(
                status_code=409, detail="同一幂等键已用于不同内容"
            ) from error
        except UploadQuotaExceeded as error:
            raise HTTPException(
                status_code=507, detail="存储配额不足，暂停接收新上传"
            ) from error
        accepted = True
        if not created and stored.asset_id != asset_id:
            # 重试的新副本不是权威原件，删掉；返回第一次接收的作业。
            final_path.unlink(missing_ok=True)
        return UploadAcceptedResponse(
            job_id=stored.id, asset_id=stored.asset_id, status="accepted"
        )
    finally:
        if not accepted:
            container.import_jobs.release_upload(reservation)
            staging_path.unlink(missing_ok=True)
            if final_path is not None:
                final_path.unlink(missing_ok=True)


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


@router.get("/assets/{asset_id}/poster")
def asset_poster(asset_id: str, container: Container = Depends(get_container)) -> Response:
    """视频封面：优先复用已抽取的关键帧，没有就抽第一帧；图片直接给预览。"""
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.media_type is not MediaType.VIDEO:
        return deliver_asset(asset_id, original=False, container=container)

    cache = {"Cache-Control": "private, max-age=86400"}
    for derived in container.video_repository.list_derived(asset.id):
        path = Path(derived.path)
        if path.is_file():
            return FileResponse(path, media_type="image/jpeg", headers=cache)
    try:
        path = container.video_keyframe_extractor.extract(Path(asset.path), asset.id, 0, 0.0)
    except Exception as error:  # noqa: BLE001 - 取不到封面就明确 404
        raise HTTPException(status_code=404, detail="video poster unavailable") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="video poster unavailable")
    return FileResponse(path, media_type="image/jpeg", headers=cache)


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
