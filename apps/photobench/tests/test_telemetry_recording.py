"""Recording modes must bound memory without changing saved evaluation results."""

import json
from types import SimpleNamespace

import pytest
from fakes_execution import FakeTelemetry
from test_config_service import PROVIDERS, TARGETS
from test_run_execution import _execution, _request
from test_run_read import _NoQaMetadata

from photobench.application.config.service import ConfigService
from photobench.application.run_execution.gpu_sampler import GpuSampler
from photobench.application.runs.index import RunIndex
from photobench.application.runs.read_runs import ReadRuns
from photobench.domain.errors import InvalidRequestError
from photobench.domain.runs.analysis import resource_series_stats, validated_telemetry_sample
from photobench.domain.runs.telemetry_summary import TelemetrySummary, attach_recorded_history
from photobench.domain.runtime.metrics import RESOURCE_TELEMETRY_VERSION
from photobench.infrastructure.config.files import JsonConfigStore
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore


def test_history_is_opt_in_and_only_a_json_boolean_is_accepted(tmp_path):
    store = JsonConfigStore(tmp_path)
    store.write_json("judge_providers.json", PROVIDERS)
    store.write_json("vllm_targets.json", TARGETS)
    assert ConfigService(store).telemetry_history_enabled is False
    for enabled in (False, True):
        store.write_json("telemetry.json", {"history_enabled": enabled})
        config = ConfigService(store)
        assert config.telemetry_history_enabled is enabled
        assert config.public_config()["telemetry_config"] == {"history_enabled": enabled}
    for invalid in ("false", "true", 0, 1, None):
        store.write_json("telemetry.json", {"history_enabled": invalid})
        with pytest.raises(InvalidRequestError, match="JSON boolean"):
            ConfigService(store)


@pytest.mark.parametrize("history_enabled,retained", [(False, 1), (True, 240)])
def test_long_sampling_keeps_a_bounded_window_and_whole_run_peak(history_enabled, retained):
    telemetry = FakeTelemetry(process_memory={"process_memory_used_mib": 5000})
    sampler = GpuSampler(telemetry, history_enabled=history_enabled)
    sampler.sample_once()
    telemetry.process_memory_data["process_memory_used_mib"] = 100
    for _ in range(2000):
        sampler.sample_once()
        telemetry.calls.clear()
    assert len(sampler.samples) == retained
    assert len(sampler._summary.counters) < 60
    metrics = sampler.aggregate()
    assert metrics["samples_count"] == 2001
    stats = metrics["model_process_memory_used_mib"]
    assert stats["peak"] == 5000
    assert stats["mean"] == round((5000 + 2000 * 100) / 2001, 1)
    assert stats["valid_count"] == 2001
    assert "p50" not in stats and "p95" not in stats
    assert metrics["quantiles_status"] == "not_recorded_streaming"


def test_default_run_freezes_the_mode_and_never_appends_raw_history(monkeypatch):
    execution, _, writer, *_ = _execution()
    state = execution.prepare(_request())
    execution.telemetry_history_enabled = True
    monkeypatch.setattr(writer, "append_gpu_sample", lambda *args: pytest.fail("raw history written"))
    result = execution.execute(_request())
    assert state is execution.state
    assert result["telemetry_history_enabled"] is False
    assert result["telemetry_live"]["history"] == []
    assert result["telemetry_live"]["latest"]
    assert result["phases"]["gpu_metrics"]["samples_count"] >= 3
    assert len(execution._sampler.samples) == 1


