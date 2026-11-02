"""控制面登录、会话与 CSRF。

会话令牌只以 SHA-256 摘要入库；浏览器拿到的 cookie 是 HttpOnly 的原始令牌。
CSRF 采用双提交：非 HttpOnly 的 csrf cookie + 请求头，服务端再与库内摘要比对。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sentrix.control import security
from sentrix.control.config import ControlSettings
from sentrix.control.store import Account, ControlStore, SessionRecord

CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOGIN_WINDOW_S = 600
MAX_PAIR_FAILURES = 5
MAX_IP_FAILURES = 20


@dataclass(frozen=True)
class AuthContext:
    account: Account
    session: SessionRecord
    token: str


class LoginRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def _store(request: Request) -> ControlStore:
    return request.app.state.control_store


def _settings(request: Request) -> ControlSettings:
    return request.app.state.control_settings


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _cookie_names(settings: ControlSettings) -> tuple[str, str]:
    return f"{settings.cookie_namespace}_session", f"{settings.cookie_namespace}_csrf"


def current_auth(request: Request) -> AuthContext:
    settings = _settings(request)
    session_cookie, _ = _cookie_names(settings)
    token = request.cookies.get(session_cookie, "")
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    view = _store(request).get_session(
        token, idle_s=settings.session_idle_s, max_s=settings.session_max_s
    )
    if view is None:
        raise HTTPException(status_code=401, detail="session expired")
    return AuthContext(account=view.account, session=view.session, token=token)


def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.account.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return auth


def require_csrf(request: Request, auth: AuthContext) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    _, csrf_cookie = _cookie_names(_settings(request))
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(csrf_cookie, "")
    if not header or not cookie or not security.constant_time_equal(header, cookie):
        raise HTTPException(status_code=403, detail="csrf token missing or invalid")
    if not security.constant_time_equal(security.token_digest(header), auth.session.csrf_hash):
        raise HTTPException(status_code=403, detail="csrf token does not match session")


def _set_cookies(
    response: Response, settings: ControlSettings, token: str, csrf_token: str
) -> None:
    session_cookie, csrf_cookie = _cookie_names(settings)
    max_age = min(settings.session_idle_s, settings.session_max_s)
    response.set_cookie(
        session_cookie,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        csrf_cookie,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )


def _clear_cookies(response: Response, settings: ControlSettings) -> None:
    session_cookie, csrf_cookie = _cookie_names(settings)
    response.delete_cookie(session_cookie, path="/")
    response.delete_cookie(csrf_cookie, path="/")


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    def login(payload: LoginRequest, request: Request) -> Response:
        store = _store(request)
        settings = _settings(request)
        ip = _client_ip(request)
        request_id = f"req_{uuid.uuid4().hex[:16]}"

        pair_failures, ip_failures = store.login_failures(
            payload.account_id, ip, window_s=LOGIN_WINDOW_S
        )
        if pair_failures >= MAX_PAIR_FAILURES or ip_failures >= MAX_IP_FAILURES:
            store.audit(
                "auth.login",
                target=payload.account_id,
                result="rate_limited",
                detail={"ip": ip},
                request_id=request_id,
            )
            raise HTTPException(
                status_code=429,
                detail="too many failed logins; retry later",
                headers={"Retry-After": str(LOGIN_WINDOW_S)},
            )

        account = store.get_account(payload.account_id)
        if account is None or not store.verify_account_password(account, payload.password):
            store.record_login_attempt(payload.account_id, ip, success=False)
            store.audit(
                "auth.login",
                target=payload.account_id,
                result="denied",
                detail={"ip": ip},
                request_id=request_id,
            )
            raise HTTPException(status_code=401, detail="invalid account or password")
        if settings.admin_only and account.role != "admin":
            store.record_login_attempt(payload.account_id, ip, success=False)
            store.audit(
                "auth.login",
                target=payload.account_id,
                result="admin_only",
                detail={"ip": ip},
                request_id=request_id,
            )
            raise HTTPException(status_code=403, detail="this entry accepts administrator accounts only")

        store.record_login_attempt(payload.account_id, ip, success=True)
        store.purge_login_attempts(older_than_s=LOGIN_WINDOW_S * 6)
        token = security.new_token()
        csrf_token = security.new_token()
        session = store.create_session(
            account.id,
            token=token,
            csrf_token=csrf_token,
            ip=ip,
            user_agent=request.headers.get("user-agent", "")[:256],
            idle_s=settings.session_idle_s,
            max_s=settings.session_max_s,
        )
        store.audit(
            "auth.login",
            actor_account_id=account.id,
            target=account.id,
            result="ok",
            detail={"ip": ip},
            request_id=request_id,
        )
        response = JSONResponse(
            {
                "account_id": account.id,
                "display_name": account.display_name,
                "role": account.role,
                "expires_at": session.expires_at,
                "admin_only": settings.admin_only,
            }
        )
        _set_cookies(response, settings, token, csrf_token)
        return response

    @router.post("/auth/logout", status_code=204)
    def logout(
        request: Request,
        auth: AuthContext = Depends(current_auth),
    ) -> Response:
        require_csrf(request, auth)
        _store(request).revoke_session(auth.token)
        _store(request).audit(
            "auth.logout",
            actor_account_id=auth.account.id,
            target=auth.account.id,
            result="ok",
        )
        response = Response(status_code=204)
        _clear_cookies(response, _settings(request))
        return response

    @router.get("/auth/me")
    def me(
        request: Request, auth: AuthContext = Depends(current_auth)
    ) -> dict[str, object]:
        return {
            "account_id": auth.account.id,
            "display_name": auth.account.display_name,
            "role": auth.account.role,
            "expires_at": auth.session.expires_at,
            "admin_only": _settings(request).admin_only,
        }

    @router.get("/meta")
    def meta(request: Request) -> dict[str, object]:
        settings = _settings(request)
        return {
            "admin_only": settings.admin_only,
            "cookie_namespace": settings.cookie_namespace,
            "build_sha": settings.build_sha,
        }

    return router
