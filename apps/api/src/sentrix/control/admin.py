"""管理员概览、事件、趋势与固定服务的重启动作。"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request

import httpx

from sentrix.control.auth import AuthContext, require_admin, require_csrf

router = APIRouter()


def _store(request: Request):
    return request.app.state.control_store


def _monitor(request: Request):
    return request.app.state.monitor


def _settings(request: Request):
    return request.app.state.control_settings


@router.get("/admin/overview")
def overview(request: Request, _: AuthContext = Depends(require_admin)) -> dict[str, object]:
    payload = dict(_monitor(request).snapshot())
    settings = _settings(request)
    if settings.admin_only:
        # 管理入口不转发模型流量，自己的准入队列恒为空；读主入口落库的真实队列。
        latest = _store(request).latest_metric(source="control")
        if latest is not None:
            queue = (latest.get("payload") or {}).get("model_queue")
            if isinstance(queue, dict):
                payload["model_queue"] = queue
                payload["model_queue_sampled_at"] = latest["sampled_at"]
    unmanaged = list(settings.unmanaged_model_consumer_list())
    payload.update(
        {
            "build_sha": settings.build_sha,
            "frontend_build": settings.frontend_build,
            "shared_model_units": list(settings.shared_model_unit_list()),
            "unmanaged_model_consumers": unmanaged,
            "scheduling_complete": not unmanaged,
        }
    )
    return payload


@router.get("/admin/trends")
def trends(
    request: Request,
    _: AuthContext = Depends(require_admin),
    minutes: int = 60,
) -> dict[str, object]:
    window = max(1, min(minutes, 24 * 60))
    since = (datetime.now(UTC) - timedelta(minutes=window)).isoformat()
    return {
        "minutes": window,
        "items": _store(request).list_metric_series(source="minute", since=since),
    }


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


@router.get("/admin/users/{account_id}/client-events")
def user_client_events(
    account_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    after_id: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    # 只允许查询配置中的体验账号；管理员账号没有用户 API，也不该有轨迹。
    _route(request, account_id)
    items, has_more = _store(request).list_client_events(
        account_id, after_id=max(0, after_id), limit=limit
    )
    if after_id <= 0:
        # 打开轨迹记一条审计；2 秒轮询带 after_id，不重复记。
        _store(request).audit(
            "client_events.view",
            actor_account_id=auth.account.id,
            target=account_id,
            result="ok",
        )
    visible = [
        {
            "id": int(item["id"]),
            "received_at": str(item["received_at"]),
            "operation_id": str(item["operation_id"]),
            "event_name": str(item["event_name"]),
            "route": str(item["route"]),
            "result": str(item["result"]),
            "scope_id": str(item["scope_id"]),
            "job_id": str(item["job_id"]),
            "turn_id": str(item["turn_id"]),
            "event_id": str(item["event_id"]),
            "asset_id": str(item["asset_id"]),
            "entity_id": str(item["entity_id"]),
            "error_code": str(item["error_code"]),
        }
        for item in items
    ]
    next_cursor = int(visible[-1]["id"]) if visible else after_id
    return {"items": visible, "next_cursor": next_cursor, "has_more": has_more}


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
    settings = _settings(request)
    store = _store(request)
    allowed = set(settings.restart_unit_list())
    require_csrf(request, auth)
    if service_id not in allowed:
        raise HTTPException(status_code=403, detail="service is not in the restart allowlist")
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")

    existing = store.get_restart_request(key)
    if existing is not None:
        if str(existing["service_id"]) != service_id:
            raise HTTPException(
                status_code=409, detail="idempotency key reused for a different service"
            )
        body = json.loads(str(existing["body_json"]))
        store.audit(
            "service.restart",
            actor_account_id=auth.account.id,
            target=service_id,
            result="replayed",
            detail={"idempotency_key": key},
            request_id=str(existing["request_id"]),
        )
        if int(existing["status_code"]) >= 400:
            raise HTTPException(
                status_code=int(existing["status_code"]),
                detail=str(body.get("detail", "restart failed")),
            )
        return {**body, "idempotent_replay": True}

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
    shared = service_id in set(settings.shared_model_unit_list())
    body: dict[str, object] = {
        "service_id": service_id,
        "result": result,
        "request_id": request_id,
        "affected_all_users": shared,
    }
    status_code = 200 if completed.returncode == 0 else 502
    if completed.returncode != 0:
        body["detail"] = f"restart failed: {completed.stderr[-300:]}"
    store.audit(
        "service.restart",
        actor_account_id=auth.account.id,
        target=service_id,
        result=result,
        detail={
            "returncode": completed.returncode,
            "stderr": completed.stderr[-500:],
            "idempotency_key": key,
            "affected_all_users": shared,
        },
        request_id=request_id,
    )
    store.save_restart_request(
        key,
        service_id=service_id,
        request_id=request_id,
        status_code=status_code,
        body=body,
    )
    if completed.returncode != 0:
        raise HTTPException(status_code=502, detail=str(body["detail"]))
    return body