@pytest.mark.parametrize("duplicate_gpu", [False, True])
def test_streaming_integrals_match_existing_scope_and_gap_rules(duplicate_gpu):
    rows = []
    for timestamp, value, phase in [
        (0, 10, "a"),
        (1, 20, "a"),
        (3, 40, "a"),
        (4, None, "a"),
        (5, 100, "a"),
        (9, 100, "a"),
        (10, 100, "b"),
        (11, 150, "b"),
    ]:
        for index in range(2 if duplicate_gpu else 1):
            rows.append(
                validated_telemetry_sample(
                    {
                        "protocol_version": RESOURCE_TELEMETRY_VERSION,
                        "_t": timestamp,
                        "power_draw_w": value,
                        "phase": phase,
                        "index": index,
                    }
                )[0]
            )
    summary = TelemetrySummary()
    for row in rows:
        summary.add(row)
        # Reading snapshots between GPU rows must not commit an unfinished timestamp group.
        summary.snapshot()
    result = summary.snapshot()["power_draw_w"]
    expected = resource_series_stats(rows, "power_draw_w")
    for key in ("peak", "mean", "valid_count", "covered_seconds", "coverage_ratio", "energy_joules"):
        assert result[key] == pytest.approx(expected[key])
    assert summary.snapshot()["sampling"]["gaps_over_3_seconds"] == 1


def test_missing_observation_keeps_platform_context_without_reusing_a_memory_value():
    summary = TelemetrySummary()
    summary.add(
        validated_telemetry_sample(
            {
                "_t": 0,
                "protocol_version": RESOURCE_TELEMETRY_VERSION,
                "memory_accounting": "jetson_pss_and_nvmap_v3",
                "model_process_memory_used_mib": 100,
            }
        )[0]
    )
    summary.add(validated_telemetry_sample({"_t": 1, "model_process_memory_used_mib": None})[0])
    metrics = summary.snapshot()
    assert metrics["protocol_version"] == RESOURCE_TELEMETRY_VERSION
    assert metrics["memory_profile"]["method"] == "jetson_pss_and_nvmap_v3"
    assert metrics["model_process_memory_used_mib"]["valid_count"] == 1
    assert summary.latest["model_process_memory_used_mib"] is None


def _reader(store, **kwargs):
    return ReadRuns(store, RunIndex(), _NoQaMetadata(), SimpleNamespace(), lambda: "unused", **kwargs)


@pytest.mark.parametrize("recorded_mode", [None, True])
def test_disabled_history_reads_also_skip_legacy_and_previously_recorded_runs(
    tmp_path, monkeypatch, recorded_mode
):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    state = {
        "run_id": "r",
        "status": "completed",
        "telemetry_live": {"latest": {"temperature_c": 60}, "history": [{"t": 1}, {"t": 2}]},
        "phases": {"gpu_metrics": {"temperature_c": {"peak": 95, "p95": 90}}},
    }
    if recorded_mode is not None:
        state["telemetry_history_enabled"] = recorded_mode
    writer.write_snapshot("r", state)
    monkeypatch.setattr(store, "read_gpu_sample_lines", lambda _: pytest.fail("old history opened"))
    result = _reader(store, history_reads_enabled=False).get_run("r", include_history=True)
    assert result["history_mode"] == "latest"
    assert result["telemetry_live"]["history"] == []
    assert result["phases"] == state["phases"]
    assert store.load_state("r") == state


def test_latest_mode_full_detail_never_opens_history_and_preserves_results(tmp_path, monkeypatch):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    state = {
        "run_id": "r",
        "status": "completed",
        "telemetry_history_enabled": False,
        "telemetry_live": {"latest": {"temperature_c": 50}, "history": []},
        "phases": {"gpu_metrics": {"temperature_c": {"peak": 70, "mean": 60}}},
        "items": [{"qa_id": "q", "answer": "original"}],
    }
    writer.write_snapshot("r", state)
    writer.append_gpu_sample("r", {"_t": 0, "temperature_c": 1})
    raw = (tmp_path / "r" / "gpu_samples.jsonl").read_bytes()
    monkeypatch.setattr(store, "read_gpu_sample_lines", lambda _: pytest.fail("history opened"))
    for include_history in (False, True):
        result = _reader(store).get_run("r", include_history=include_history)
        assert result["history_mode"] == "latest"
        assert result["telemetry_live"]["latest"]["temperature_c"] == 50
        assert result["phases"] == state["phases"]
    assert store.load_state("r") == state
    assert (tmp_path / "r" / "gpu_samples.jsonl").read_bytes() == raw


