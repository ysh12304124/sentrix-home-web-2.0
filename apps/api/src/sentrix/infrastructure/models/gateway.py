"""通过控制面共享模型入口调用主模型时的统一附头。

体验实例把 ``SENTRIX_VLM_BASE_URL`` 指向控制面 ``/internal/model/v1``，并设置
``SENTRIX_MODEL_GATEWAY_TOKEN``。旧 8191/PhotoBench 不设令牌，这里全部退化为
无操作的直连——现有行为不变。

工作类型（问答 / 导入）来自 ``sentrix.runtime.work``；导入线程由 ``import_task``
显式带过去，不能指望线程池自动继承 contextvars。
"""

from __future__ import annotations

import os

from sentrix.runtime.work import current_work


def gateway_configured() -> bool:
    return bool(os.getenv("SENTRIX_MODEL_GATEWAY_TOKEN", "").strip())


def model_auth_headers() -> dict[str, str]:
    token = os.getenv("SENTRIX_MODEL_GATEWAY_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "X-Sentrix-Work": current_work()}


def props_url(base_url: str) -> str:
    """模型上下文探测的 ``/props`` 地址。

    控制面入口把 ``/props`` 放在 ``/internal/model/v1/props`` 下；直连 llama.cpp 时
    它挂在根上（不带 ``/v1``）。两条路径不能混用。
    """
    base = base_url.rstrip("/")
    if gateway_configured():
        return f"{base}/props"
    root = base[:-3] if base.endswith("/v1") else base
    return f"{root}/props"


def json_headers() -> dict[str, str]:
    headers = model_auth_headers()
    headers["Content-Type"] = "application/json"
    return headers
