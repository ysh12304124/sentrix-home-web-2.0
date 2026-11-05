"""GPU 采样线程：1.0 ``GpuSampler``（``:1688``）的照搬。

**它为什么在 application 而不是 infrastructure。** 它自己没有 IO——所有系统调用都在
``TelemetryProvider`` 端口后面（那个端口由 ``infrastructure.runtime.providers`` 实现）。
它做的是一件用例层的事：**在 run 进行期间按固定间隔把遥测拉下来并交给回调**。
``photobench-application-purity`` 禁的是 import infrastructure，不是禁线程。

**它是 ``TelemetryProvider.gpu_stats`` / ``process_memory`` / ``system_memory`` 三个
方法的唯一生产调用者**——S9a 落地这三个方法时没有任何调用者，S9b-1 复查时它们仍然
是 method-reachability 的盲区（因为 provider 自己的实现里有同名 ``self.*`` 调用，
门禁判成「已接线」）。这里接上之后，改坏这三个方法名会让 ``tests/test_gpu_sampler.py``
红。

**样本的字段映射逐行照搬，不做表驱动**：那 40 多行里混着三种东西——同名直取、
改名映射（``processes`` → ``model_processes``）、以及 ``vllm_metrics`` /
``memory_profile`` 两层嵌套的展开，还有 `or []` 与 `or sample[...]` 两种兜底。
抽成一张表会把这些差异压成一个形状，而它们恰好是差分对照要逐字段比对的东西。

**三处与 1.0 的差别，都是「2.0 没有了那个前提」：**

1. ``hasattr(self.provider, "system_memory")`` 的那层探测去掉——2.0 的
   ``TelemetryProvider`` 协议里它一定在（S9a 的端口定义），留一个 hasattr 只会让
   「协议少了一个方法」这件事在运行时静默通过。
2. ``aggregate()`` 在 S9b-3 补上：它**委托给纯函数**
   ``domain.runs.analysis.aggregate_gpu_samples``（1.0 ``:1785``），自己只负责把
   ``self.samples`` 递过去。留在采样器上的理由只有一个——调用方
   （``_phase_gpu_metrics``）手上就是采样器对象，让它去 import 一个 domain 函数会
   把「采样的状态」与「聚合的算法」的边界画反。
3. ``samples`` 累积在内存里（1.0 亦然），聚合与实时视图都读它。
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

from photobench.domain.runs.analysis import aggregate_gpu_samples, validated_telemetry_sample
from photobench.domain.runtime.metrics import RESOURCE_TELEMETRY_VERSION, finite_number
from photobench.domain.runtime.ports import ManagedTelemetryWork, OwnedTelemetryWork, TelemetryProvider

#: 采样行会带上相位标签，读侧就不必去 run.json 里反查「这条样本属于哪一段」。
#: 1.0 ``_persist_gpu_sample``（``:2113``）的那张表，逐字照搬（含中文标签）。
PHASE_LABELS: dict[str, str] = {
    "model_deploy": "模型加载",
    "scope_setup": "创建相册",
    "scope_attach": "绑定相册",
    "identity_seed": "身份预置",
    "photo_import": "数据导入",
    "pipeline_processing": "相册流水线预处理",
    "qa_eval": "QA 测评",
    "gpu_metrics": "资源指标收尾",
    "aggregate": "结果汇总",
    "unassigned": "未分配阶段",
}


class GpuSampler:
    """按间隔轮询设备与被管模型的内存；一个采样点一次回调。

    ``on_sample`` 的异常**被吞掉**（1.0 ``:1767`` 的 ``try/except``）：采样是增强，
    一次写盘失败不该让整条采样线程死掉——那会让后面的曲线整个缺一段，而缺一段的
    原因（磁盘满）与被测对象毫无关系。
    """

    def __init__(
        self,
        provider: TelemetryProvider,
        interval: float = 1.0,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        on_aggregate: Callable[[dict[str, Any]], None] | None = None,
        publish_interval: float = 2.0,
    ) -> None:
        if (
            not math.isfinite(interval)
            or interval <= 0
            or not math.isfinite(publish_interval)
            or publish_interval <= 0
        ):
            raise ValueError("sampling and publishing intervals must be positive and finite")
        self.provider = provider
        self.interval = interval
        self.on_sample = on_sample
        self.on_aggregate = on_aggregate
        self.publish_interval = publish_interval
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_lock = threading.Lock()
        self._samples_lock = threading.Lock()
        self._publisher: threading.Thread | None = None
        self._previous_callback_seconds = 0.0
        self._callback_error: str | None = None
        self._callback_failures = 0
        self._publisher_failures = 0
        self._last_callback_failure: str | None = None
        self._last_publisher_failure: str | None = None
        self._collection_failures = 0
        self._last_collection_failure: str | None = None
        self._stop_pending: list[str] = []
        self._missed_intervals = 0
        self._source = ""

    def start(self) -> None:
        if any(thread and thread.is_alive() for thread in (self._thread, self._publisher)):
            raise RuntimeError("sampler is already running")
        self._stop.clear()
        self.samples = []
        self._source = ""
        self._previous_callback_seconds = 0.0
        self._callback_error = self._last_callback_failure = self._last_publisher_failure = None
        self._last_collection_failure = None
        self._callback_failures = self._publisher_failures = self._collection_failures = 0
        self._missed_intervals = 0
        self._stop_pending = []
        if isinstance(self.provider, ManagedTelemetryWork):
            self.provider.start_sampling()
        self._thread = threading.Thread(target=self._run, daemon=True, name="gpu-sampler")
        self._thread.start()
        if self.on_aggregate is not None:
            self._publisher = threading.Thread(target=self._publish_loop, daemon=True, name="gpu-publisher")
            self._publisher.start()

    def stop(self) -> None:
        self._stop.set()
        deadline = time.monotonic() + 10
        for thread in (self._thread, self._publisher):
            if thread:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        self._stop_pending = [
            thread.name for thread in (self._thread, self._publisher) if thread and thread.is_alive()
        ]

    def pending_work(self) -> list[str]:
        threads = [thread.name for thread in (self._thread, self._publisher) if thread and thread.is_alive()]
        if isinstance(self.provider, OwnedTelemetryWork):
            threads.extend(self.provider.pending_work())
        return threads

    def sample_once(self, *, scheduled_at: float | None = None) -> None:
        """采一个点。

        **抽出来只为让用例不必起线程**：没有它，这个类的用例只能靠 ``sleep`` 等线程
        跑一轮——那种用例既慢，又在负载高的机器上随机红，而它要断言的东西（样本的
        字段映射）与线程毫无关系。``_run`` 与它共用同一段逻辑，不存在第二条路径。
        """

        requested = time.monotonic()
        with self._sample_lock:
            started = time.monotonic()
            self._sample_once(
                {
                    "started_at_monotonic": started,
                    "lock_wait_seconds": started - requested,
                    "schedule_lateness_seconds": max(0, started - scheduled_at)
                    if scheduled_at is not None
                    else None,
                    "missed_intervals": self._missed_intervals,
                    "previous_callback_seconds": self._previous_callback_seconds,
                    "previous_callback_error": self._callback_error,
                }
            )

    def _sample_once(self, timing: dict[str, Any]) -> None:
        def probe(role: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
            started = time.monotonic()
            try:
                return callback()
            except Exception as error:
                return {"status": "unavailable", "error": str(error)}
            finally:
                timing[f"{role}_seconds"] = time.monotonic() - started

        gpu_result = probe("device", self.provider.gpu_stats)
        gpu_observed_at = time.monotonic()
        memory_result = probe("process", self.provider.process_memory)
        system_result = probe("system", self.provider.system_memory)
        if self._stop.is_set():
            return
        data = (gpu_result.get("data") or {}) if gpu_result.get("status") == "available" else {}
        # 进程归属拿不到时**仍然保留进程与系统内存**——绝不把「没找到模型进程」
        # 变成一个看起来有意义的 0（1.0 ``:1713-1716`` 的注释）。
        process_memory = (
            (memory_result.get("data") or {}) if memory_result.get("status") == "available" else {}
        )
        system_memory = (
            (system_result.get("data") or {}) if system_result.get("status") == "available" else {}
        )
        timing["process_probe_seconds"] = process_memory.get("process_probe_seconds")
        timing["process_probe_pending"] = process_memory.get("process_probe_pending")
        if "process_probe_worker_pid" in process_memory:
            timing["process_probe_worker_pid"] = process_memory["process_probe_worker_pid"]
        timestamp = time.monotonic()
        timing.update(
            finished_at_monotonic=timestamp,
            collection_seconds=timestamp - timing["started_at_monotonic"],
        )
        for gpu in data.get("gpus") or [{"index": 0}]:
            sample = dict(gpu)
            for key in ("protocol_version", "memory_accounting"):
                if key in data:
                    sample[key] = data[key]
            for key in (
                "protocol_version",
                "product_stack_gpu_memory_mib",
                "device_gpu_memory_mib",
            ):
                if key in process_memory:
                    sample[key] = process_memory[key]
            sample["resource_missing_reasons"] = {
                **(sample.get("resource_missing_reasons") or {}),
                **(process_memory.get("resource_missing_reasons") or {}),
            }
            for key in ("cpu_utilization_pct", "cpu_observed_at_monotonic"):
                sample[key] = system_memory.get(key)
            per_gpu = process_memory.get("gpu_process_memory_by_index")
            gpu_memory = per_gpu.get(gpu.get("index"), {}) if isinstance(per_gpu, dict) else process_memory

            if sample.get("cpu_temperature_c") is None and data.get("cpu_temperature_c") is not None:
                sample["cpu_temperature_c"] = data["cpu_temperature_c"]
            sample["_t"] = timestamp
            if gpu_result.get("status") == "available":
                sample.setdefault("device_observed_at_monotonic", gpu_observed_at)
                sample.setdefault("device_observation_ttl_seconds", 3.0)
            sample["observed_at_epoch"] = time.time()
            sample["metrics_contract_version"] = 2
            sample["sampling_diagnostics"] = dict(timing)
            for key in ("memory_observation_version", "memory_observations", "memory_dependencies"):
                if key in process_memory:
                    sample[key] = process_memory[key]
            if sample.get("protocol_version") == RESOURCE_TELEMETRY_VERSION:
                sample["memory_observation_version"] = 1
            if sample.get("memory_observation_version") == 1:
                sample["memory_observations"] = dict(sample.get("memory_observations") or {})
                sample["memory_observations"]["system"] = system_memory.get("system_observation") or {}
                sample["memory_dependencies"] = dict(sample.get("memory_dependencies") or {})
                for key in (
                    "system_memory_used_mib",
                    "system_memory_total_mib",
                    "system_memory_available_mib",
                ):
                    sample["memory_dependencies"][key] = ["system"]
            if (
                sample.get("memory_observation_version") == 1
                and "device_gpu_memory_mib" not in sample["memory_dependencies"]
            ):
                sample["memory_observations"]["device_gpu"] = {
                    "source": gpu_result.get("source"),
                    "status": gpu_result.get("status"),
                    "observed_at_monotonic": sample.get("device_observed_at_monotonic"),
                    "finished_at_monotonic": gpu_observed_at,
                    "ttl_seconds": 3.0,
                }
                sample["memory_dependencies"]["device_gpu_memory_mib"] = ["device_gpu"]
            sample["source_status"] = {
                "device": gpu_result.get("status"),
                "process": memory_result.get("status"),
                "system": system_result.get("status"),
            }
            sample["source_diagnostics"] = {
                role: {key: result[key] for key in ("reason", "error", "source") if key in result}
                for role, result in (
                    ("device", gpu_result),
                    ("process", memory_result),
                    ("system", system_result),
                )
            }
            for key in (
                "memory_accounting",
                "memory_complete",
                "model_process_footprint_mib",
                "model_process_mapped_weights_mib",
                "product_stack_footprint_mib",
                "product_stack_mapped_weights_mib",
            ):
                sample[key] = process_memory.get(key, sample.get(key))
            sample["model_process_memory_used_mib"] = process_memory.get("process_memory_used_mib")
            sample["model_process_system_memory_used_mib"] = process_memory.get(
                "model_process_system_memory_used_mib"
            )
            # 进程内存与GPU分配分别传递；多卡GPU按当前卡取值。
            sample["model_process_host_memory_mib"] = process_memory.get("model_process_host_memory_mib")
            sample["model_process_gpu_memory_mib"] = gpu_memory.get("model_process_gpu_memory_mib")
            sample["product_stack_gpu_memory_mib"] = gpu_memory.get("product_stack_gpu_memory_mib")
            sample["model_process_gpu_memory_source"] = process_memory.get("model_process_gpu_memory_source")
            sample["benchmark_process_memory_used_mib"] = process_memory.get(
                "benchmark_process_memory_used_mib"
            )
            sample["benchmark_process_gpu_memory_mib"] = gpu_memory.get("benchmark_process_gpu_memory_mib")
            sample["other_processes_memory_mib"] = process_memory.get("other_processes_memory_mib")
            sample["all_processes_memory_mib"] = process_memory.get("all_processes_memory_mib")
            sample["system_memory_used_mib"] = system_memory.get("system_memory_used_mib")
            sample["system_memory_total_mib"] = system_memory.get("system_memory_total_mib")
            sample["system_memory_available_mib"] = system_memory.get("system_memory_available_mib")
            sample["system_memory_scope"] = system_memory.get("system_memory_scope")
            sample["system_memory_delta_mib"] = process_memory.get("system_memory_delta_mib")
            sample["system_memory_baseline_mib"] = process_memory.get("system_memory_baseline_mib")
            sample["llama_server_args"] = process_memory.get("llama_server_args")
            sample["sentrix_stack_pss_mib"] = process_memory.get("sentrix_stack_pss_mib")
            sample["sentrix_product_pss_mib"] = process_memory.get("sentrix_product_pss_mib")
            sample["sentrix_auxiliary_model_pss_mib"] = process_memory.get("sentrix_auxiliary_model_pss_mib")
            sample["sentrix_auxiliary_model_uma_mib"] = process_memory.get("sentrix_auxiliary_model_uma_mib")
            sample["product_stack_memory_mib"] = process_memory.get("product_stack_memory_mib")
            sample["memory_scope"] = process_memory.get("memory_scope") or sample.get("memory_scope")
            sample["process_memory_scope"] = process_memory.get("process_memory_scope")
            sample["model_process_system_memory_scope"] = process_memory.get(
                "model_process_system_memory_scope"
            )
            sample["benchmark_process_memory_scope"] = process_memory.get("benchmark_process_memory_scope")
            sample["benchmark_process_gpu_memory_scope"] = process_memory.get(
                "benchmark_process_gpu_memory_scope"
            )
            sample["process_attribution"] = process_memory.get("process_attribution")
            sample["all_processes_scope"] = process_memory.get("all_processes_scope")
            sample["model_processes"] = process_memory.get("processes") or []
            sample["model_system_processes"] = process_memory.get("model_system_processes") or []
            sample["benchmark_processes"] = process_memory.get("benchmark_processes") or []
            sample["benchmark_gpu_processes"] = process_memory.get("benchmark_gpu_processes") or []
            sample["other_processes"] = process_memory.get("other_processes") or []
            sample["all_processes"] = process_memory.get("all_processes") or []
            sample["model_process_memory_limit_mib"] = process_memory.get("process_memory_limit_mib")
            sample["model_process_memory_over_limit"] = process_memory.get("process_memory_over_limit")
            sample["model_process_pid"] = process_memory.get("root_pid")
            sample["component_validity"] = {
                key: "valid"
                if (value := finite_number(sample.get(key))) is not None and value >= 0
                else "unavailable"
                for key in (
                    "system_memory_used_mib",
                    "model_process_host_memory_mib",
                    "model_process_gpu_memory_mib",
                    "product_stack_memory_mib",
                    "model_process_system_memory_used_mib",
                    "model_process_footprint_mib",
                    "model_process_mapped_weights_mib",
                )
            }
            sample["process_observed_at_monotonic"] = process_memory.get(
                "pss_sampled_at_monotonic", timestamp
            )
            sample["memory_unit"] = process_memory.get("memory_unit") or sample.get("memory_unit")
            self._source = gpu_result.get("source") or memory_result.get("source") or self._source
            sample["source"] = self._source or self.provider.__class__.__name__
            sample["pss_sampled_at_monotonic"] = process_memory.get("pss_sampled_at_monotonic")
            sample["kv_cache_used_tokens"] = process_memory.get("kv_cache_used_tokens")
            vllm_metrics = process_memory.get("vllm_metrics") or {}
            sample["kv_cache_usage_pct"] = vllm_metrics.get("kv_cache_usage_pct")
            memory_profile = process_memory.get("memory_profile") or {}
            sample["kv_cache_capacity_gib"] = memory_profile.get("kv_cache_capacity_gib")
            sample["kv_cache_capacity_tokens"] = memory_profile.get("kv_cache_capacity_tokens")
            sample["weight_gib"] = memory_profile.get("weight_gib")
            sample["peak_activation_gib"] = memory_profile.get("peak_activation_gib")
            sample["non_torch_gib"] = memory_profile.get("non_torch_gib")
            sample["cuda_graph_gib"] = memory_profile.get("cuda_graph_gib")
            sample, _rejected = validated_telemetry_sample(sample)
            with self._samples_lock:
                self.samples.append(sample)
            if self.on_sample:
                callback_started = time.monotonic()
                try:
                    self.on_sample(sample)
                    self._callback_error = None
                except Exception as error:
                    self._callback_error = f"{type(error).__name__}: {error}"
                    self._last_callback_failure = self._callback_error
                    self._callback_failures += 1
                finally:
                    self._previous_callback_seconds = time.monotonic() - callback_started

    def aggregate(self) -> dict[str, Any]:
        """把已累积的样本收成曲线统计（1.0 ``GpuSampler.aggregate`` ``:1785``）。

        算法在 ``domain.runs.analysis.aggregate_gpu_samples``——**采样器只做转发**，
        这样聚合逻辑能在一个没有线程、没有 provider 的用例里逐字段被钉住。
        """
        with self._samples_lock:
            snapshot = list(self.samples)
        result = aggregate_gpu_samples(snapshot)
        result["sampling_integrity"] = {
            "callback_failures": self._callback_failures,
            "last_callback_failure": self._last_callback_failure,
            "publisher_failures": self._publisher_failures,
            "last_publisher_failure": self._last_publisher_failure,
            "collection_failures": self._collection_failures,
            "last_collection_failure": self._last_collection_failure,
            "stop_pending_threads": self.pending_work() if self._stop.is_set() else list(self._stop_pending),
            "missed_intervals": self._missed_intervals,
        }
        return result

    def _publish_loop(self) -> None:
        # One publisher, no queue and no sampling lock held during aggregation.
        while not self._stop.wait(self.publish_interval):
            if self.on_aggregate is not None:
                try:
                    aggregate = self.aggregate()
                    if not self._stop.is_set():
                        self.on_aggregate(aggregate)
                except Exception as error:
                    self._publisher_failures += 1
                    self._last_publisher_failure = f"{type(error).__name__}: {error}"

    def _run(self) -> None:
        try:
            self._sample_loop()
        finally:
            if isinstance(self.provider, ManagedTelemetryWork):
                self.provider.stop_sampling()

    def _sample_loop(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            # 1.0 ``:1782``：一次探不到不该终止采样。
            try:
                self.sample_once(scheduled_at=deadline)
            except Exception as error:
                self._collection_failures += 1
                self._last_collection_failure = f"{type(error).__name__}: {error}"
            deadline += self.interval
            now = time.monotonic()
            if deadline <= now:
                missed = math.floor((now - deadline) / self.interval) + 1
                self._missed_intervals += missed
                deadline += missed * self.interval
            self._stop.wait(max(0, deadline - now))
