"""从 app.state 取依赖。路由只从这里拿东西，不自己构造。"""

from __future__ import annotations

import secrets
from typing import cast

from fastapi import HTTPException, Request

from sentrix.bootstrap.container import Container

INTERNAL_TOKEN_HEADER = "x-sentrix-internal-token"

# 体验账号不能从浏览器触达的能力。控制网关已经按角色拒绝一次；用户 API 在
# experience 模式下再拒绝一次，防止绕过网关直连回环端口。
_EXPERIENCE_DENIED: tuple[tuple[str | None, str], ...] = (
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


def get_container(request: Request) -> Container:
    # app.state 是 Starlette 的动态属性，mypy 只看得见 Any；装进去的就是 Container。
    return cast(Container, request.app.state.container)


def internal_call_guard(request: Request) -> None:
    """体验实例只接受控制面转发的请求。

    旧 8191/PhotoBench 不设这两个开关，行为保持不变。
    """
    settings = request.app.state.settings
    if settings.require_internal_token:
        expected = settings.internal_token
        provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="internal token required")
    if settings.experience_mode and _denied(request.method, request.url.path):
        raise HTTPException(status_code=403, detail="route not available in experience mode")


def _denied(method: str, path: str) -> bool:
    upper = method.upper()
    for allowed_method, prefix in _EXPERIENCE_DENIED:
        if allowed_method is not None and allowed_method != upper:
            continue
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False
