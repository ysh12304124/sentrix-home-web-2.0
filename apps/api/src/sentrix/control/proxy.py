"""用户路由表与流式反向转发。

目标 API 只由控制面按登录账号决定；浏览器提供的端口、数据根或身份头一律
不参与路由。请求体、响应体和 SSE 都按块转发，不整包读入内存。
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

_STRIP_REQUEST_HEADERS = _HOP_BY_HOP | {
    "host",
    "cookie",
    "authorization",
    "content-length",
    "x-sentrix-internal-token",
    "x-sentrix-account-id",
    "x-sentrix-user",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}

_STRIP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length", "set-cookie"}

# 普通体验账号不能从浏览器触达的能力。角色授权在控制面完成；用户 API 的
# experience 模式还会再拒绝一次，避免绕过网关直连回环端口。
DENIED_USER_ROUTES: tuple[tuple[str | None, str], ...] = (
    ("POST", "/api/v1/imports"),
    ("POST", "/api/v1/pipeline/process"),
    ("POST", "/api/v1/media/import"),
    ("POST", "/api/v1/videos"),
    ("POST", "/api/v1/maintenance"),
    (None, "/api/v1/diagnostics"),
    (None, "/api/v1/memory-graph"),
    (None, "/api/v1/search"),
    (None, "/api/v1/knowledge"),
    (None, "/api/v1/entity-groups"),
    (None, "/api/v1/entities"),
    ("POST", "/api/v1/facts"),
    ("POST", "/api/v1/family-graph/runs"),
    ("POST", "/api/v1/family-graph/scopes/merge"),
)

PROXY_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class RouteTableError(ValueError):
    pass


@dataclass(frozen=True)
class UserRoute:
    account_id: str
    api_url: str
    api_token: str


def denied_for_user(method: str, path: str) -> bool:
    upper = method.upper()
    for allowed_method, prefix in DENIED_USER_ROUTES:
        if allowed_method is not None and allowed_method != upper:
            continue
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _require_loopback(account_id: str, url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or not parts.port:
        raise RouteTableError(f"user {account_id}: api url must be http://host:port")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError as error:
        raise RouteTableError(f"user {account_id}: api host must be a literal IP") from error
    if not address.is_loopback:
        raise RouteTableError(f"user {account_id}: api host must be loopback")


class RouteTable:
    """从 0600 的 users 文件加载账号 → 本机 API 映射。"""

    def __init__(self, routes: dict[str, UserRoute], error: str = "") -> None:
        self._routes = routes
        self._error = error

    @classmethod
    def load(cls, path: Path) -> RouteTable:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return cls({}, f"route table unavailable: {error}")
        entries = document.get("users") if isinstance(document, dict) else None
        if not isinstance(entries, dict):
            return cls({}, "route table unavailable: missing users object")
        routes: dict[str, UserRoute] = {}
        for account_id, value in entries.items():
            if not isinstance(value, dict):
                return cls({}, f"route table invalid for {account_id}")
            api_url = str(value.get("api_url") or "")
            api_token = str(value.get("api_token") or "")
            try:
                _require_loopback(account_id, api_url)
            except RouteTableError as error:
                return cls({}, str(error))
            if not api_token:
                return cls({}, f"user {account_id}: api token missing")
            routes[str(account_id)] = UserRoute(str(account_id), api_url.rstrip("/"), api_token)
        return cls(routes)

    def get(self, account_id: str) -> UserRoute | None:
        return self._routes.get(account_id)

    def ids(self) -> list[str]:
        return list(self._routes)

    def __len__(self) -> int:
        return len(self._routes)

    @property
    def error(self) -> str:
        return self._error

    @property
    def ready(self) -> bool:
        return not self._error


async def forward(
    request: Request,
    route: UserRoute,
    client: httpx.AsyncClient,
    *,
    request_id: str | None = None,
) -> StreamingResponse:
    rid = request_id or f"req_{uuid.uuid4().hex[:16]}"
    target = route.api_url + request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = [
        (name, value)
        for name, value in request.headers.items()
        if name.lower() not in _STRIP_REQUEST_HEADERS
    ]
    headers.append(("x-sentrix-internal-token", route.api_token))
    headers.append(("x-sentrix-account-id", route.account_id))
    headers.append(("x-request-id", rid))

    body = None if request.method.upper() in {"GET", "HEAD", "OPTIONS"} else request.stream()
    upstream_request = client.build_request(request.method, target, headers=headers, content=body)
    upstream = await client.send(upstream_request, stream=True)

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _STRIP_RESPONSE_HEADERS
    }
    response_headers["x-request-id"] = rid

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
        background=BackgroundTask(upstream.aclose),
    )