def test_opt_in_detail_streams_a_bounded_preview_without_replacing_summary(tmp_path, monkeypatch):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    state = {
        "run_id": "r",
        "status": "completed",
        "telemetry_history_enabled": True,
        "telemetry_live": {"statistics_mode": "streaming_v1", "history_enabled": True},
        "phases": {"gpu_metrics": {"temperature_c": {"peak": 95, "mean": 60, "valid_count": 5000}}},
    }
    writer.write_snapshot("r", state)
    path = tmp_path / "r" / "gpu_samples.jsonl"
    with path.open("w") as handle:
        for i in range(5000):
            handle.write(json.dumps({"_t": i, "temperature_c": 50}) + chr(10))
        handle.write("broken line" + chr(10))
    # The adapter must yield lines instead of allocating the complete text.
    original = type(path).read_text

    def guard(self, *args, **kwargs):
        if self.name == "gpu_samples.jsonl":
            pytest.fail("whole history read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", guard)
    result = _reader(store).get_run("r")
    history = result["telemetry_live"]["history"]
    assert 2 <= len(history) <= 240
    assert history[0]["t"] == 0 and history[-1]["t"] == 4999
    assert result["phases"] == state["phases"]
    assert result["telemetry_live"]["history_samples_read"] == 5000


def test_bad_history_lines_and_read_failure_keep_a_bounded_diagnostic():
    def lines():
        yield "null"
        yield "{"
        yield '{"_t": 1, "temperature_c": 50}'
        raise OSError("synthetic read error")

    live = {}
    attach_recorded_history(live, lines())
    assert len(live["history"]) == 1
    assert "synthetic read error" in live["history_read_error"]


def test_decimation_keeps_actual_gaps_without_inventing_gaps_from_stride():
    def lines(with_gap):
        for index in range(5000):
            yield json.dumps(
                {
                    "_t": index + (10 if with_gap and index >= 777 else 0),
                    "temperature_c": 50,
                }
            )

    live = {}
    attach_recorded_history(live, lines(False))
    assert not any(point["gap_before"] for point in live["history"])
    attach_recorded_history(live, lines(True))
    assert any(point["gap_before"] for point in live["history"])
    assert len(live["history"]) <= 240


def test_integrated_streaming_statistics_and_previews_are_separate_per_gpu():
    from photobench.domain.runs.telemetry_summary import DeviceTelemetrySummary
    from photobench.domain.runs.analysis import apply_live_sample

    summary = DeviceTelemetrySummary()
    live = {}
    rows = []
    for t in range(1000):
        for gpu in (0, 1):
            row = {
                "_t": t,
                "index": gpu,
                "model_process_gpu_memory_mib": 6000 if gpu == 0 else 0,
                "power_draw_w": 100 if gpu == 0 else 10,
                "protocol_version": RESOURCE_TELEMETRY_VERSION,
            }
            summary.add(validated_telemetry_sample(row)[0])
            rows.append(json.dumps(row))
            apply_live_sample(live, row, "qa", "答题", record_history=False)
    result = summary.snapshot()
    assert result["device_index"] == 0 and result["raw_samples_count"] == 2000
    assert result["devices"]["0"]["power_draw_w"]["mean"] == 100
    assert result["devices"]["1"]["power_draw_w"]["mean"] == 10
    assert all(not d["history"] for d in live["devices"].values())
    attach_recorded_history(live, iter(rows))
    assert all(len(d["history"]) <= 240 for d in live["devices"].values())
    assert {p["index"] for p in live["devices"]["0"]["history"]} == {0}
    assert {p["index"] for p in live["devices"]["1"]["history"]} == {1}
