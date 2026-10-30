"""``GpuSampler`` 的用例：``TelemetryProvider`` 三个方法的接线证据。

**采样是这一片唯一调 ``gpu_stats`` / ``process_memory`` / ``system_memory`` 的地方。**
在这之前它们是 method-reachability 的盲区——门禁判「已接线」是因为 provider 自己的
实现里有同名 ``self.*`` 调用，那是漏报。改坏这三个方法名在这里会红。

用例一律用 ``sample_once`` 而不是起线程：断言的是字段映射，与线程调度无关，
而线程版要在负载高的机器上等 0.5 秒才出第一个点（还可能一个点都没出）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fakes_execution import FakeTelemetry

from photobench.application.run_execution.gpu_sampler import PHASE_LABELS, GpuSampler


def _sampler(**kwargs: Any) -> tuple[GpuSampler, list[dict[str, Any]], FakeTelemetry]:
    samples: list[dict[str, Any]] = []
    telemetry = FakeTelemetry(**kwargs)
    return GpuSampler(telemetry, on_sample=samples.append), samples, telemetry


def test_a_sample_merges_the_gpu_row_with_process_and_system_memory():
    """一个采样点 = 设备行 + 进程内存 + 系统内存三份数据的合并。

    这条钉住三条最容易写错的映射：``processes`` → ``model_processes``（**改名**，
    不是同名复制）、``vllm_metrics`` 里那两个键要**摊平**、以及 ``memory_profile``
    里那五个同理。少了摊平，``kv_cache_usage_pct`` 之类在样本里会是 ``None``，
    而读侧的曲线只会「看起来是空的」，不会报错。
    """
    sampler, samples, telemetry = _sampler(
        gpus=[{"index": 0, "temperature_c": None, "power_draw_w": 210.0}],
        process_memory={
            "process_memory_used_mib": 8100,
            "processes": [{"pid": 42}],
            "root_pid": 42,
            "vllm_metrics": {"kv_cache_usage_pct": 12.5},
            "memory_profile": {"kv_cache_capacity_gib": 20.0, "weight_gib": 14.0},
        },
        system_memory={"system_memory_used_mib": 30000},
    )
    sampler.sample_once()
    assert telemetry.calls == ["gpu_stats", "process_memory", "system_memory"]
    sample = samples[0]
    assert sample["index"] == 0
    assert sample["power_draw_w"] == 210.0
    assert sample["model_process_memory_used_mib"] == 8100
    assert sample["model_process_pid"] == 42
    assert sample["model_processes"] == [{"pid": 42}]
    assert sample["kv_cache_usage_pct"] == 12.5
    assert sample["kv_cache_capacity_gib"] == 20.0
    assert sample["weight_gib"] == 14.0
    assert sample["system_memory_used_mib"] == 30000
    assert sample["source"] == "fake"
    assert sample["_t"] > 0


@pytest.mark.parametrize("work, expected_starts", [(0.2, [0, 0.5, 1]), (1.2, [0, 1.5, 3])])
def test_sampler_uses_absolute_schedule_and_skips_missed_ticks(monkeypatch, work, expected_starts):
    from photobench.application.run_execution import gpu_sampler

    now = [0.0]
    starts = []

    class Stop:
        def is_set(self):
            return len(starts) >= 3

        def wait(self, delay):
            assert delay > 0
            now[0] += delay

    sampler = GpuSampler(FakeTelemetry(), interval=0.5)
    sampler._stop = Stop()

    def sample(**kwargs):
        starts.append(now[0])
        now[0] += work

    monkeypatch.setattr(gpu_sampler.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(sampler, "sample_once", sample)
    sampler._run()
    assert starts == pytest.approx(expected_starts)
    assert sampler._missed_intervals == (6 if work > 0.5 else 0)


def test_slow_publishing_does_not_hold_sampling_lock_and_stops_cleanly(monkeypatch):
    import threading

    entered, release, collected = threading.Event(), threading.Event(), threading.Event()
    sampler = GpuSampler(
        FakeTelemetry(), interval=0.01, on_aggregate=lambda metrics: None, publish_interval=0.01
    )

    def slow_aggregate():
        entered.set()
        assert release.wait(2)
        return {"samples_count": len(sampler.samples)}

    monkeypatch.setattr(sampler, "aggregate", slow_aggregate)
    sampler.start()
    try:
        assert entered.wait(2)
        sampler.on_sample = lambda sample: collected.set()
        assert collected.wait(2), "sampling must continue while the publisher is blocked"
    finally:
        release.set()
        sampler.stop()
    assert not sampler._thread.is_alive()
    assert not sampler._publisher.is_alive()


def test_sampler_owns_managed_probe_until_the_sampling_loop_exits(monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()

    class Managed(FakeTelemetry):
        events = []

        def start_sampling(self):
            self.events.append("start")

        def stop_sampling(self):
            assert release.is_set()
            self.events.append("stop")

    provider = Managed()
    sampler = GpuSampler(provider)

    def loop():
        assert provider.events == ["start"]
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(sampler, "_sample_loop", loop)
    sampler.start()
    try:
        assert entered.wait(2)
        assert provider.events == ["start"]
    finally:
        release.set()
        sampler.stop()
    assert provider.events == ["start", "stop"]
    assert sampler.pending_work() == []


def test_managed_probe_cleanup_runs_when_sampling_loop_raises(monkeypatch):
    class Managed(FakeTelemetry):
        closed = False

        def start_sampling(self):
            pass

        def stop_sampling(self):
            self.closed = True

    provider = Managed()
    sampler = GpuSampler(provider)

    def broken():
        raise RuntimeError("sampling loop failure")

    monkeypatch.setattr(sampler, "_sample_loop", broken)
    with pytest.raises(RuntimeError, match="sampling loop failure"):
        sampler._run()
    assert provider.closed


def test_jetson_cpu_gpu_split_fields_survive_the_sample_mapping():
    """Jetson 的 host/gpu/source 三字段必须进样本，否则落盘前会被丢掉。

    46 上 ``unified_model_memory`` 已经算出 714.6 + 2617.9，但样本映射表没有
    这三个键，结果 run.json 只剩 714.6（少 79%）。这条用例直接钉映射，不测算法。
    """
    sampler, samples, _telemetry = _sampler(
        process_memory={
            "model_process_system_memory_used_mib": 3332.5,
            "model_process_host_memory_mib": 714.6,
            "model_process_gpu_memory_mib": 2617.9,
            "model_process_gpu_memory_source": "nvmap_iovmm",
        }
    )
    sampler.sample_once()
    sample = samples[0]
    assert sample["model_process_system_memory_used_mib"] == 3332.5
    assert sample["model_process_host_memory_mib"] == 714.6
    assert sample["model_process_gpu_memory_mib"] == 2617.9
    assert sample["model_process_gpu_memory_source"] == "nvmap_iovmm"


def test_the_gpu_row_supplies_the_cpu_temperature_when_it_lacks_one():
    """1.0 ``:1719``：``gpu_stats`` 顶层的 ``cpu_temperature_c`` 补到**缺它的**那张卡上。

    两件事一起钉：卡自己带温度时**不覆盖**（第二张卡是 44），缺时才用它兜底
    （第一张卡拿到 55）。只断言「第一张卡是 55」的用例对一个无条件覆盖的实现
    同样成立——而那个实现会把每张卡都写成 55，多卡机器上就再也看不出差异。
    """
    samples: list[dict[str, Any]] = []
    sampler = GpuSampler(_LayeredTelemetry(), on_sample=samples.append)
    sampler.sample_once()
    assert [sample["cpu_temperature_c"] for sample in samples] == [55.0, 44.0]


def test_process_lists_default_to_empty_lists_not_none():
    """1.0 ``:1748-1753`` 的 ``or []``。

    读侧对这几个键做的是「遍历」，``None`` 会让它 TypeError——而 ``None`` 与 ``[]``
    在「进程归属没探到」这件事上表达的意思一样，所以默认值必须是空表。
    """
    sampler, samples, _telemetry = _sampler(process_memory={"process_memory_used_mib": 1})
    sampler.sample_once()
    for key in (
        "model_processes",
        "model_system_processes",
        "benchmark_processes",
        "benchmark_gpu_processes",
        "other_processes",
        "all_processes",
    ):
        assert samples[0][key] == []


def test_an_unavailable_gpu_probe_preserves_fresh_memory():
    """探不到就**不产出样本**，而不是产出一条全 0 的。

    0 是一个合法的测量结果（真的 0% 利用率），缺失不是——1.0 ``:1712`` 的判据就是
    ``status != "available"`` 直接跳过。一条全 0 的样本会污染峰值与均值两条统计。

    **夹具刻意让探不到的响应里仍然带着 gpus 数据**：只把 status 换成 unavailable 的
    话，``data.gpus`` 是空的，于是「跳过」与「不跳过」都会得到零个样本——那条用例对
    这个判据**没有区分力**（本仓库第五次踩这个坑）。真正的判据是**状态**，不是
    「data 里恰好有没有东西」：一个 provider 完全可能带着上一次的读数报 unavailable。
    """
    sampler = GpuSampler(_UnavailableWithStaleData(), on_sample=lambda _sample: None)
    sampler.sample_once()
    assert len(sampler.samples) == 1
    assert sampler.samples[0].get("temperature_c") is None
    assert sampler.samples[0]["source_status"]["device"] == "unavailable"


def test_a_failing_callback_does_not_stop_the_sampling_loop():
    """回调失败吞掉（1.0 ``:1767``）：磁盘满不该让后面的曲线整段缺失。"""

    def boom(_sample: dict[str, Any]) -> None:
        raise OSError("no space left on device")

    telemetry = FakeTelemetry()
    sampler = GpuSampler(telemetry, on_sample=boom)
    sampler.sample_once()
    assert len(sampler.samples) == 1


def test_a_provider_that_raises_is_tolerated_between_samples():
    """``_run`` 的 ``except``（1.0 ``:1782``）：一次探不到不该终止采样。

    用 ``_run`` 的一次迭代来钉：把 ``_stop`` 预先置上，循环体跑一次就退出。
    """
    sampler = GpuSampler(_RaisingTelemetry())
    # 预先置上停止位，让 ``_run`` 的循环体恰好跑一轮就退出（不然要等 0.5 秒）。
    sampler._stop.set()
    sampler._run()
    assert sampler.samples == []


def test_phase_labels_cover_every_phase_the_chain_records():
    """标签表是 ``_sample_writer`` 的输入；少一项，那一相的样本就只有英文相位名。

    单列一条是因为它是一张**查表**：漏一项不会报错，只会让某一相的 `phase_label`
    在 UI 上显示成 ``pipeline_processing`` 这样的内部名。
    """
    assert set(PHASE_LABELS) >= {
        "model_deploy",
        "scope_setup",
        "identity_seed",
        "photo_import",
        "pipeline_processing",
        "qa_eval",
        "unassigned",
    }
    assert PHASE_LABELS["pipeline_processing"] == "相册流水线预处理"


class _LayeredTelemetry:
    """真的带 ``cpu_temperature_c`` 层级与多卡的那种 ``gpu_stats`` 形状。"""

    def gpu_stats(self) -> dict[str, Any]:
        return {
            "status": "available",
            "source": "layered",
            "data": {
                "cpu_temperature_c": 55.0,
                "gpus": [{"index": 0}, {"index": 1, "cpu_temperature_c": 44.0}],
            },
        }

    def process_memory(self) -> dict[str, Any]:
        return {"status": "available", "data": {}}

    def system_memory(self) -> dict[str, Any]:
        return {"status": "available", "data": {}}

    def kv_cache(self) -> dict[str, Any]:
        return {"status": "not_applicable", "data": {}}


class _UnavailableWithStaleData(_LayeredTelemetry):
    """报 ``unavailable`` 但 ``data.gpus`` 里还留着上一次的读数。

    真的 ``UnavailableTelemetryProvider`` 不带 data，所以这种形状得单独造一个——
    而只有这种形状能把「按状态跳过」与「按 data 空跳过」区分开。
    """

    def gpu_stats(self) -> dict[str, Any]:
        return {"status": "unavailable", "reason": "nvidia-smi 超时", "data": {"gpus": [{"index": 0}]}}


class _RaisingTelemetry(_LayeredTelemetry):
    def gpu_stats(self) -> dict[str, Any]:
        raise RuntimeError("nvidia-smi 不在 PATH 上")


def test_all_sources_failed_preserves_reason_and_null_without_reusing_old_values():
    class Failed:
        def gpu_stats(self):
            return {"status": "unavailable", "reason": "exited", "data": {"gpus": [{"power_draw_w": 50}]}}

        def process_memory(self):
            return {"status": "unavailable", "reason": "permission_denied"}

        def system_memory(self):
            return {"status": "unavailable", "reason": "read_failed"}

    sampler = GpuSampler(Failed())
    sampler.sample_once()
    sample = sampler.samples[0]
    assert sample.get("power_draw_w") is None
    assert sample["model_process_system_memory_used_mib"] is None
    assert sample["source_diagnostics"]["device"]["reason"] == "exited"
    assert sample["source_diagnostics"]["process"]["reason"] == "permission_denied"
    sampler.stop()
    sampler.sample_once()
    assert len(sampler.samples) == 1


def test_callback_failure_remains_visible_after_a_later_success():
    sampler, _samples, _telemetry = _sampler()

    def fail(sample):
        raise OSError("disk full")

    sampler.on_sample = fail
    sampler.sample_once()
    sampler.on_sample = lambda sample: None
    sampler.sample_once()
    integrity = sampler.aggregate()["sampling_integrity"]
    assert integrity["callback_failures"] == 1
    assert "disk full" in integrity["last_callback_failure"]
    assert sampler.samples[-1]["sampling_diagnostics"]["previous_callback_error"] == "OSError: disk full"


def test_publisher_failure_has_diagnostic_and_retries_are_bounded_by_period():
    import threading

    failed = threading.Event()

    def fail(metrics):
        failed.set()
        raise RuntimeError("snapshot failed")

    sampler = GpuSampler(FakeTelemetry(), on_aggregate=fail, publish_interval=0.01)
    sampler.start()
    assert failed.wait(2)
    sampler.stop()
    integrity = sampler.aggregate()["sampling_integrity"]
    assert integrity["publisher_failures"] >= 1
    assert integrity["last_publisher_failure"] == "RuntimeError: snapshot failed"
    assert integrity["stop_pending_threads"] == []


def test_stop_timeout_is_explicit_instead_of_claiming_worker_exit():
    class Pending:
        name = "blocked-provider"

        def join(self, timeout):
            assert 0 <= timeout <= 10

        def is_alive(self):
            return True

    sampler = GpuSampler(FakeTelemetry())
    sampler._thread = Pending()
    sampler.stop()
    assert sampler.aggregate()["sampling_integrity"]["stop_pending_threads"] == ["blocked-provider"]
    with pytest.raises(RuntimeError, match="already running"):
        sampler.start()
