"""run 的**只读**路由（1.0 ``do_GET`` 里 ``/api/runs*`` 那几条，``:7648-7740``）。

写侧（``POST /api/runs`` 建 run、``/cancel``、``/resume``、``/rejudge``、
``POST /reviews``、``DELETE /api/runs/{id}``）不在这里——它们要么属于 S9b-2 的
run-execution，要么属于 S9b-3 的 judge。

**分页参数为什么声明成 ``str`` 再自己转。** 1.0 是这样做的，错的时候回
``400 {"error": "page and page_size must be integers"}``；用 FastAPI 的 ``int`` 声明
会变成 ``422 {"detail": ...}``——前端读的是前一种。形状要一致，所以照 1.0 转。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from photobench.api.deps import get_container
from photobench.api.metrics_schema import RunItemResponse, RunItemsResponse, RunMetricsResponse
from photobench.domain.errors import InvalidRequestError

router = APIRouter(tags=["runs"])


def _pagination(raw_page: str, raw_page_size: str) -> tuple[int, int]:
    try:
        return max(1, int(raw_page)), max(1, min(100, int(raw_page_size)))
    except ValueError as exc:
        raise InvalidRequestError("page and page_size must be integers") from exc


@router.get("/runs")
def list_runs(request: Request, page: str = "1", page_size: str = "20") -> dict[str, Any]:
    """1.0 ``:7648``。"""
    page_value, page_size_value = _pagination(page, page_size)
    return get_container(request).runs.list_runs(page=page_value, page_size=page_size_value)


@router.get("/runs/{run_id}", response_model=RunMetricsResponse, response_model_exclude_unset=True)
def get_run(request: Request, run_id: str, include_history: bool = True) -> dict[str, Any]:
    """1.0 ``:7739``。run 不存在时 ``KeyError`` → 404（handler 在 ``api/errors.py``）。"""
    if include_history:
        return get_container(request).runs.get_run(run_id)
    return get_container(request).runs.get_run(run_id, include_history=False)


@router.get("/runs/{run_id}/items", response_model=RunItemsResponse, response_model_exclude_unset=True)
def get_run_items(
    request: Request,
    run_id: str,
    page: str = "1",
    page_size: str = "20",
    search: str = "",
    score: str = "",
    task_type: str = "",
    question_type: str = "",
    tag: str = "",
    agent_status: str = "",
    angle: str = "",
    difficulty: str = "",
    answerability: str = "",
    primary: str = "",
) -> dict[str, Any]:
    """1.0 ``:7655``。筛选项逐个透传，规则在 ``ReadRuns.get_run_items``。"""
    try:
        page_value, page_size_value = int(page), int(page_size)
    except ValueError as exc:
        raise InvalidRequestError("page and page_size must be integers") from exc
    return get_container(request).runs.get_run_items(
        run_id,
        page=page_value,
        page_size=page_size_value,
        search=search,
        score=score,
        task_type=task_type,
        question_type=question_type,
        tag=tag,
        agent_status=agent_status,
        angle=angle,
        difficulty=difficulty,
        answerability=answerability,
        primary=primary,
    )


@router.get(
    "/runs/{run_id}/items/{item_index}",
    response_model=RunItemResponse,
    response_model_exclude_unset=True,
)
def get_run_item(request: Request, run_id: str, item_index: str) -> dict[str, Any]:
    """1.0 ``:7735``。"""
    try:
        index = int(item_index)
    except ValueError as exc:
        raise InvalidRequestError(f"invalid item index: {item_index}") from exc
    return get_container(request).runs.get_run_item(run_id, index)


@router.get("/runs/{run_id}/reviews")
def get_reviews(request: Request, run_id: str) -> dict[str, Any]:
    """1.0 ``:7730``。写侧（``POST``）属 S9b-3。"""
    return get_container(request).runs.get_reviews(run_id)
