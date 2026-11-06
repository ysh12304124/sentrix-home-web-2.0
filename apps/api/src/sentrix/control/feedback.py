"""反馈闭环：用户提交/补充、管理员收件箱与受控截图。"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from sentrix.control.auth import (
    AuthContext,
    current_auth,
    require_admin,
    require_csrf,
)
from sentrix.control.store import AccountError, ControlStore

router = APIRouter()
admin_router = APIRouter()

MAX_SCREENSHOT = 5 * 1024 * 1024


def _normalize_time(value: str) -> str:
    """把浏览器的 ``Z`` 时间统一成库内 ``+00:00`` 形态，才能按字符串比较。"""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid time filter") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


class FeedbackCreate(BaseModel):
    kind: Literal["problem", "inaccurate", "suggestion"]
    body: str = Field(min_length=1, max_length=4000)
    route: str = Field(default="", max_length=300)
    scope_id: str = Field(default="", max_length=200)
    job_id: str = Field(default="", max_length=200)
    turn_id: str = Field(default="", max_length=200)
    event_id: str = Field(default="", max_length=200)
    asset_id: str = Field(default="", max_length=200)
    request_id: str = Field(default="", max_length=200)


class FeedbackUpdateIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class AdminFeedbackPatch(BaseModel):
    status: Literal["received", "triaging", "processed"] | None = None
    assignee: str | None = Field(default=None, max_length=120)
    severity: str | None = Field(default=None, max_length=40)
    internal_note: str | None = Field(default=None, max_length=4000)
    reply: str | None = Field(default=None, max_length=4000)


def _store(request: Request) -> ControlStore:
    return request.app.state.control_store


def _settings(request: Request):
    return request.app.state.control_settings


def _signature(payload: FeedbackCreate) -> str:
    raw = "|".join(
        [
            payload.kind,
            payload.body.strip(),
            payload.route,
            payload.scope_id,
            payload.job_id,
            payload.turn_id,
            payload.event_id,
            payload.asset_id,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/feedback", status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    auth: AuthContext = Depends(current_auth),
) -> dict[str, object]:
    require_csrf(request, auth)
    key = idempotency_key.strip()
    if not key or len(key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key 缺失或过长")
    try:
        feedback, _ = _store(request).create_feedback(
            auth.account.id,
            kind=payload.kind,
            body=payload.body.strip(),
            route=payload.route,
            scope_id=payload.scope_id,
            job_id=payload.job_id,
            turn_id=payload.turn_id,
            event_id=payload.event_id,
            asset_id=payload.asset_id,
            request_id=payload.request_id,
            idempotency_key=key,
            content_signature=_signature(payload),
        )
    except AccountError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return feedback


@router.get("/feedback")
def list_own_feedback(
    request: Request, auth: AuthContext = Depends(current_auth)
) -> dict[str, object]:
    return {"items": _store(request).list_feedback(auth.account.id)}


@router.get("/feedback/{feedback_id}")
def get_own_feedback(
    feedback_id: str, request: Request, auth: AuthContext = Depends(current_auth)
) -> dict[str, object]:
    feedback = _store(request).get_feedback(feedback_id, account_id=auth.account.id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    feedback["updates"] = _store(request).list_feedback_updates(feedback_id)
    return feedback


@router.post("/feedback/{feedback_id}/updates", status_code=201)
def add_own_update(
    feedback_id: str,
    payload: FeedbackUpdateIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
) -> dict[str, object]:
    require_csrf(request, auth)
    if _store(request).get_feedback(feedback_id, account_id=auth.account.id) is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    return _store(request).add_feedback_update(
        feedback_id, author=auth.account.id, body=payload.body.strip()
    )


def _sniff_image(head: bytes) -> str:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    raise HTTPException(status_code=415, detail="截图只接受 PNG、JPEG 或 WebP")


@router.post("/feedback/{feedback_id}/attachment", status_code=201)
async def upload_attachment(
    feedback_id: str,
    request: Request,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
) -> dict[str, object]:
    require_csrf(request, auth)
    store = _store(request)
    if store.get_feedback(feedback_id, account_id=auth.account.id) is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    content = await file.read(MAX_SCREENSHOT + 1)
    if len(content) > MAX_SCREENSHOT:
        raise HTTPException(status_code=413, detail="截图超过 5 MiB")
    content_type = _sniff_image(content[:16])
    attachment_id = f"fba_{uuid.uuid4().hex[:20]}"
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
    path = _settings(request).feedback_dir / f"{attachment_id}.{extension}"
    path.write_bytes(content)
    os.chmod(path, 0o600)
    return store.add_feedback_attachment(
        feedback_id,
        content_type=content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_path=str(path),
    )


@router.get("/feedback/attachments/{attachment_id}")
def download_own_attachment(
    attachment_id: str, request: Request, auth: AuthContext = Depends(current_auth)
) -> FileResponse:
    attachment = _store(request).get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    owner = _store(request).get_feedback(
        str(attachment["feedback_id"]), account_id=auth.account.id
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return FileResponse(
        str(attachment["storage_path"]),
        media_type=str(attachment["content_type"]),
        filename=f"feedback-{attachment_id}",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ---- 管理员 ---------------------------------------------------------------


@admin_router.get("/admin/feedback")
def admin_feedback(
    request: Request,
    account_id: str = "",
    kind: str = "",
    status: str = "",
    since: str = "",
    until: str = "",
    related: str = "",
    auth: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    del auth
    store = _store(request)
    items = store.list_feedback_admin(
        account_id=account_id,
        kind=kind,
        status=status,
        since=_normalize_time(since),
        until=_normalize_time(until),
        related=related,
    )
    for item in items:
        item["attachments"] = store.list_feedback_attachments(str(item["id"]))
    return {"items": items}


@admin_router.get("/admin/feedback/{feedback_id}")
def admin_feedback_detail(
    feedback_id: str, request: Request, auth: AuthContext = Depends(require_admin)
) -> dict[str, object]:
    store = _store(request)
    feedback = store.get_feedback_admin(feedback_id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    feedback["updates"] = store.list_feedback_updates(feedback_id)
    feedback["attachments"] = store.list_feedback_attachments(feedback_id)
    store.audit(
        "feedback.view",
        actor_account_id=auth.account.id,
        target=feedback_id,
        result="ok",
    )
    return feedback


@admin_router.patch("/admin/feedback/{feedback_id}")
def admin_patch_feedback(
    feedback_id: str,
    payload: AdminFeedbackPatch,
    request: Request,
    auth: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    require_csrf(request, auth)
    store = _store(request)
    if not store.update_feedback_admin(
        feedback_id,
        status=payload.status,
        assignee=payload.assignee,
        severity=payload.severity,
        internal_note=payload.internal_note,
        reply=payload.reply,
    ):
        raise HTTPException(status_code=404, detail="feedback not found")
    store.audit(
        "feedback.update",
        actor_account_id=auth.account.id,
        target=feedback_id,
        result="ok",
        detail=payload.model_dump(exclude_none=True),
    )
    return store.get_feedback_admin(feedback_id) or {}


@admin_router.get("/admin/feedback/attachments/{attachment_id}")
def admin_download_attachment(
    attachment_id: str, request: Request, auth: AuthContext = Depends(require_admin)
) -> FileResponse:
    attachment = _store(request).get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    _store(request).audit(
        "feedback.attachment.download",
        actor_account_id=auth.account.id,
        target=attachment_id,
        result="ok",
    )
    return FileResponse(
        str(attachment["storage_path"]),
        media_type=str(attachment["content_type"]),
        filename=f"feedback-{attachment_id}",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )
