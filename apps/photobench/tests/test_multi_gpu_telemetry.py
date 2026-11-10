"""GPU device identity survives live, full-history and aggregate projections."""

import copy
import json

import pytest

from photobench.domain.runs.analysis import (
    aggregate_gpu_samples,
    apply_live_sample,
    attach_gpu_sample_history,
    publish_full_sample_phase_stats,
)
from photobench.domain.runtime.metrics import RESOURCE_TELEMETRY_VERSION


def rows(main_gpu=0, count=4):
    return [
        {
            "index": index,
            "_t": float(t),
            "phase": "qa_eval",
            "phase_label": "QA",
            "source": "host_nvml",
            "protocol_version": RESOURCE_TELEMETRY_VERSION,
            "memory_accounting": "nvidia_pss_and_vram",
            "model_process_gpu_memory_mib": 6126 if index == main_gpu else 0,
            "product_stack_gpu_memory_mib": 12250 if index == main_gpu else 0,
            "device_gpu_memory_mib": 19810 if index == main_gpu else 464,
            "model_process_system_memory_used_mib": 2300,
            "system_memory_used_mib": 122000,
            "temperature_c": 70 if index == main_gpu else 40,
            "power_draw_w": 200 if index == main_gpu else 25,
        }
        for t in range(count)
        for index in (0, 1)
    ]


@pytest.mark.parametrize("main_gpu", [0, 1])
def test_live_and_complete_views_keep_device_samples_and_statistics_together(main_gpu):
    samples = rows(main_gpu)
    original = copy.deepcopy(samples)
    live = {}
    for point in samples:
        apply_live_sample(live, point, "qa_eval", "QA", tail=3)
    assert live["device_index"] == main_gpu
    assert live["samples_count"] == 4 and live["raw_samples_count"] == 8
    assert live["latest"]["index"] == main_gpu
    assert live["latest"]["model_process_gpu_memory_mib"] == 6126
    assert len(live["history"]) == 3
    assert {p["index"] for p in live["history"]} == {main_gpu}
    json.dumps(live)  # The default view must not create a cycle through devices.

    aggregate = aggregate_gpu_samples(samples)
    assert aggregate["device_index"] == main_gpu
    assert aggregate["samples_count"] == 4 and aggregate["raw_samples_count"] == 8
    assert aggregate["model_process_gpu_memory_mib"]["mean"] == 6126
    assert aggregate["model_process_gpu_memory_mib"]["p50"] == 6126
    assert aggregate["power_draw_w"]["energy_joules"] == 600

    attach_gpu_sample_history(live, [json.dumps(point) for point in samples])
    assert live["latest"]["index"] == main_gpu
    assert len(live["history"]) == 4
    assert live["raw_samples_count"] == 8
    for key, device in live["devices"].items():
        stats = device["field_stats"]["model_process_gpu_memory_mib"]
        assert stats == aggregate["devices"][key]["model_process_gpu_memory_mib"]
        assert stats["valid_count"] == 4
        assert device["latest"]["index"] == int(key)
        assert stats["mean"] == (6126 if int(key) == main_gpu else 0)
        assert len({p["t"] for p in device["history"]}) == len(device["history"])
    result = {"phases": {"gpu_metrics": {"status": "done", "samples_count": 8}}}
    publish_full_sample_phase_stats(result, live)
    phase = result["phases"]["gpu_metrics"]
    assert phase["samples_count"] == 4
    assert phase["model_process_gpu_memory_mib"]["mean"] == 6126
    assert phase["devices"][str(1 - main_gpu)]["model_process_gpu_memory_mib"]["mean"] == 0
    assert samples == original


def test_full_history_downsamples_each_device_and_preserves_its_own_gaps_and_nulls():
    samples = rows(count=500)
    for point in samples:
        if point["index"] == 0 and point["_t"] == 250:
            point["model_process_gpu_memory_mib"] = None
        if point["index"] == 1 and point["_t"] >= 300:
            point["_t"] += 10
    live = {}
    attach_gpu_sample_history(live, [json.dumps(point) for point in samples])
    for key, device in live["devices"].items():
        assert len(device["history"]) <= 240
        assert {p["index"] for p in device["history"]} == {int(key)}
        assert device["samples_count"] == 500
    assert any(p["model_process_gpu_memory_mib"] is None for p in live["devices"]["0"]["history"])
    assert any(p.get("gap_before") for p in live["devices"]["1"]["history"])
    assert not any(p.get("gap_before") for p in live["devices"]["0"]["history"])


def test_unidentified_records_remain_separate_and_legacy_unindexed_runs_keep_their_shape():
    samples = rows() + [{"_t": 5, "model_process_gpu_memory_mib": 99}]
    live = {}
    attach_gpu_sample_history(live, [json.dumps(point) for point in samples])
    assert live["device_index"] == 0
    assert live["devices"]["unidentified"]["latest"]["model_process_gpu_memory_mib"] == 99
    legacy = {}
    attach_gpu_sample_history(legacy, [json.dumps(samples[-1])])
    assert "devices" not in legacy
    assert "index" not in legacy["latest"]
