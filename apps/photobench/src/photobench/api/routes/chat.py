"""Chat session transport; asynchronous work is owned by the application service."""

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from photobench.api.deps import get_container
from photobench.api.interactive_schema import ChatCreateRequest, ChatMessageRequest, ChatSessionResponse

router = APIRouter(tags=["chat"])


@router.get("/chat/sessions")
def list_chats(request: Request) -> dict[str, Any]:
    return get_container(request).chat.list_chats()


@router.post(
    "/chat/sessions", status_code=202, response_model=ChatSessionResponse, response_model_exclude_unset=True
)
async def create_chat(request: Request, payload: ChatCreateRequest) -> dict[str, Any]:
    return await run_in_threadpool(
        get_container(request).chat.create_chat, payload.model_dump(exclude_defaults=True)
    )


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionResponse, response_model_exclude_unset=True
)
def get_chat(request: Request, session_id: str, include_items: bool = False) -> dict[str, Any]:
    return get_container(request).chat.get_chat(session_id, include_items=include_items)


@router.get("/chat/sessions/{session_id}/turns/{index}")
def get_chat_turn(request: Request, session_id: str, index: int) -> dict[str, Any]:
    return get_container(request).chat.get_chat_turn(session_id, index)


@router.get("/chat/sessions/{session_id}/turns/{index}/trace")
def get_chat_trace(
    request: Request,
    session_id: str,
    index: int,
    revision: str = "",
    turn: int = 0,
    kind: str = "calls",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=50),
) -> dict[str, Any]:
    return get_container(request).chat.get_chat_trace(
        session_id, index, revision=revision, turn=turn, kind=kind, offset=offset, limit=limit
    )


@router.get("/chat/sessions/{session_id}/turns/{index}/trace/{node_id}")
def get_chat_trace_content(
    request: Request,
    session_id: str,
    index: int,
    node_id: str,
    revision: str = "",
    field: str = "",
    view: Literal["content", "timing"] = "content",
    offset: int = Query(0, ge=0),
    limit: int = Query(32768, ge=1, le=65536),
) -> dict[str, Any]:
    return get_container(request).chat.get_chat_trace(
        session_id,
        index,
        revision=revision,
        node_id=node_id,
        field=field,
        offset=offset,
        limit=limit,
        view=view,
    )


@router.get("/chat/sessions/{session_id}/turns/{index}/content")
def get_chat_turn_content(
    request: Request,
    session_id: str,
    index: int,
    path: str,
    revision: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(32768, ge=1, le=65536),
) -> dict[str, Any]:
    keys: list[str | int] = [int(p) if p.isdigit() else p for p in path.split(".") if p]
    return get_container(request).chat.get_chat_trace(
        session_id, index, revision=revision, path=keys, offset=offset, limit=limit
    )


@router.post(
    "/chat/sessions/{session_id}/turns",
    status_code=202,
    response_model=ChatSessionResponse,
    response_model_exclude_unset=True,
)
async def send_chat(request: Request, session_id: str, payload: ChatMessageRequest) -> dict[str, Any]:
    return await run_in_threadpool(get_container(request).chat.send_chat, session_id, payload.model_dump())


@router.post("/chat/sessions/{session_id}/cancel")
def cancel_chat(request: Request, session_id: str) -> dict[str, Any]:
    return get_container(request).chat.cancel_chat(session_id)


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_chat(request: Request, session_id: str) -> Response:
    get_container(request).chat.delete_chat(session_id)
    return Response(status_code=204)
