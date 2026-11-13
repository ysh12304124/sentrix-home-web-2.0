"""``SentrixProcess``：把 2.0 的主 API 重启到某个模型端点（T923）。

**它替代的是 1.0 的 ``bind-runtime`` 那一步**（``benchmark_orchestrator.py:2611``，注释写
"Sync .100 Sentrix gamma client to the new model (no restart)"）。1.0 只需一条 HTTP 调用；
2.0 只能重启进程，所以端口从「一次 POST」变成了「一次进程控制」。形状变了，
**目的没变**：让跑评测的那台主产品接下来用哪个模型。

**为什么返回一个值对象而不是抛异常**：1.0 把这一步的失败收进 ``gamma_sync``
（``:2613`` 的 ``try/except`` 把 ``sync_error`` 记下来），**不中断整轮评测**。
``RestartOutcome`` 把同一件事变成可断言的事实——它进 ``run.json`` 的
``phases.model_deploy.gamma_sync``。**「没换成功」与「本来就不用换」是两种结果**，
所以除了 ``ok`` 还有一个 ``restarted``：前者说「目标端点没生效」，后者说「动过进程」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: 同机共享模型经控制面准入时使用的三个环境变量。application 与 infrastructure
#: 两侧都要读；放在 domain 只放常量，不碰 os。
BENCHMARK_GATEWAY_URL_ENV = "SENTRIX_BENCHMARK_GATEWAY_URL"
BENCHMARK_GATEWAY_UPSTREAM_ENV = "SENTRIX_BENCHMARK_GATEWAY_UPSTREAM"
BENCHMARK_GATEWAY_TOKEN_ENV = "SENTRIX_BENCHMARK_GATEWAY_TOKEN"


@dataclass(frozen=True, slots=True)
class RestartOutcome:
    """一次「把 2.0 指到某个端点」的结果。落进 run.json 的 ``gamma_sync``。"""

    #: 目标端点是否已经生效。``changed=False`` 时它是 ``True``（不需要动它）。
    ok: bool
    #: 这一次是否真的重启了进程。``False`` 表示端点本来就对（或没要求端点）。
    restarted: bool
    #: 本轮要 2.0 指向的端点（``model_base_url``）。
    endpoint: str
    #: 重启前 2.0 记着的端点。没记录过时是空串。
    previous_endpoint: str = ""
    #: 新进程的 pid（``restarted=False`` 时是 ``None``）。
    pid: int | None = None
    #: 人读的原因词：``already_pointing_here`` / ``restarted`` / ``no_endpoint_requested``。
    reason: str = ""
    #: 失败原因（1.0 的 ``gamma_sync.error``）。成功时是空串。
    error: str = ""

    def to_state(self) -> dict[str, Any]:
        """run.json 里的形状。**保留 1.0 的 ``ok`` / ``error`` 两个键**——读侧与运维
        按它们判「这一步过没过」，其余是 2.0 加的诊断字段。"""
        return {
            "ok": self.ok,
            "error": self.error or None,
            "restarted": self.restarted,
            "endpoint": self.endpoint,
            "previous_endpoint": self.previous_endpoint,
            "pid": self.pid,
            "reason": self.reason,
        }


@runtime_checkable
class SentrixProcess(Protocol):
    """主 API 进程的控制面。**实现要碰子进程与信号，所以只能是 infrastructure 的活**
    （``photobench-domain-purity`` 的 forbidden 名单里有 ``os``）。"""

    def running_endpoint(self) -> str:
        """上一次由本进程启动的 2.0 用的是哪个模型端点；没记录或进程已死则空串。

        它是「要不要重启」的判据来源：**请求的端点与它相同就不动进程**——每轮评测都
        重启一次主 API 是不可接受的副作用（T923 的代价要压到最小）。
        """
        ...

    def restart(self, *, endpoint: str, timeout: float = 60) -> RestartOutcome:
        """把 2.0 停掉、以 ``endpoint`` 为 ``SENTRIX_VLM_BASE_URL`` 重新起一个，
        并等它健康。**``endpoint`` 为空时不重启**，直接回一个说明原因的结果。"""
        ...
