"""健康检查：轻量存活探针与完整诊断分开。

1.0 的教训（2026-10-16）：``/api/health`` 同时查数据库与嵌入侧车，热态 7–11 ms；
看门狗用 6 s 超时轮询它，重负载下仍可能把一次慢探测判成「进程死了」并重启，
SIGTERM 打断正在跑的评测 run。1.0 因此拆成 ``/api/health``（0.15–0.21 ms）与
``/api/diagnostics``（完整诊断）。

2.0 保留 ``/api/v1/health`` 的既有响应形状（已有消费方与契约测试），同时新增：

- ``/api/v1/health/live``：只证明事件循环还能回包，不碰数据库、不碰嵌入侧车；
  看门狗/负载均衡应轮询这个。
- ``/api/v1/diagnostics``：完整诊断（数据库 + 嵌入侧车 + 降级原因）。
"""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from sentrix import __version__


class DiagnosticsResponse(BaseModel):
    status: str
    version: str
    build_revision: str
    database: str
    embeddings: str
    embeddings_error: str | None = None
    asr: str
    asr_error: str | None = None
    runtime_configuration: dict[str, Any]


router = APIRouter(tags=["health"])


@router.get("/health/live")
async def health_live(request: Request) -> dict[str, str]:
    """轻量存活探针：不访问任何依赖，也不进线程池。"""
    return {
        "status": "ok",
        "version": __version__,
        "build_revision": request.app.state.container.build_revision,
    }


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """进程与它依赖的**当前**可用性。

    **为什么嵌入器要进来。** 2026-10-10 那轮 PhotoBench 评测里，WeMM 嵌入侧车全程
    不在（354/370 次检索静默降级成 ``backend=none``），而本端点一直回
    ``{"status":"ok"}`` —— 整轮数据作废却没有任何一处报错。数据库好、嵌入器坏，
    不是 ``ok``。

    **为什么是 ``degraded`` 而不是 ``error``。** 嵌入是**可选**依赖：侧车不在时
    检索退回词法/元数据/实体三路，进程照常服务。两档分开才能让「还能用但少了东西」
    与「用不了」在监控上可区分；把两者压成一个失败码，运维就没法只对后者告警。

    探测走 ``EmbeddingHealthProbe``，它自带 10 秒 TTL（``models/wemm.py``），
    所以这个端点被高频轮询时不会每次都打一次侧车。

    **高频探活请用 ``/health/live``。** 本端点是完整诊断，重负载下不应作为
    看门狗的 1 分钟级探针；保留它是为了不破坏既有消费方。
    """
    return _diagnostics(request)


@router.get("/diagnostics", response_model=DiagnosticsResponse, response_model_exclude_none=True)
def diagnostics(request: Request) -> dict[str, Any]:
    """完整诊断：数据库 + 嵌入侧车 + 降级原因。与 ``/health`` 同一份实现。"""
    return {
        **_diagnostics(request),
        "runtime_configuration": request.app.state.container.settings.runtime_configuration(),
    }


def _diagnostics(request: Request) -> dict[str, str]:
    container = request.app.state.container
    database_ok = container.database_healthy()
    embeddings_ok = container.embedding_health.available
    asr_ok = container.audio_transcriber.available
    payload = {
        "status": "ok" if (database_ok and embeddings_ok and asr_ok) else "degraded",
        "version": __version__,
        "build_revision": container.build_revision,
        "database": "ok" if database_ok else "error",
        "embeddings": "ok" if embeddings_ok else "unavailable",
        "asr": "ok" if asr_ok else "unavailable",
    }
    if not embeddings_ok:
        # **带上原因。** 只有「不可用」三个字的话，排查要从日志里捞——
        # 而这条路径的整个教训就是「降级必须自己走到人眼前」。
        payload["embeddings_error"] = container.embedding_health.load_error or "sidecar_unreachable"
    if not asr_ok:
        payload["asr_error"] = container.audio_transcriber.load_error or "sidecar_unreachable"
    return payload
