"""管理员概览、事件与固定服务的重启动作。"""

from __future__ import annotations

import os
import subprocess
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request

import httpx

from sentrix.control.auth import AuthContext, require_admin, require_csrf

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


def _route(request: Request, account_id: str):
    routes = request.app.state.route_table
    route = routes.get(account_id)
    if route is None:
        raise HTTPException(status_code=404, detail="user not found")
    return route


@router.get("/admin/users/{account_id}/jobs")
async def user_jobs(
    account_id: str, request: Request, _: AuthContext = Depends(require_admin)
) -> dict[str, object]:
    route = _route(request, account_id)
    client: httpx.AsyncClient = request.app.state.http_client
    response = await client.get(
        f"{route.api_url}/api/v1/import-jobs",
        headers={"x-sentrix-internal-token": route.api_token},
    )
    if not response.is_success:
        raise HTTPException(status_code=502, detail=f"user API returned {response.status_code}")
    return response.json()


@router.post("/admin/users/{account_id}/jobs/{job_id}/requeue")
async def requeue_job(
    account_id: str,
    job_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    require_csrf(request, auth)
    route = _route(request, account_id)
    client: httpx.AsyncClient = request.app.state.http_client
    response = await client.post(
        f"{route.api_url}/api/v1/import-jobs/{job_id}/requeue",
        headers={"x-sentrix-internal-token": route.api_token},
    )
    result = "ok" if response.is_success else "failed"
    _store(request).audit(
        "job.requeue",
        actor_account_id=auth.account.id,
        target=f"{account_id}:{job_id}",
        result=result,
        detail={"status_code": response.status_code},
    )
    if not response.is_success:
        raise HTTPException(
            status_code=response.status_code if response.status_code >= 400 else 502,
            detail="requeue rejected by user API",
        )
    return response.json()  # type: ignore[no-any-return]


@router.post("/admin/services/{service_id}/restart")
def restart_service(
    service_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, object]:
    settings = request.app.state.control_settings
    allowed = set(settings.restart_unit_list())
    require_csrf(request, auth)
    if service_id not in allowed:
        raise HTTPException(status_code=403, detail="service is not in the restart allowlist")
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    # 只接受固定单元名；不拼接用户输入，不经过 shell。
    command = (
        ["systemctl", "restart", service_id]
        if os.geteuid() == 0
        else ["sudo", "-n", "systemctl", "restart", service_id]
    )
    completed = subprocess.run(
        command,
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
