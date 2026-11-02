"""浏览器侧关键动作的轻量诊断日志。

只记录动作类型、文件元信息和错误文本，不记录图片内容、文件路径或问答正文。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from sentrix.control.auth import AuthContext, current_auth, require_csrf

log = logging.getLogger("sentrix.control.client")
router = APIRouter()


class ClientEvent(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    detail: str = Field(default="", max_length=500)


@router.post("/client-events")
def client_event(
    payload: ClientEvent,
    request: Request,
    auth: AuthContext = Depends(current_auth),
) -> dict[str, object]:
    require_csrf(request, auth)
    log.warning(
        "client event account=%s kind=%s detail=%s ua=%s",
        auth.account.id,
        payload.kind,
        payload.detail,
        request.headers.get("user-agent", "")[:120],
    )
    return {"ok": True}
