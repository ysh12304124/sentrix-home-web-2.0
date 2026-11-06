"""Evidence parsing must preserve histogram buckets and missing observations."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("mlx_capacity", ROOT / "scripts/benchmarks/mlx_capacity.py")
assert spec and spec.loader
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def test_accepted_prefix_counts_deduplicate_short_prompt_admission():
    terminal = [{"request_id": "a", "prompt_tokens": 1600}, {"request_id": "b", "prompt_tokens": 200}]
    hit = {"event": "request_prefill", "request_id": "a", "prompt_tokens": 1600, "cached_tokens": 1024}
    miss = {"event": "request_prefill", "request_id": "b", "prompt_tokens": 200, "cached_tokens": 0}
    evidence = capacity.reconcile_prefix_reuse(terminal, [hit, hit, miss])
    assert evidence["verified"]
    assert evidence["cached_tokens"] == 1024
    assert evidence["uncached_tokens"] == 776
    assert evidence["request_hit_rate"] == 0.5
    assert evidence["token_hit_rate"] == 1024 / 1800
    assert not capacity.reconcile_prefix_reuse(terminal, [hit])["verified"]
    assert capacity.reconcile_prefix_reuse(terminal, [])["token_hit_rate"] is None


def test_rejected_or_inconsistent_prefix_evidence_is_not_a_cache_hit():
    terminal = [{"request_id": "a", "prompt_tokens": 1600}]
    hit = {"event": "request_prefill", "request_id": "a", "prompt_tokens": 1600, "cached_tokens": 1024}
    for invalid in [
        {**hit, "cached_tokens": 1601},
        {**hit, "prompt_tokens": 1599},
        {**hit, "cached_tokens": True},
    ]:
        assert not capacity.reconcile_prefix_reuse(terminal, [invalid])["verified"]
    assert not capacity.reconcile_prefix_reuse(terminal, [hit, {**hit, "cached_tokens": 0}])["verified"]
    assert not capacity.reconcile_prefix_reuse(terminal, [{**hit, "request_id": "unrelated"}])["verified"]


def test_histogram_bounds_and_outcomes_are_not_summed():
    parsed = capacity.prometheus_values(
        'vllm_mlx_inference_ttft_seconds_bucket{le="1.0"} 2\n'
        'vllm_mlx_inference_ttft_seconds_bucket{le="5.0"} 7\n'
        'vllm_mlx_inference_requests_total{result="error"} 1\n'
        'vllm_mlx_inference_requests_total{result="success"} 6\n'
    )
    assert len(parsed) == 4
    assert parsed['vllm_mlx_inference_ttft_seconds_bucket{le="1.0"}'] == 2
    assert parsed['vllm_mlx_inference_requests_total{result="error"}'] == 1


def test_fallback_engine_gauges_and_missing_values():
    samples = [
        {"engine": {"vllm_mlx_scheduler_running_requests": 3, "vllm_mlx_scheduler_waiting_requests": 7}},
        {"engine_unavailable": "ReadTimeout"},
    ]
    summary = capacity.summarize_resources(samples)
    assert summary["engine_num_requests_running_peak"] == 3
    assert summary["engine_num_requests_waiting_peak"] == 7
    assert summary["engine_metrics_missing_samples"] == 1
    assert summary["gpu_percent"]["max"] is None
    assert summary["swap_in_bytes_delta"] is None


def test_pending_prefill_queue_supersedes_scheduler_only_gauge_without_double_counting():
    summary = capacity.summarize_resources(
        [
            {
                "engine": {
                    "sentrix_mlx_num_requests_waiting": 7,
                    "vllm_mlx_scheduler_waiting_requests": 2,
                }
            }
        ]
    )
    assert summary["engine_num_requests_waiting_peak"] == 7


def test_tool_internal_unknown_timeout_is_counted_even_after_agent_returns_an_answer():
    item = {
        "tool_trace": [
            {
                "result": {
                    "model_call_observations": [
                        {
                            "diagnostics": {"request_sent": None, "error_category": "request_timeout"},
                            "error_type": "ReadTimeout",
                        }
                    ]
                }
            }
        ]
    }
    counts = capacity.diagnostic_failure_categories([item])
    assert counts["timeout"] == counts["unknown"] == 1


def test_engine_journal_reconciles_main_calls_and_cached_tool_observations():
    vision = {"call_id": "vision", "status": "complete", "prompt_tokens": 900, "completion_tokens": 20}
    items = [
        {
            "model_call_metrics": [
                {"call_id": "agent", "status": "complete", "prompt_tokens": 500, "completion_tokens": 30}
            ],
            "tool_trace": [{"result": {"model_call_observations": [vision]}}] * 2,
        }
    ]
    terminal = [
        {"request_id": "r1", "prompt_tokens": 900, "completion_tokens": 20},
        {"request_id": "r2", "prompt_tokens": 500, "completion_tokens": 30},
    ]
    evidence = capacity.reconcile_model_calls(items, terminal)
    assert evidence["verified"] is True
    assert evidence["expected_calls"] == evidence["recorded_calls"] == 2
    assert capacity.reconcile_model_calls(items, terminal[:1])["verified"] is False
    terminal[1]["request_id"] = "r1"
    assert capacity.reconcile_model_calls(items, terminal)["verified"] is False


def test_incomplete_or_conflicting_application_observations_never_pass_reconciliation():
    call = {"call_id": "c", "status": "complete", "prompt_tokens": 50, "completion_tokens": 3}
    terminal = [{"request_id": "r", "prompt_tokens": 50, "completion_tokens": 3}]
    assert (
        capacity.reconcile_model_calls([{"model_call_metrics": [{**call, "status": "error"}]}], terminal)[
            "verified"
        ]
        is False
    )
    evidence = capacity.reconcile_model_calls(
        [{"model_call_metrics": [call, {**call, "completion_tokens": 4}]}], terminal
    )
    assert evidence["verified"] is False
    assert evidence["conflicting_observations"] == 1


def test_ambiguous_submission_is_never_replayed(tmp_path):
    import httpx
    import pytest

    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("accepted response lost", request=request)

    receipt = tmp_path / "submission.json"
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(httpx.ReadTimeout):
            capacity.submit_run_once(client, receipt, {"models": ["__current__"]})
        with pytest.raises(FileExistsError):
            capacity.submit_run_once(client, receipt, {"models": ["__current__"]})
    assert len(attempts) == 1
    assert "submitting" in receipt.read_text()


def test_accepted_submission_keeps_exact_receipt(tmp_path):
    import json

    import httpx

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(202, json={"run_ids": ["r1"]}))
    ) as client:
        receipt = tmp_path / "submission.json"
        assert capacity.submit_run_once(client, receipt, {"qa_concurrency": 16}) == "r1"
    stored = json.loads(receipt.read_text())
    assert stored["status"] == "accepted"
    assert stored["response"]["run_ids"] == ["r1"]
    assert stored["payload"] == {"qa_concurrency": 16}


def test_failure_categories_use_diagnostics_not_question_or_answer_prose():
    items = [
        {"answer": "The example says out of memory and timeout", "error": ""},
        {
            "model_call_metrics": [
                {
                    "error_type": "RequestOutcomeUnknownError",
                    "diagnostics": {"error_category": "request_timeout"},
                }
            ]
        },
        {"runtime_turns": [{"error": "database is locked"}, {"error": "database is locked"}]},
        {"model_call_metrics": [{"diagnostics": {"error_category": "output_truncated"}}]},
    ]
    assert capacity.diagnostic_failure_categories(items) == {
        "oom": 0,
        "lock_conflict": 1,
        "service_exit": 0,
        "timeout": 1,
        "unknown": 1,
        "truncation": 1,
    }


def test_unknown_submission_from_legacy_error_only_record_is_counted():
    assert (
        capacity.diagnostic_failure_categories(
            [{"error": "POST outcome unknown after ReadError; request was not replayed"}]
        )["unknown"]
        == 1
    )


def test_inspected_failed_round_can_resume_later_trials_without_becoming_a_pass(tmp_path):
    import hashlib
    import json

    import pytest

    path = tmp_path / "public-summary.json"
    failed = {"status": "completed", "passed": False, "failures": {"counter_mismatch": 1}}
    path.write_text(json.dumps(failed))
    with pytest.raises(RuntimeError, match="requires inspection"):
        capacity.read_completed_round(path)
    inspected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert capacity.read_completed_round(path, inspected) == failed
    assert json.loads(path.read_text())["passed"] is False
    path.write_text(json.dumps({**failed, "failures": {"unknown": 1}}))
    with pytest.raises(RuntimeError, match="requires inspection"):
        capacity.read_completed_round(path, inspected)


def test_nonterminal_round_cannot_be_skipped_even_with_a_matching_hash(tmp_path):
    import hashlib

    import pytest

    path = tmp_path / "public-summary.json"
    path.write_text('{"status":"running","passed":false}')
    with pytest.raises(RuntimeError, match="not terminal"):
        capacity.read_completed_round(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_scheduler_output_disagreement_fails_even_when_application_pairs_match():
    call = {"call_id": "c", "status": "complete", "prompt_tokens": 275, "completion_tokens": 150}
    terminal = [
        {
            "request_id": "r",
            "prompt_tokens": 275,
            "completion_tokens": 150,
            "output_counters_consistent": False,
            "returned_completion_tokens": 144,
        }
    ]
    evidence = capacity.reconcile_model_calls([{"model_call_metrics": [call]}], terminal)
    assert evidence["token_multiset_matches"] is True
    assert evidence["output_counters_consistent"] is False
    assert evidence["verified"] is False


def test_macos_swap_uses_swapins_not_general_pageins():
    text = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pageins: 99999.\nPageouts: 8888.\nSwapins: 7.\nSwapouts: 11.\n"
    )
    assert capacity.macos_swap_counters(text) == {
        "actual_swap_in_bytes": 7 * 16384,
        "actual_swap_out_bytes": 11 * 16384,
    }


def test_speed_gate_requires_both_trials_and_rejects_one_fast_one_slow():
    import pytest

    def round(trial, seconds):
        return {
            "label": f"C16-R{trial}",
            "actual_batch_peak": 16,
            "trial": trial,
            "concurrency": 16,
            "questions": 64,
            "completed": 64,
            "judge_completed": 64,
            "passed": True,
            "failures": {},
            "model_call_evidence": {"verified": True},
            "qa_seconds": seconds,
        }

    baseline = [round(1, 1800), round(2, 1800)]
    assert capacity.optimization_speed_gate(baseline, [round(1, 1500), round(2, 1500)])["passed"]
    assert not capacity.optimization_speed_gate(baseline, [round(1, 1200), round(2, 1750)])["passed"]
    assert not capacity.optimization_speed_gate(baseline, [round(1, 1700), round(2, 1700)])["passed"]
    with pytest.raises(ValueError, match="two distinct"):
        capacity.optimization_speed_gate(baseline, [round(1, 1400), round(1, 1400)])
    with pytest.raises(ValueError, match="fully accepted"):
        capacity.optimization_speed_gate(baseline, [round(1, 1400), {**round(2, 1400), "passed": False}])


def test_single_reference_requires_explicit_opt_in_and_never_waives_candidate_trial():
    import pytest

    def row(trial, seconds):
        return {
            "label": f"C16-R{trial}",
            "trial": trial,
            "concurrency": 16,
            "actual_batch_peak": 16,
            "questions": 64,
            "completed": 64,
            "judge_completed": 64,
            "passed": True,
            "failures": {},
            "model_call_evidence": {"verified": True},
            "qa_seconds": seconds,
        }

    baseline = [row(1, 1701.0975)]
    candidate = [row(1, 1450), row(2, 1450)]
    with pytest.raises(ValueError, match="two distinct"):
        capacity.optimization_speed_gate(baseline, candidate)
    gate = capacity.optimization_speed_gate(baseline, candidate, allow_single_baseline=True)
    assert gate["passed"] and gate["baseline_trials"] == 1
    assert gate["baseline_qpm"] == pytest.approx(2.257366200350068)
    with pytest.raises(ValueError, match="two distinct"):
        capacity.optimization_speed_gate(baseline, candidate[:1], allow_single_baseline=True)
    with pytest.raises(ValueError, match="fully accepted"):
        capacity.optimization_speed_gate(
            [{**baseline[0], "passed": False}], candidate, allow_single_baseline=True
        )
    assert not capacity.optimization_speed_gate(
        baseline, [row(1, 1500), row(2, 1500)], allow_single_baseline=True
    )["passed"]


def test_cancel_ack_alone_cannot_release_baseline_switch_gate():
    import pytest

    receipt = {
        "status": "stopped",
        "label": "C16-R2-v7",
        "run_id": "cancelled-run",
        "user_requested": True,
        "raw_status": "cancelled",
        "work": {"cleanup_status": "stopped", "work": []},
    }
    capacity.validate_user_cancelled_baseline(receipt, "cancelled-run")
    for change in [
        {"user_requested": False},
        {"raw_status": "running"},
        {"work": {"cleanup_status": "pending", "work": []}},
        {"work": {"cleanup_status": "stopped", "work": [{"worker_alive": True}]}},
        {"run_id": "different-run"},
    ]:
        with pytest.raises(ValueError, match="confirmed user cancellation"):
            capacity.validate_user_cancelled_baseline({**receipt, **change}, "cancelled-run")
