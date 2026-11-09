"""浏览器侧关键动作的结构化轨迹。

协议只接受固定事件名、固定路由模板、固定结果码和已有对象 ID。任何自由文本都会
被拒绝；账号与会话由认证上下文填写。上报失败不影响上传、问答等主流程。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sentrix.control.auth import AuthContext, current_auth, require_csrf

log = logging.getLogger("sentrix.control.client")
router = APIRouter()

EVENT_NAMES = frozenset(
    {
        "page.view",
        "nav.click",
        "scope.change",
        "feedback.open",
        "auth.logout",
        "onboarding.open",
        "onboarding.skip",
        "onboarding.complete",
        "help.open",
        "memory.next",
        "memory.prev",
        "memory.pause",
        "memory.resume",
        "memory.video_play",
        "memory.media_error",
        "import.pick",
        "import.upload_start",
        "import.upload_accepted",
        "import.upload_failed",
        "import.cancel",
        "assistant.ask",
        "assistant.accepted",
        "assistant.stop",
        "assistant.complete",
        "people.view",
        "people.detail_open",
        "people.confirm_submit",
        "people.confirm_result",
        "people.filter",
        "library.view",
        "library.detail_open",
        "library.filter",
        "library.media_error",
        "timeline.month",
        "timeline.date",
        "timeline.event_open",
        "timeline.media_error",
    }
)
ROUTE_TEMPLATES = frozenset({"/", "/assistant", "/timeline", "/people", "/library", "/imports"})
RESULT_CODES = frozenset(
    {"started", "accepted", "ok", "partial", "failed", "cancelled", "rejected", "ignored"}
)
ERROR_CODES = frozenset(
    {"network", "timeout", "http_4xx", "http_5xx", "media_load", "video_play", "unknown"}
)
_ID = r"^$|^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class ClientEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(min_length=1, max_length=64)
    route: str = Field(min_length=1, max_length=32)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
    result: str = Field(min_length=1, max_length=16)
    error_code: str = Field(default="", max_length=16)
    scope_id: str = Field(default="", max_length=128, pattern=_ID)
    job_id: str = Field(default="", max_length=128, pattern=_ID)
    turn_id: str = Field(default="", max_length=128, pattern=_ID)
    event_id: str = Field(default="", max_length=128, pattern=_ID)
    asset_id: str = Field(default="", max_length=128, pattern=_ID)
    entity_id: str = Field(default="", max_length=128, pattern=_ID)

    @field_validator("event_name")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in EVENT_NAMES:
            raise ValueError("unknown event name")
        return value

    @field_validator("route")
    @classmethod
    def _known_route(cls, value: str) -> str:
        if value not in ROUTE_TEMPLATES:
            raise ValueError("unknown route template")
        return value

    @field_validator("result")
    @classmethod
    def _known_result(cls, value: str) -> str:
        if value not in RESULT_CODES:
            raise ValueError("unknown result code")
        return value

    @field_validator("error_code")
    @classmethod
    def _known_error(cls, value: str) -> str:
        if value and value not in ERROR_CODES:
            raise ValueError("unknown error code")
        return value


@router.post("/client-events")
def client_event(
    payload: ClientEventRequest,
    request: Request,
    auth: AuthContext = Depends(current_auth),
) -> dict[str, object]:
    require_csrf(request, auth)
    store = request.app.state.control_store
    try:
        event_id = store.record_client_event(
            account_id=auth.account.id,
            session_id=auth.session.id,
            operation_id=payload.operation_id,
            event_name=payload.event_name,
            route=payload.route,
            result=payload.result,
            scope_id=payload.scope_id,
            job_id=payload.job_id,
            turn_id=payload.turn_id,
            event_id=payload.event_id,
            asset_id=payload.asset_id,
            entity_id=payload.entity_id,
            error_code=payload.error_code,
        )
    except Exception:  # noqa: BLE001 - 诊断写入失败不能影响业务主流程
        log.warning("client event write failed account=%s event=%s", auth.account.id, payload.event_name)
        raise HTTPException(status_code=503, detail="event store unavailable") from None
    return {"ok": True, "id": event_id}
