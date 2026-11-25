"""Identify recorded service failures that ended before an answer was delivered."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def service_retry_evidence(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Conservatively support single-turn model-service failures, independent of score.

    A failed call after final_answer never invalidates that delivered answer. Budget,
    parsing and safety outcomes remain part of the original model evaluation.
    """
    if item.get("execution_status") not in {"completed", "failed"}:
        return None
    turns = item.get("runtime_turns") or []
    if len(turns) != 1:
        return None
    turn = turns[0]
    if turn.get("termination_reason") not in {"model_error", "planner_blocked"}:
        return None
    if turn.get("status") not in {"failed", "partial"}:
        return None
    tools = item.get("tool_trace")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if tool.get("tool") == "final_answer" and tool.get("status") == "ok":
            return None
    if turn.get("attachments") or item.get("predicted_media"):
        return None
    calls = item.get("model_call_metrics") or []
    if not calls:
        return None
    call = calls[-1]
    if (
        call.get("status") != "error"
        or call.get("error_type") != "ModelServiceError"
        or call.get("call_type") not in {"planner", "agent"}
        or call.get("recovered_by_call_id")
    ):
        return None
    diagnostics = call.get("diagnostics") or {}
    category = diagnostics.get("error_category")
    status = diagnostics.get("http_status")
    if category != "request_timeout" and not (
        category == "service_error" and isinstance(status, int) and (status == 429 or 500 <= status < 600)
    ):
        return None
    return {
        "call_id": call.get("call_id"),
        "error_category": category,
        "http_status": status,
        "termination_reason": turn.get("termination_reason"),
    }
