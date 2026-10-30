"""PhotoBench 的模型运行时端口：推理 / 生命周期 / 遥测。

对应 1.0 ``backend/runtime_providers.py`` 里的三个基类（``InferenceProvider`` /
``LifecycleProvider`` / ``TelemetryProvider``）与 ``RuntimeProviders`` 数据类。
1.0 把「接口」与「实现」写在同一个文件里；2.0 按分层契约拆开——**接口在这里，
实现在 ``photobench.infrastructure.runtime.providers``**。理由是机械的：实现要起
子进程 / 读 ``/proc`` / 打 HTTP，而 ``domain-purity`` 契约禁止 domain 碰这些。

**形状与 1.0 的三处差异，每一处都是分层逼出来的，不是口味。**

1. **协议不是 ABC。** 1.0 的基类会用 ``raise NotImplementedError`` 兜底；
   2.0 用 ``Protocol``（结构化类型），实现类不继承它。这与 ``apps/api`` 的
   ``domain/media/ports.py`` 是同一条口径。
2. **不泄漏 httpx 的类型。** 1.0 的 ``chat`` 返回 ``httpx.Response``、``chat_stream``
   返回 httpx 的 stream 上下文管理器。那两样东西在 domain 里是非法的（``httpx`` 在
   ``domain-purity`` 的 forbidden 名单上），所以端口把它们收成
   「解析后的 JSON 对象」与「逐行的迭代器」。**非 2xx 仍然抛异常**——由适配器在返回前
   ``raise_for_status()``，调用方看到的行为与 1.0 一致（1.0 的调用方自己调它）。
3. **``token_count`` 的默认值没了。** 1.0 的基类给了一个返回 ``None`` 的实现，供没有
   Manager 的端点用；协议不能带实现，所以这条默认值落在唯一的实现
   ``OpenAICompatibleInferenceProvider`` 上（它在 ``manager_url`` 为空时返回 ``None``）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RunRuntimeSnapshot:
    """Immutable connection facts; JSON storage also prevents nested mutation."""

    connection_json: str
    build_revision: str = "development"

    def connection(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.connection_json)
        return value


# ``runtime_checkable`` 是为了让「假件能替真件」这件事**可断言**而不是口头承诺：
# 用例里 ``isinstance(Fake, TelemetryProvider)`` 一旦为假，就说明端口表面被改动过
# （方法被改名或删除），而那种改动在没有生产调用者的切片里靠别的办法看不出来。
# 代价是 ``isinstance`` 只按方法名判、不看签名——这一层由 mypy strict 管。


@runtime_checkable
class InferenceProvider(Protocol):
    """一次推理调用所需的最小面：探活 / 列模型 / 对话 / 流式对话 / 数 token / 自述能力。

    推理是**必需**的（1.0 的模块注释：Lifecycle 与 telemetry 可选，是为了让一个普通的
    OpenAI 兼容端点——llama.cpp / Ollama / vLLM——不用装 Sentrix vLLM Manager 也能评测）。
    """

    def health(self) -> dict[str, Any]: ...

    def list_models(self) -> dict[str, Any]: ...

    def chat(self, payload: Mapping[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def chat_stream(self, payload: Mapping[str, Any], *, timeout: float | None = None) -> Iterator[str]: ...

    def token_count(
        self, messages: Sequence[Mapping[str, Any]], *, timeout: float = 15
    ) -> dict[str, Any] | None: ...

    def capabilities(self) -> dict[str, Any]: ...

    def max_concurrency(self) -> int | None:
        """这个端点**同时能服务几个请求**。读不到返回 ``None``。

        与 ``health`` 是两件事：健康只回答「能不能用」，这一格回答「能用几个」，而
        **它是 QA 并发唯一诚实的来源**——发超过槽位数的请求，服务端只会排队或拒绝。

        1.0 ``benchmark_orchestrator.py:3667-3688`` 的 ``_runtime_max_concurrency``：
        ninfer 问 ``/stats`` 的 ``capacity.max_concurrency``，llama.cpp 问 ``/props`` 的
        ``total_slots``，托管 vLLM 问 manager ``/state`` 的 ``max_num_seqs``。

        2.0 原先没有这一格，于是 ``_resolve_qa_concurrency`` 只能退回 1（串行）——
        实测 qa_eval 6570s，而 1.0 同一轮是 2876s。
        """
        ...


@runtime_checkable
class LifecycleProvider(Protocol):
    """模型进程的生命周期（归 vLLM Manager 管）。没有 Manager 时是 ``not_applicable``。"""

    def profiles(self) -> dict[str, Any]: ...

    def start(self, payload: Mapping[str, Any], *, timeout: float = 120) -> dict[str, Any]: ...

    def stop(self, payload: Mapping[str, Any] | None = None, *, timeout: float = 90) -> dict[str, Any]: ...

    def state(self) -> dict[str, Any]: ...


@runtime_checkable
class TelemetryProvider(Protocol):
    """本机遥测。**每一个方法都返回一个带 ``status`` 的字典，一个都不抛。**

    这是 1.0 的口径，照搬：遥测是增强而不是评测的前置条件，探不到就报
    ``unavailable`` / ``not_applicable`` 并写明 ``reason``，**绝不用 0 顶替缺失值**
    （0 是一个合法的测量结果，缺失不是）。
    """

    def gpu_stats(self) -> dict[str, Any]: ...

    def process_memory(self) -> dict[str, Any]: ...

    def kv_cache(self) -> dict[str, Any]: ...

    def system_memory(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RuntimeProviders:
    """一次评测所用的三件套。1.0 是 ``@dataclass(frozen=True)`` 无 ``slots``，形状一致。

    ``source`` 是 S9b-3 加的：``select_runtime_providers`` 除了挑出遥测，还给出
    「这是哪一路」（``jetson_local_pss`` / ``apple_unified`` / ``host_nvidia_smi`` /
    ``vllm_manager`` / ``unavailable``）。它要落进 ``run.json`` 的 ``telemetry_source``
    ——**不同来源的数字口径不同**（UMA 的 PSS 不是显存），读侧与运维靠这个字段判断
    曲线能不能横向比。默认空串，因为 ``create_runtime_providers`` 那条 Manager 路径
    不经过选择器。
    """

    inference: InferenceProvider
    lifecycle: LifecycleProvider
    telemetry: TelemetryProvider
    source: str = ""


@runtime_checkable
class OwnedTelemetryWork(Protocol):
    """Optional diagnostic capability; the four TelemetryProvider methods remain unchanged."""

    def pending_work(self) -> list[str]:
        """Return live owned probe identifiers, not a stale stop snapshot."""
        ...


@runtime_checkable
class ManagedTelemetryWork(Protocol):
    """Optional sampler-owned resources; core telemetry methods stay unchanged."""

    def start_sampling(self) -> None: ...

    def stop_sampling(self) -> None: ...
