"""遥测聚合：Apple 新口径与 ``runtime_*`` 动态曲线。

两条都是「字段已经算出来，读侧却可能丢」的路径：
- Apple 的 ``memory_unit`` 从 footprint 改成 footprint+权重后，聚合分支若只认旧值，
  新 run 会掉进默认的 vLLM 估算分支，memory_profile 与实际口径不符；
- ``runtime_*`` / ``kv_cache_*`` 在 1.0 有动态兜底，2.0 若只认固定清单，provider
  以后补字段时曲线会静默为空。
"""

from __future__ import annotations

import json

from photobench.domain.runs.analysis import (
    aggregate_gpu_samples,
    apply_live_sample,
    attach_gpu_sample_history,
)


def test_apple_new_unit_keeps_the_plus_mapped_weights_method() -> None:
    metrics = aggregate_gpu_samples(
        [
            {
                "source": "apple_unified",
                "memory_unit": "apple_phys_footprint_plus_mapped_weights_mib",
                "model_process_system_memory_used_mib": 4185.7,
            }
        ]
    )
    assert metrics["memory_profile"]["method"] == "apple_phys_footprint_plus_mapped_weights_v1"
    assert metrics["memory_profile"]["memory_unit"] == "apple_phys_footprint_plus_mapped_weights_mib"


def test_apple_old_runs_keep_the_footprint_method() -> None:
    metrics = aggregate_gpu_samples(
        [
            {
                "source": "apple_unified",
                "memory_unit": "apple_phys_footprint_mib",
                "model_process_system_memory_used_mib": 1612.8,
            }
        ]
    )
    assert metrics["memory_profile"]["method"] == "apple_phys_footprint_v1"


def test_dynamic_runtime_keys_are_aggregated_without_being_in_the_fixed_list() -> None:
    metrics = aggregate_gpu_samples(
        [
            {"source": "vllm", "runtime_decode_tokens_per_second": 10.0},
            {"source": "vllm", "runtime_decode_tokens_per_second": 30.0},
        ]
    )
    assert metrics["runtime_decode_tokens_per_second"] == {
        "peak": 30.0,
        "mean": 20.0,
        "p50": 30.0,
        "p95": 30.0,
        "valid_count": 2,
        "mean_kind": "sample_mean",
        "peak_kind": "sampled_peak",
    }


def test_dynamic_runtime_keys_survive_history_attach() -> None:
    live: dict[str, object] = {}
    attach_gpu_sample_history(
        live,
        ['{"_t": 1.0, "runtime_requests_running": 2, "kv_cache_host_used_mib": 512.0, "ignored_field": 9}'],
    )
    assert live["latest"] == {"runtime_requests_running": 2, "kv_cache_host_used_mib": 512.0}


def test_full_history_preserves_related_gpu_peak_before_downsampling() -> None:
    samples = [
        {"_t": index, "benchmark_process_gpu_memory_mib": 16000 if index == 1 else 0} for index in range(554)
    ]
    live: dict = {}
    for sample in samples:
        apply_live_sample(live, sample, "qa_eval", "QA")
    live_peak = live["peak"]["benchmark_process_gpu_memory_mib"]
    attach_gpu_sample_history(live, [json.dumps(sample) for sample in samples])
    assert len(live["history"]) == 240
    assert not any(point["benchmark_process_gpu_memory_mib"] == 16000 for point in live["history"])
    assert live["peak"]["benchmark_process_gpu_memory_mib"] == live_peak == 16000
    stats = live["field_stats"]["benchmark_process_gpu_memory_mib"]
    assert stats["peak"] == 16000 and stats["valid_count"] == 554
    assert aggregate_gpu_samples(samples)["benchmark_process_gpu_memory_mib"] == stats
