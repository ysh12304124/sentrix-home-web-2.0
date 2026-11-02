"""管理员概览、事件与固定服务的重启动作。"""

from __future__ import annotations

import subprocess
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from sentrix.control.auth import AuthContext, require_admin

router = APIRouter()


def _store(request: Request):
    return request.app.state.control_store


def _monitor(request: Request):
    return request.app.state.monitor


@router.get("/admin/overview")
def overview(request: Request, _: AuthContext = Depends(require_admin)) -> dict[str, object]:
    return _monitor(request).snapshot()


@router.get("/admin/incidents")
def incidents(
    request: Request, _: AuthContext = Depends(require_admin), limit: int = 100
) -> dict[str, object]:
    return {"items": _store(request).list_incidents(limit=min(limit, 500))}


@router.get("/admin/users/{account_id}")
def user_detail(
    account_id: str, request: Request, _: AuthContext = Depends(require_admin)
) -> dict[str, object]:
    services = _monitor(request).snapshot().get("services", {})
    users = services.get("users", {}) if isinstance(services, dict) else {}
    if not isinstance(users, dict) or account_id not in users:
        raise HTTPException(status_code=404, detail="user not found")
    return {"account_id": account_id, "status": users[account_id]}


@router.post("/admin/services/{service_id}/restart")
def restart_service(
    service_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, object]:
    settings = request.app.state.control_settings
    allowed = set(settings.restart_unit_list())
    if service_id not in allowed:
        raise HTTPException(status_code=403, detail="service is not in the restart allowlist")
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    # 只接受固定单元名；不拼接用户输入，不经过 shell。
    completed = subprocess.run(
        ["systemctl", "restart", service_id],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    result = "ok" if completed.returncode == 0 else "failed"
    _store(request).audit(
        "service.restart",
        actor_account_id=auth.account.id,
        target=service_id,
        result=result,
        detail={
            "returncode": completed.returncode,
            "stderr": completed.stderr[-500:],
            "idempotency_key": idempotency_key,
        },
        request_id=request_id,
    )
    if completed.returncode != 0:
        raise HTTPException(status_code=502, detail=f"restart failed: {completed.stderr[-300:]}")
    return {"service_id": service_id, "result": result, "request_id": request_id}
