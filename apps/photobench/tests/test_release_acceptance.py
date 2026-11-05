"""Release and acceptance guardrails exercised without touching real listeners."""

from __future__ import annotations

import importlib.util
import io
import json
import socket
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[3]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/deploy/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relative_paths_bind_to_the_old_deployment(tmp_path):
    release = script("photobench_release")
    assert release.bound_path("data", tmp_path) == tmp_path / "data"
    assert release.bound_path(tmp_path / "absolute", tmp_path / "other") == tmp_path / "absolute"


def test_release_registers_the_launched_api_so_full_run_does_not_restart_it(tmp_path, monkeypatch):
    release = script("photobench_release")
    from photobench.application.deploy.service import SentrixModelSwitch
    from photobench.infrastructure.deploy.sentrix_process import SubprocessSentrixProcess

    root, runtime, config = (tmp_path / name for name in ("release", "runtime", "config"))
    for path in (root, runtime, config):
        path.mkdir()
    endpoint = "http://127.0.0.1:8100/v1"
    release.write_json(
        config / "runtime_connection.json",
        {"model_base_url": endpoint, "sentrix_url": "http://127.0.0.1:8291"},
    )
    environments = []

    class Child:
        def __init__(self, *args, **kwargs):
            self.pid = 100 + len(environments)
            environments.append(kwargs["env"])

    monkeypatch.setattr(release, "listener", lambda port: None)
    monkeypatch.setattr(release.subprocess, "Popen", Child)
    monkeypatch.setattr(
        release.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps(
            {
                "paths": [
                    f"{root}/apps/api/src/sentrix/__init__.py",
                    f"{root}/apps/photobench/src/photobench/__init__.py",
                ],
                "python": [3, 11],
                "runtime": {
                    "deployment_profile": "server",
                    "agent_wall_time_s": 180,
                    "chat_timeout_s": 120,
                    "face_providers": "CPUExecutionProvider",
                    "retinaface_providers": "CPUExecutionProvider",
                },
            }
        ),
    )
    monkeypatch.setattr(release, "wait_ready", lambda revision: None)
    bindings = {key: str(tmp_path / key) for key in ("venv", "data", "datasets", "results")}
    bindings.update(config=str(config), environments={"api": {}, "photobench": {}})
    release.launch(root, runtime, bindings, "fixed")
    record = runtime / "sentrix-process.json"
    assert json.loads(record.read_text()) == {
        "pid": 100,
        "endpoint": endpoint,
        "runtime_connection": release.read_json(config / "runtime_connection.json"),
    }
    assert environments[1]["PHOTOBENCH_SENTRIX_PID_FILE"] == str(record)
    process = SubprocessSentrixProcess(
        api_url="http://127.0.0.1:8291",
        start_command=["must-not-spawn"],
        cwd=root,
        pid_file=record,
        log_path=runtime / "api.log",
        http=object(),
        alive=lambda pid: pid == 100,
    )
    outcome = SentrixModelSwitch(process).ensure_endpoint(endpoint)
    assert outcome.ok and not outcome.restarted


def test_unknown_total_cannot_pass_as_zero():
    acceptance = script("photobench_acceptance")
    assert acceptance.expected_total([10, None], None)
    assert not acceptance.expected_total([10, None], 10)
    assert not acceptance.expected_total([None], 0)
    assert acceptance.expected_total([0, 10], 10)


def test_resident_acceptance_recomputes_components_and_rejects_missing_swap():
    acceptance = script("photobench_acceptance")
    observation = {
        "status": "available",
        "observed_at_monotonic": 9,
        "finished_at_monotonic": 9.5,
        "ttl_seconds": 3,
        "components_bytes": {
            "dirty": 100 * 1048576,
            "swapped": 60 * 1048576,
            "clean": 20 * 1048576,
            "reclaimable": 10 * 1048576,
        },
    }
    sample = {
        "_t": 10,
        "memory_observation_version": 1,
        "memory_observations": {"model": observation, "product": observation},
        "memory_dependencies": {
            "model_process_system_memory_used_mib": ["model"],
            "product_stack_memory_mib": ["product"],
        },
        "model_process_system_memory_used_mib": 70,
        "product_stack_memory_mib": 70,
    }
    assert acceptance.check_apple_resident_totals(sample)
    sample["product_stack_memory_mib"] = 130
    assert not acceptance.check_apple_resident_totals(sample)
    sample["product_stack_memory_mib"] = 70
    del observation["components_bytes"]["swapped"]
    assert not acceptance.check_apple_resident_totals(sample)


