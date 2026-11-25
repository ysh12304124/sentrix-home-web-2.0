"""Service recovery is based on delivery and failure facts, never on quality score."""

from __future__ import annotations

import copy

import pytest

from photobench.domain.runs.service_recovery import service_retry_evidence


def service_failure(qa_id="q1", category="service_error", status=429):
    return {
        "qa_id": qa_id,
        "execution_status": "completed",
        "runtime_turns": [{"status": "partial", "termination_reason": "planner_blocked"}],
        "model_call_metrics": [
            {
                "call_id": "call-1",
                "call_type": "planner",
                "status": "error",
                "error_type": "ModelServiceError",
                "diagnostics": {"error_category": category, "http_status": status},
            }
        ],
        "tool_trace": [],
    }


@pytest.mark.parametrize(
    "category,status", [("service_error", 429), ("service_error", 503), ("request_timeout", None)]
)
def test_structured_service_failure_without_delivery_is_retryable(category, status):
    item = service_failure(category=category, status=status)
    before = copy.deepcopy(item)
    assert service_retry_evidence(item) == {
        "call_id": "call-1",
        "error_category": category,
        "http_status": status,
        "termination_reason": "planner_blocked",
    }
    assert item == before


@pytest.mark.parametrize("delivered", [0, 1])
def test_delivered_text_is_kept_even_if_no_media_delivered_or_score_is_zero(delivered):
    item = service_failure()
    item["tool_trace"] = [{"tool": "final_answer", "status": "ok", "result": {"delivered": delivered}}]
    assert service_retry_evidence(item) is None


@pytest.mark.parametrize(
    "outcome",
    [
        "budget",
        "safety",
        "recovered",
        "verifier",
        "attachments",
        "multi_turn",
        "successful_later",
        "no_evidence",
        "auth_error",
        "missing_tools",
    ],
)
def test_budget_safety_delivered_or_recovered_outcomes_are_never_retried(outcome):
    item = service_failure()
    call = item["model_call_metrics"][-1]
    if outcome == "budget":
        call.update(error_type="OutputTruncatedError", finish_reason="length")
    elif outcome == "safety":
        item["runtime_turns"][0]["termination_reason"] = "guard_blocked"
    elif outcome == "recovered":
        call["recovered_by_call_id"] = "call-2"
    elif outcome == "verifier":
        call["call_type"] = "claim_verifier"
    elif outcome == "attachments":
        item["runtime_turns"][0]["attachments"] = [{"asset_id": "photo"}]
    elif outcome == "multi_turn":
        item["runtime_turns"].append({"status": "complete"})
    elif outcome == "successful_later":
        item["model_call_metrics"].append({"status": "complete"})
    elif outcome == "no_evidence":
        call["diagnostics"] = {}
    elif outcome == "auth_error":
        call["diagnostics"]["http_status"] = 401
    elif outcome == "missing_tools":
        del item["tool_trace"]
    assert service_retry_evidence(item) is None
