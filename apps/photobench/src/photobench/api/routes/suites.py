"""写侧路由：建 run / 取消 / 删除 / 评审 / 重判 / 内存画像 / judge 提示词。

1.0 ``OrchestratorHandler.do_POST``（``:7764``）与 ``do_DELETE``（``:7870``）里那几条
会改变状态的路由。**状态码照 1.0**：``start_suite`` / ``rejudge`` / ``memory-profile``
回 **202**（它们起后台线程，不是同步完成），``cancel`` / ``reviews`` / ``delete`` /
judge 提示词回 **200**。

**一条没接的路由：``POST /api/runs/{id}/resume``。** 1.0 有它（从 ``interrupted`` /
``failed`` 的 run 断点续跑）。2.0 的 resume 要重建一个 ``BenchmarkRun``（``mode=resume``
+ ``existing_scope_id``），而 2.0 的 scope 是导入时内部 ensure 的、没有「复用已存在
scope」这条路（S9b-2 删掉了 ``scope_attach``）。本切片不接它，也**不返回一个假的成功**
——路由不存在时 FastAPI 回 405/404，前端会显示「这条路没了」。登记在行为基线里，
归属留待 S9c 或后续切片裁决。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from photobench.api.deps import get_container, json_body
from photobench.api.metrics_schema import WorkStatusResponse

router = APIRouter(tags=["suites"])


@router.post("/runs", status_code=202)
async def start_suite(request: Request) -> dict[str, Any]:
    """``POST /api/runs``（1.0 ``:7854`` 分支 → ``start_suite`` ``:7306``）。"""
    payload = await json_body(request)
    return await run_in_threadpool(get_container(request).run_suites.start_suite, payload)


@router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: str) -> dict[str, Any]:
    """``POST /api/runs/{id}/cancel``（1.0 ``:7770``）。"""
    return get_container(request).run_suites.cancel_run(run_id)


@router.post("/cancel-active")
def cancel_active(request: Request) -> dict[str, Any]:
    """``POST /api/cancel-active``（1.0 ``:7767``）。"""
    container = get_container(request)
    result = container.run_suites.cancel_active_suite()
    result["analyses"] = [
        container.analysis_backend.cancel(work["id"]) for work in container.analysis_backend.work()
    ]
    return result


@router.post("/runs/{run_id}/rejudge", status_code=202)
async def start_rejudge(request: Request, run_id: str) -> dict[str, Any]:
    """``POST /api/runs/{id}/rejudge``（1.0 ``:7804``）。"""
    payload = await json_body(request)
    return await run_in_threadpool(get_container(request).run_suites.start_rejudge, run_id, payload)


@router.post("/runs/{run_id}/reviews")
async def save_reviews(request: Request, run_id: str) -> dict[str, Any]:
    """``POST /api/runs/{id}/reviews``（1.0 ``:7807``）。"""
    payload = await json_body(request)
    return await run_in_threadpool(get_container(request).run_suites.save_reviews, run_id, payload)


@router.get("/runs/{run_id}/judge-prompt")
def get_run_judge_prompt(request: Request, run_id: str) -> dict[str, Any]:
    """``GET /api/runs/{id}/judge-prompt``（1.0 ``:7737``）。"""
    return get_container(request).run_suites.get_run_judge_prompt(run_id)


@router.post("/memory-profile", status_code=202)
async def start_memory_profile(request: Request) -> dict[str, Any]:
    """``POST /api/memory-profile``（1.0 ``:7850``）。"""
    payload = await json_body(request)
    return await run_in_threadpool(get_container(request).run_suites.start_memory_profile, payload)


@router.delete("/runs/{run_id}")
def delete_run(request: Request, run_id: str) -> dict[str, Any]:
    """``DELETE /api/runs/{id}``（1.0 ``:7870``）。"""
    return get_container(request).run_suites.delete_run(run_id)


@router.get("/runs/{run_id}/recovery-plan")
def recovery_plan(request: Request, run_id: str) -> dict[str, Any]:
    return get_container(request).run_suites.recovery_plan(run_id)


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(request: Request, run_id: str) -> dict[str, Any]:
    """``POST /api/runs/{id}/resume``（1.0 ``:7768`` → ``resume_run`` ``:7488``）。

    202 与 ``POST /runs`` 同：它起后台线程，不是同步完成。

    2026-10-15 之前这条路回 501，理由是「2.0 的 scope 由导入内部 ensure，没有复用
    已存在 scope 这条路（S9b-2 删掉了 ``scope_attach``）」。那条理由随 ``mode=reuse``
    一起消失了——续跑与复用本就是同一件事的两种题目集。
    """
    payload = await json_body(request)
    return await run_in_threadpool(get_container(request).run_suites.resume_run, run_id, payload)


@router.get("/active-work", response_model=WorkStatusResponse, response_model_exclude_none=True)
def active_work(request: Request) -> dict[str, Any]:
    """Live run/auxiliary ownership; cancellation acknowledgement is not worker exit."""
    container = get_container(request)
    result = container.run_suites.work_status()
    extra = container.analysis_backend.work()
    result["work"].extend(extra)
    if extra:
        result["cleanup_status"] = "pending"
    return result


@router.post("/active-work/cleanup", response_model=WorkStatusResponse, response_model_exclude_none=True)
def refresh_cleanup(request: Request) -> dict[str, Any]:
    """Bounded recheck of previously requested cancellation, preserving failed evidence."""
    container = get_container(request)
    result = container.run_suites.refresh_cleanup()
    extra = container.analysis_backend.work()
    result["work"].extend(extra)
    if extra:
        result["cleanup_status"] = "pending"
    return result
