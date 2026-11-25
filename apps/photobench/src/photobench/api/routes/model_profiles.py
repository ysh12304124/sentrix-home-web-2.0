"""``/api/profiles`` 与 ``/api/current-model``（1.0 ``do_POST`` ``:7826`` / ``:7844``）。

两条都是**查询**：问 Manager 有哪些 profile、问端点现在真的在跑哪个模型。起停模型
（1.0 ``_phase_model_deploy``）属 S9b-2 的 run-execution 主链，不在这里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from photobench.api.deps import get_container, json_body

router = APIRouter(tags=["model-profiles"])


@router.post("/profiles")
async def list_profiles(request: Request) -> dict[str, Any]:
    """1.0 ``:7826``。"""
    payload = await json_body(request)
    return get_container(request).model_profiles.query_profiles(payload)


@router.get("/current-model")
def current_model_get() -> JSONResponse:
    """1.0 ``:7530``：GET 这一条要明说「请用 POST」，而不是让 405 用 FastAPI 的形状回。"""
    return JSONResponse(status_code=405, content={"error": "current-model requires POST"})


@router.post("/current-model")
async def current_model(request: Request) -> dict[str, Any]:
    """1.0 ``:7844``。"""
    payload = await json_body(request)
    return get_container(request).model_profiles.current_model(payload)


@router.get("/current-deployed-model")
def current_deployed_model(request: Request) -> dict[str, Any]:
    """Query the product's actual model endpoint without changing its deployment."""
    container = get_container(request)
    base, model = container.run_suites.interactive_models.deployed_endpoint()
    return container.model_profiles.current_model({"model_base_url": base, "model": model})


@router.post("/test-model")
async def test_model(request: Request) -> dict[str, Any]:
    """1.0 ``:7965``：「测试模型端点」按钮。

    2.0 的换模型语义是「重启 2.0」（S9c · 用户裁决③），但那条只影响 run-execution 的
    ``model_deploy`` 与前端交互——**探活本身没有副作用**，所以 2.0 版与 1.0 版逐行
    相同，不往这里加重启语义。
    """
    payload = await json_body(request)
    return get_container(request).model_profiles.test_model(payload)
