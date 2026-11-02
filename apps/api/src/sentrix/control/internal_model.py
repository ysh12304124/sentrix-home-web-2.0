"""控制面对内部模型入口：OpenAI 兼容，按服务身份与工作类型准入。

浏览器和任意本机进程没有令牌都不能调用；PhotoBench 令牌固定为评测级，
用户 API 令牌只能申报 ``chat`` 或 ``import``。
"""

from __future__ import annotations

import sys

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from sentrix.control.model_gate import (
    KIND_BENCHMARK,
    KIND_CHAT,
    KIND_IMPORT,
    ModelClient,
)
from sentrix.control.proxy import _HOP_BY_HOP

router = APIRouter(prefix="/internal/model/v1")

_STRIP_UPSTREAM_HEADERS = _HOP_BY_HOP | {
    "host",
    "authorization",
    "content-length",
    "x-sentrix-work",
    "x-sentrix-internal-token",
    "x-sentrix-account-id",
}


def _model_client(request: Request) -> ModelClient:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    client = request.app.state.model_client_table.resolve(token)
    if client is None:
        raise HTTPException(status_code=401, detail="model gateway token required")
    return client


def _work_kind(client: ModelClient, request: Request) -> str:
    if client.kind == "benchmark":
        # 调用方不能自报更高等级。
        return KIND_BENCHMARK
    work = request.headers.get("x-sentrix-work", KIND_CHAT).strip().lower()
    if work not in {KIND_CHAT, KIND_IMPORT}:
        raise HTTPException(status_code=400, detail="invalid work kind")
    return work


def _upstream(request: Request, suffix: str) -> str:
    settings = request.app.state.control_settings
    return f"{settings.model_upstream_url.rstrip('/')}/{suffix.lstrip('/')}"


def _props_upstream(request: Request) -> str:
    settings = request.app.state.control_settings
    base = settings.model_upstream_url.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    return f"{root}/props"


def _upstream_headers(request: Request) -> dict[str, str]:
    settings = request.app.state.control_settings
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _STRIP_UPSTREAM_HEADERS
    }
    if settings.model_upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.model_upstream_api_key}"
    return headers


@router.get("/models")
async def models(request: Request) -> Response:
    _model_client(request)
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream = await client.get(_upstream(request, "models"), headers=_upstream_headers(request))
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"model upstream unreachable: {error}") from error
    if upstream.is_success:
        request.app.state.model_gate.mark_ok()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@router.get("/props")
async def props(request: Request) -> Response:
    _model_client(request)
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream = await client.get(
            _props_upstream(request), headers=_upstream_headers(request)
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"model upstream unreachable: {error}") from error
    if upstream.is_success:
        request.app.state.model_gate.mark_ok()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@router.get("/meta")
def meta(request: Request) -> dict[str, object]:
    _model_client(request)
    return request.app.state.model_gate.describe()


@router.post("/chat/completions")
async def chat_completions(request: Request) -> StreamingResponse:
    client = _model_client(request)
    kind = _work_kind(client, request)
    gate = request.app.state.model_gate
    http: httpx.AsyncClient = request.app.state.http_client

    lease = gate.lease(kind, client.user_id)
    await lease.__aenter__()
    try:
        upstream_request = http.build_request(
            "POST",
            _upstream(request, "chat/completions"),
            headers=_upstream_headers(request),
            content=request.stream(),
        )
        upstream = await http.send(upstream_request, stream=True)
    except BaseException:
        await lease.__aexit__(*sys.exc_info())
        raise
    gate.mark_ok()

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _HOP_BY_HOP | {"content-length"}
    }

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await lease.__aexit__(None, None, None)

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