def test_acceptance_derives_age_independently_instead_of_trusting_reported_age():
    acceptance = script("photobench_acceptance")
    sample = {
        "_t": 165.0,
        "device_observed_at_monotonic": 99.5,
        "device_observation_age_seconds": 0.5,
        "device_observation_ttl_seconds": 1.5,
        "power_draw_w": 30,
        "power_rails_w": {"VDD_IN": 30},
    }
    assert set(acceptance.check_device_freshness(sample)) == {
        "device_age_not_rebased_at_sample",
        "stale_device_values",
        "stale_device_rails",
    }
    sample.update(device_observation_age_seconds=65.5, power_draw_w=None, power_rails_w=None)
    assert acceptance.check_device_freshness(sample) == []


def test_jetson_acceptance_requires_process_pss_before_summing_allocations():
    acceptance = script("photobench_acceptance")
    sample = {
        "benchmark_processes": [],
        "model_process_gpu_memory_mib": 100,
        "sentrix_auxiliary_model_uma_mib": 20,
        "product_stack_memory_mib": None,
    }
    assert acceptance.check_jetson_product_total(sample)
    sample["product_stack_memory_mib"] = 120
    assert not acceptance.check_jetson_product_total(sample)
    sample["benchmark_processes"] = [{"pid": 1, "pss_mib": 30}]
    sample["product_stack_memory_mib"] = 150
    assert acceptance.check_jetson_product_total(sample)
    sample["benchmark_processes"][0]["pss_mib"] = None
    assert not acceptance.check_jetson_product_total(sample)


def test_preserved_lan_endpoint_must_belong_to_this_host(monkeypatch):
    release = script("photobench_release")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda name: (name, [], ["192.168.0.118"]))
    endpoint = "http://192.168.0.118:8103/v1"
    assert release.local_url(endpoint, preserve_local=True) == endpoint
    assert release.local_url(endpoint) == "http://127.0.0.1:8103/v1"
    with pytest.raises(RuntimeError, match="not on this host"):
        release.local_url("http://203.0.113.88:8103/v1", preserve_local=True)


def test_agent_acceptance_uses_nested_budgets_and_measured_phase_wall():
    acceptance = script("photobench_acceptance")
    items = [
        {
            "runtime_turns": [
                {
                    "termination_reason": "answered",
                    "answer": "answer",
                    "agent2_trace": {"budget_outcome": {"model_steps": 3, "max_steps": 4}},
                }
            ],
            "execution_trace": [
                {
                    "stage": "model",
                    "tool_argument_parse_facts": [{"json_object_valid": False}],
                }
            ],
        }
    ]
    phase = {"agent_phase_wall_ms": 2000, "agent_completed": 1}
    good = {
        "tool_argument_json_total": 1,
        "tool_argument_json_success": 0,
        "tool_argument_json_rate": 0,
        "qa_completion_valid_count": 1,
        "qa_completion_within_steps_rate": 1,
        "json_parse_success_rate": None,
        "agent_throughput_latency_ms": 2000,
        "agent_throughput_qa_per_s": 0.5,
    }
    assert acceptance.check_agent_summary(items, good, phase) == []
    bad = {**good, "qa_completion_valid_count": 0, "agent_throughput_latency_ms": 1500}
    assert set(acceptance.check_agent_summary(items, bad, phase)) == {
        "agent_summary:qa_completion_valid_count",
        "agent_summary:agent_throughput_latency_ms",
    }


def test_raw_statistics_detect_downsampled_or_null_coerced_summary():
    acceptance = script("photobench_acceptance")
    points = [{"system_memory_used_mib": value} for value in (None, 0, 20)]
    good = {"peak": 20, "mean": 10, "p95": 20, "valid_count": 2}
    result = {
        "phases": {"gpu_metrics": {"system_memory_used_mib": good}},
        "telemetry_live": {"field_stats": {"system_memory_used_mib": good}},
    }
    assert acceptance.check_statistics(points, result, result) == []
    bad = {"phases": {"gpu_metrics": {"system_memory_used_mib": {**good, "mean": 6.7}}}}
    assert "saved:system_memory_used_mib:full_sample_statistics" in acceptance.check_statistics(
        points, bad, result
    )


def test_release_verification_rejects_modified_source(tmp_path):
    release = script("photobench_release")
    (tmp_path / "source.py").write_text("version=1")
    release.write_json(tmp_path / "build-info.json", {"revision": "fixed"})
    release.write_json(
        tmp_path / "release-manifest.json",
        {
            "revision": "fixed",
            "sha256": {"source.py": release.digest(tmp_path / "source.py")},
        },
    )
    assert release.verify(tmp_path)["revision"] == "fixed"
    (tmp_path / "source.py").write_text("version=2")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        release.verify(tmp_path)


def test_attribution_cannot_validate_an_unrelated_ram_and_gpu_pair():
    acceptance = script("photobench_acceptance")
    sample = {
        "model_system_processes": [{"pid": 10, "ppid": 1}, {"pid": 11, "ppid": 10}],
        "model_processes": [{"pid": 11}],
    }
    assert acceptance.check_nvidia_attribution(sample, 10)
    sample["model_system_processes"].append({"pid": 99, "ppid": 1})
    sample["model_processes"].append({"pid": 99})
    assert not acceptance.check_nvidia_attribution(sample, 10)


def test_typed_media_arithmetic_keeps_types_and_exclusions():
    acceptance = script("photobench_acceptance")
    items = [
        {"media_retrieval_counts": {"gt": 2, "predicted": 4, "matched": 1}},
        {
            "answerability": "unanswerable",
            "media_retrieval_counts": {"gt": 100, "predicted": 100, "matched": 100},
        },
    ]
    good = {
        "media_retrieval_precision_micro": 0.25,
        "media_retrieval_recall_micro": 0.5,
        "media_retrieval_f1_micro": 0.333,
    }
    assert acceptance.check_retrieval_summary(items, good) == []
    assert "media_retrieval_recall_arithmetic" in acceptance.check_retrieval_summary(
        items, {**good, "media_retrieval_recall_micro": 1}
    )


def test_preflight_native_bge_probe_uses_cls_not_token_mean(monkeypatch):
    acceptance = script("photobench_acceptance")
    calls = []

    def request(url, body, timeout=180):
        calls.append(url)
        if url.endswith("/embed"):
            raise HTTPError(url, 404, "not found", {}, io.BytesIO(b"{}"))
        if url.endswith("/v1/embeddings"):
            detail = {
                "error": {
                    "message": (
                        "Pooling type 'none' is not OAI compatible. Please use a different pooling type"
                    )
                }
            }
            raise HTTPError(url, 400, "bad request", {}, io.BytesIO(json.dumps(detail).encode()))
        return [{"index": 0, "embedding": [[3, 4] + [0] * 1022, [99] * 1024]}]

    monkeypatch.setattr(acceptance, "http_value", request)
    result, protocol = acceptance.probe_text_embedding("http://127.0.0.1:8101", False)
    assert result["vector"] == [0.6, 0.8] + [0.0] * 1022
    assert protocol == "llama_cpp_bge_m3_cls_l2"
    assert len(calls) == 3


def task_item(reason="answered", errors=None):
    trace = {
        "terminal_reason": reason,
        "task_state": {"phase": "terminal"},
        "budget_outcome": {"model_steps": 2, "max_steps": 8},
        "guard": [{"code": "prompt_too_long"}] if errors else [],
    }
    return {
        "qa_id": "q1",
        "execution_status": "completed",
        "termination_reason": reason,
        "answer": "a",
        "runtime_turns": [
            {
                "agent_status": "complete" if reason == "answered" else "partial",
                "termination_reason": reason,
                "agent2_trace": trace,
            }
        ],
        "execution_trace": errors or [],
    }


def test_acceptance_distinguishes_controlled_recovery_and_task_failure_from_program_defects():
    a = script("photobench_acceptance")
    calls = [
        {
            "stage": "model",
            "step_id": "s",
            "status": "error",
            "error_type": "PromptTooLongError",
            "duration_ms": 2,
        },
        {"stage": "model", "step_id": "s", "status": "complete", "duration_ms": 3},
    ]
    good = task_item(errors=calls)
    failed = task_item("parse_failure")
    failed["qa_id"] = "q2"
    report = a.assess_tasks([good, failed], expected_count=2)
    assert report["service_errors"] == []
    assert report["success"] == 1 and report["recovered_success"] == 1
    assert report["controlled_failure"] == 1 and report["unhandled_failure"] == 0
    assert report["call_error_rate"] == {"numerator": 1, "denominator": 2}
    failed["runtime_turns"][0]["agent2_trace"]["task_state"]["phase"] = "acting"
    assert a.assess_tasks([failed], expected_count=1)["service_errors"]


def test_acceptance_does_not_invent_recovery_from_a_caught_error_or_hide_missing_questions():
    a = script("photobench_acceptance")
    caught = task_item(
        errors=[{"stage": "model", "status": "error", "error_type": "PromptTooLongError", "duration_ms": 1}]
    )
    report = a.assess_tasks([caught], expected_count=2)
    assert report["recovered_success"] == 0
    assert report["unverified_recovery_events"] == 1
    assert "scheduled_item_count_mismatch" in report["service_errors"]


def test_independent_acceptance_rejects_completed_when_final_product_turn_failed():
    a = script("photobench_acceptance")
    item = task_item("parse_failure")
    state = {"status": "completed", "items": [item]}
    tasks = a.assess_tasks([item], expected_count=1)
    assert tasks["controlled_failure"] == 1
    assert a.check_terminal_status(state, state, tasks, []) == [
        "terminal_status_expected:completed_with_errors"
    ]
    state["status"] = "completed_with_errors"
    assert a.check_terminal_status(state, state, tasks, []) == []
    assert "terminal_status_saved_api_mismatch" in a.check_terminal_status(
        state, {"status": "completed"}, tasks, []
    )
    # An earlier failed conversation turn must not disappear behind the last answer.
    item["termination_reason"] = "answered"
    assert a.assess_tasks([item], expected_count=1)["controlled_failure"] == 1


def test_independent_acceptance_keeps_zero_scores_and_recovery_while_checking_subjudges():
    a = script("photobench_acceptance")
    item = task_item()
    state = {"status": "completed", "items": [item]}
    tasks = a.assess_tasks([item], expected_count=1)
    assert a.check_terminal_status(state, state, tasks, []) == []
    item["runtime_turns"][0]["task_judges"] = [{"judge_status": "failed"}]
    assert a.check_terminal_status(state, state, tasks, []) == [
        "terminal_status_expected:completed_with_errors"
    ]


def test_acceptance_score_parser_rejects_bool_and_accepts_finite_integral_strings():
    a = script("photobench_acceptance")
    assert a.explicit_score("1.0") == 1
    assert a.explicit_score(True) is None
    assert a.explicit_score("得2分") is None


def test_environment_failure_is_detected_before_any_listener_is_touched(tmp_path, monkeypatch):
    release = script("photobench_release")
    config = tmp_path / "config"
    config.mkdir()
    release.write_json(
        config / "runtime_connection.json",
        {"model_base_url": "http://127.0.0.1:8100/v1", "sentrix_url": "http://127.0.0.1:8291"},
    )

    def bad_interpreter(*args, **kwargs):
        raise RuntimeError("missing dependency")

    monkeypatch.setattr(release.subprocess, "check_output", bad_interpreter)
    monkeypatch.setattr(
        release, "listener", lambda _: pytest.fail("touched listeners before environment check")
    )
    bindings = {key: str(tmp_path / key) for key in ("venv", "data", "datasets", "results")}
    bindings.update(config=str(config), environments={"api": {}, "photobench": {}})
    with pytest.raises(RuntimeError, match="missing dependency"):
        release.launch(tmp_path / "release", tmp_path / "runtime", bindings, "fixed")


def test_build_acceptance_does_not_require_qa_terminals(tmp_path, monkeypatch):
    acceptance = script("photobench_acceptance")
    results = tmp_path / "results" / "build-run"
    results.mkdir(parents=True)
    runtime = tmp_path / "attempt"
    runtime.mkdir()
    for name, data in {
        "bindings": {"results": str(results.parent)},
        "preflight": {"revision": "fixed", "api_pid": 1, "pb_pid": 2, "dataset": {}},
        "run-response": {"run_ids": ["build-run"]},
    }.items():
        (runtime / f"{name}.json").write_text(json.dumps(data))
    state = {
        "mode": "build",
        "status": "completed",
        "qa_count": 0,
        "run_valid": None,
        "build_revision": "fixed",
        "scope_build": {"status": "ready"},
        "items": [],
        "summary": {"judge_valid_count": 0},
        "phases": {
            name: {"status": "done"}
            for name in (
                "model_deploy",
                "scope_setup",
                "gpu_metrics",
                "aggregate",
                "identity_seed",
                "photo_import",
                "pipeline_processing",
            )
        },
    }
    (results / "run.json").write_text(json.dumps(state))
    (results / "gpu_samples.jsonl").write_text("")
    monkeypatch.setattr(acceptance, "http", lambda *args: state)
    monkeypatch.setattr(acceptance, "listener", lambda port: 1 if port == 8291 else 2)
    report = acceptance.audit(runtime)
    assert report["chain_errors"] == []
    assert report["scoring_complete"] is None
    assert report["tasks"]["expected_count"] == 0
    assert report["scoring"] == {
        "valid": 0,
        "expected": 0,
        "applicable": False,
        "missing": [],
        "standardized_responses": [],
    }
    # Empty telemetry must still fail: build is not exempt from memory coverage.
    assert "no_core_memory_in_photo_import" in report["metric_errors"]


def test_rollback_precheck_accepts_previous_settings_without_runtime_diagnostics(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    release = script("photobench_release")
    root = tmp_path / "old-release"
    for package, relative in (("sentrix", "apps/api/src"), ("photobench", "apps/photobench/src")):
        target = root / relative / package
        target.mkdir(parents=True)
        (target / "__init__.py").write_text("")
    (root / "apps/api/src/sentrix/config.py").write_text(
        "class Settings:\n    agent_wall_time_s = 180.0\n    chat_timeout_s = 120.0\n"
    )
    config = tmp_path / "config"
    config.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    release.write_json(
        config / "runtime_connection.json",
        {
            "sentrix_url": "http://127.0.0.1:8291",
            "model_base_url": "http://127.0.0.1:8100/v1",
        },
    )
    actual_check_output = subprocess.check_output
    actual_popen = subprocess.Popen

    def run_check(command, **kwargs):
        return actual_check_output([sys.executable, *command[1:]], **kwargs)

    class Child:
        pid = 999

    def popen(command, **kwargs):
        return actual_popen(command, **kwargs) if "-c" in command else Child()

    monkeypatch.setattr(release, "listener", lambda port: None)
    monkeypatch.setattr(release, "wait_ready", lambda revision: None)
    monkeypatch.setattr(release.subprocess, "Popen", popen)
    monkeypatch.setattr(release.subprocess, "check_output", run_check)
    env = {
        **os.environ,
        "FACE_PROVIDERS": "CPUExecutionProvider",
        "RETINAFACE_PROVIDERS": "CPUExecutionProvider",
    }
    bindings = {key: str(tmp_path / key) for key in ("venv", "data", "datasets", "results")}
    bindings.update(config=str(config), environments={"api": env, "photobench": env})
    release.launch(root, runtime, bindings, "old-fixed")
    product = release.read_json(config / "runtime_connection.json")["product_settings"]
    assert product["agent_wall_time_s"] == 180
    assert product["face_providers"] == "CPUExecutionProvider"


def test_acceptance_checks_component_timestamps_and_oldest_input_independently():
    acceptance = script("photobench_acceptance")
    sample = {
        "_t": 10,
        "memory_observation_version": 1,
        "memory_complete": True,
        "model_process_system_memory_used_mib": 10,
        "product_stack_memory_mib": 10,
        "memory_dependencies": {
            "model_process_system_memory_used_mib": ["rss", "nvmap"],
            "product_stack_memory_mib": ["rss", "nvmap"],
        },
        "memory_observations": {
            key: {
                "status": "available",
                "observed_at_monotonic": at,
                "finished_at_monotonic": at + 0.1,
                "ttl_seconds": 3,
                "age_seconds": 0,
            }
            for key, at in (("rss", 9), ("nvmap", 7))
        },
        "process_observed_at_monotonic": 7,
    }
    assert acceptance.check_memory_freshness(sample) == []
    sample["process_observed_at_monotonic"] = 9
    assert "memory_observation_not_oldest_input" in acceptance.check_memory_freshness(sample)
    sample["_t"] = 10.01
    assert not acceptance.memory_dependencies_fresh(sample, "product_stack_memory_mib")
    assert "stale_or_unverified_memory:product_stack_memory_mib" in acceptance.check_memory_freshness(sample)
    sample["product_stack_memory_mib"] = None
    assert acceptance.check_jetson_product_total(sample)


@pytest.mark.parametrize(
    "dependencies,sources",
    [
        (None, None),
        ([], []),
        ({"product_stack_memory_mib": None}, {}),
        ({"product_stack_memory_mib": [False]}, []),
        ({"product_stack_memory_mib": []}, {}),
    ],
)
def test_acceptance_malformed_or_empty_dependencies_cannot_verify_a_zero(dependencies, sources):
    acceptance = script("photobench_acceptance")
    sample = {
        "_t": 10,
        "memory_observation_version": 1,
        "memory_complete": True,
        "memory_dependencies": dependencies,
        "memory_observations": sources,
        "product_stack_memory_mib": 0,
    }
    assert not acceptance.memory_dependencies_fresh(sample, "product_stack_memory_mib")
    assert acceptance.check_memory_freshness(sample)
