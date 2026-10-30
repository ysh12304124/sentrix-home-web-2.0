"""New protocol: independent ledgers, observable identity, expiry and weighted statistics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from photobench.domain.runs.analysis import (
    aggregate_gpu_samples,
    apply_live_sample,
    attach_gpu_sample_history,
    downsample_history,
    resource_series_stats,
    validated_telemetry_sample,
)
from photobench.domain.runtime.metrics import RESOURCE_TELEMETRY_VERSION as VERSION
from photobench.infrastructure.runtime import jetson_telemetry as jetson
from photobench.infrastructure.runtime import providers
from photobench.infrastructure.runtime.hardware import HostCpuSampler


def test_cpu_first_observation_is_warmup_and_bounded(monkeypatch):
    values = iter([88, 0, 100, 101])
    monkeypatch.setattr(
        "photobench.infrastructure.runtime.hardware.psutil.cpu_percent", lambda interval: next(values)
    )
    cpu = HostCpuSampler()
    assert [cpu.sample() for _ in range(4)] == [None, 0, 100, None]


def sample(t=102):
    return {
        "protocol_version": VERSION,
        "_t": t,
        "observed_at_epoch": 1000,
        "memory_accounting": "jetson_pss_and_nvmap_v3",
        "source": "jetson_local_pss",
        "memory_observation_version": 1,
        "model_process_system_memory_used_mib": 10,
        "product_stack_memory_mib": 12,
        "model_process_gpu_memory_mib": 20,
        "product_stack_gpu_memory_mib": 22,
        "device_gpu_memory_mib": 30,
        "system_memory_used_mib": 100,
        "cpu_utilization_pct": 75,
        "cpu_observed_at_monotonic": t,
        "memory_dependencies": {
            key: [ref]
            for key, ref in [
                ("model_process_system_memory_used_mib", "pss"),
                ("product_stack_memory_mib", "pss"),
                ("model_process_gpu_memory_mib", "gpu"),
                ("product_stack_gpu_memory_mib", "gpu"),
                ("device_gpu_memory_mib", "gpu"),
                ("system_memory_used_mib", "pss"),
            ]
        },
        "memory_observations": {
            ref: {
                "status": "available",
                "observed_at_monotonic": at,
                "finished_at_monotonic": at,
                "ttl_seconds": 3,
            }
            for ref, at in [("pss", 102), ("gpu", 100)]
        },
    }


def test_gpu_expiry_does_not_erase_independent_ram_or_cpu_and_export_cannot_reuse_old_record():
    first, _ = validated_telemetry_sample(sample())
    assert first["resource_metrics"]["model_gpu_memory"]["value"] == 20 * 1048576
    expired, _ = validated_telemetry_sample({**first, "_t": 103.1, "cpu_observed_at_monotonic": 103.1})
    assert expired["model_process_system_memory_used_mib"] == 10
    assert expired["model_process_gpu_memory_mib"] is None
    assert expired["cpu_utilization_pct"] == 75
    assert expired["resource_metrics"]["model_gpu_memory"]["value"] is None
    assert expired["resource_metrics"]["model_gpu_memory"]["status"] == "unavailable"
    assert expired["resource_metrics"]["model_process_memory"]["observed_at_epoch"] == pytest.approx(998.9)
    assert "resource_metrics" not in sample()


def test_new_jetson_aggregation_never_selects_old_sum_formula_and_history_preserves_version():
    raw = sample()
    raw["model_process_host_memory_mib"] = 10
    aggregate = aggregate_gpu_samples([raw])
    assert aggregate["memory_profile"]["method"] == "jetson_pss_and_nvmap_v3"
    assert aggregate["model_process_system_memory_used_mib"]["peak"] == 10
    live = {}
    apply_live_sample(live, raw, "qa_eval", "QA")
    assert live["latest"]["resource_metrics"]["model_gpu_memory"]["value"] == 20 * 1048576
    import json

    attach_gpu_sample_history(live, [json.dumps(raw)])
    assert live["protocol_version"] == VERSION
    assert live["history"][0]["product_stack_gpu_memory_mib"] == 22
    legacy = aggregate_gpu_samples([{"source": "jetson_local_pss", "model_process_host_memory_mib": 10}])
    assert legacy["memory_profile"]["method"] == "orin_smaps_rss_plus_gpu_uma_v1"


def test_weighted_statistics_cut_missing_long_and_phase_boundary_intervals():
    points = [
        {"protocol_version": VERSION, "t": t, "power_draw_w": value, "phase": phase}
        for t, value, phase in [
            (0, 10, "a"),
            (1, 20, "a"),
            (3, 40, "a"),
            (4, None, "a"),
            (5, 100, "a"),
            (9, 100, "a"),
            (10, 100, "b"),
        ]
    ]
    stats = resource_series_stats(points, "power_draw_w")
    assert stats["peak"] == 100
    assert stats["covered_seconds"] == 3
    assert stats["energy_joules"] == 75
    assert stats["mean"] == 25
    assert stats["coverage_ratio"] == 0.3
    single = resource_series_stats(points[:1], "power_draw_w")
    assert single["peak"] == 10 and single["mean"] is None and single["energy_joules"] is None


def test_downsampling_preserves_null_transitions_and_real_gaps():
    points = [{"protocol_version": VERSION, "t": t, "system_memory_used_mib": 1} for t in range(100)]
    points[51]["system_memory_used_mib"] = None
    points[75]["gap_before"] = True
    result = downsample_history(points, limit=4)
    assert all(points[i] in result for i in (50, 51, 52, 74, 75))


def test_nvml_compute_graphics_same_pid_is_not_counted_twice_or_across_cards():
    class Nvml:
        def nvmlDeviceGetCount(self):
            return 2

        def nvmlDeviceGetHandleByIndex(self, i):
            return i

        def nvmlDeviceGetComputeRunningProcesses(self, h):
            return [SimpleNamespace(pid=42, usedGpuMemory=(h + 1) * 1048576)]

        nvmlDeviceGetGraphicsRunningProcesses = nvmlDeviceGetComputeRunningProcesses

    provider = providers.HostNvidiaTelemetryProvider()
    provider._nvml_library = Nvml()
    assert provider._gpu_processes() == {
        0: [{"pid": 42, "used_memory_mib": 1}],
        1: [{"pid": 42, "used_memory_mib": 2}],
    }


def test_gpu_attribution_still_works_when_pss_is_unreadable(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_host_process_rows",
        lambda: [{"pid": 42, "identity": "llama-server", "listening_ports": ["8100"]}],
    )
    monkeypatch.setattr(providers, "_process_pss_mib", lambda pid: None)
    provider = providers.HostNvidiaTelemetryProvider(
        endpoint_url="http://localhost:8100", telemetry_services={}
    )
    monkeypatch.setattr(provider, "_gpu_processes", lambda: {0: [{"pid": 42, "used_memory_mib": 20}]})
    result = provider.process_memory()["data"]
    assert result["model_process_system_memory_used_mib"] is None
    assert result["model_process_gpu_memory_mib"] == 20


def test_agx_power_never_sums_child_ddr_or_partial_parent_rails():
    full = jetson._parse_tegrastats_line(
        "RAM 1/10MB VDD_GPU_SOC 5000mW/4000mW "
        "VDD_CPU_CV 3000mW/2000mW VIN_SYS_5V0 2000mW/1000mW VDDQ_VDD2_1V8AO 1000mW/900mW tj@80C"
    )
    assert full["power_draw_w"] == 10 and full["power_scope"] == "module_estimate"
    assert full.get("temperature_c") is None
    partial = jetson._parse_tegrastats_line("RAM 1/10MB VDD_GPU_SOC 5000mW/4000mW VDD_CPU_CV 3000mW/2000mW")
    assert partial.get("power_draw_w") is None


def test_sampler_uses_each_cards_attribution_and_retains_device_memory_on_process_failure():
    import time

    from fakes_execution import FakeTelemetry

    from photobench.application.run_execution.gpu_sampler import GpuSampler

    now = time.monotonic()
    observation = {
        "status": "available",
        "observed_at_monotonic": now,
        "finished_at_monotonic": now,
        "ttl_seconds": 3,
    }
    process = {
        "protocol_version": VERSION,
        "gpu_process_memory_by_index": {
            0: {"model_process_gpu_memory_mib": 10, "product_stack_gpu_memory_mib": 15},
            1: {"model_process_gpu_memory_mib": 20, "product_stack_gpu_memory_mib": 25},
        },
        "memory_observation_version": 1,
        "memory_observations": {"gpu_process": observation},
        "memory_dependencies": {
            key: ["gpu_process"] for key in ("model_process_gpu_memory_mib", "product_stack_gpu_memory_mib")
        },
    }
    device = [
        {
            "index": i,
            "protocol_version": VERSION,
            "device_gpu_memory_mib": (i + 1) * 100,
            "memory_observations": {"device_gpu": observation},
            "memory_dependencies": {"device_gpu_memory_mib": ["device_gpu"]},
        }
        for i in range(2)
    ]
    provider = FakeTelemetry(gpus=device, process_memory=process)
    sampler = GpuSampler(provider)
    sampler.sample_once()
    assert [row["model_process_gpu_memory_mib"] for row in sampler.samples] == [10, 20]
    assert [row["product_stack_gpu_memory_mib"] for row in sampler.samples] == [15, 25]
    # A failed process probe must not erase a separately observed device value.
    provider.process_memory = lambda: {"status": "unavailable"}
    sampler.sample_once()
    assert [row["device_gpu_memory_mib"] for row in sampler.samples[-2:]] == [100, 200]
    assert all(row["model_process_gpu_memory_mib"] is None for row in sampler.samples[-2:])


def test_frozen_product_scope_excludes_benchmark_but_keeps_product_services():
    from photobench.infrastructure.runtime.telemetry_scope import services_from_connection

    connection = {
        "model_base_url": "http://127.0.0.1:8100/v1",
        "sentrix_url": "http://127.0.0.1:8291",
        "product": [
            {"id": "photobench", "ports": [8872]},
            {"id": "sentrix_api", "ports": [8191]},
            {"id": "redis", "ports": [6379]},
        ],
    }
    scope = services_from_connection(connection)
    assert scope["product"] == [
        {"id": "sentrix_api", "ports": [8291], "local": True},
        {"id": "redis", "ports": [6379]},
    ]
    assert connection["product"][0]["id"] == "photobench"
    assert connection["product"][1]["ports"] == [8191]
