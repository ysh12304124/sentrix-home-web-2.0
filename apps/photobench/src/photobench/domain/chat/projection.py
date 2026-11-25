"""Project product turn facts into the shared trace/media view, without scoring."""

from __future__ import annotations

from typing import Any

from photobench.domain.evaluation.sources import resolve_sources
from photobench.domain.runs.execution import (
    agent2_trace_from,
    debug_steps_from,
    resolve_predicted_media,
    retrieved_candidate_asset_ids,
    selected_asset_ids,
    summarize_progress,
    traces_from_progress,
)


def turn_item(
    record: dict[str, Any], turn: dict[str, Any], assets: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    result = turn.get("result") or {}
    events = turn.get("public_progress") or []
    bundle = result.get("trace") or {}
    runtime = {
        "index": 0,
        "message": record["message"],
        "turn_id": record.get("turn_id"),
        "answer": result.get("answer") or "",
        "status": turn.get("status"),
        "trace": events,
        "trace_bundle": bundle,
        "agent2_trace": agent2_trace_from(
            bundle, termination_reason=str(result.get("termination_reason") or "")
        ),
        "debug_trace": debug_steps_from(events),
    }
    tools, execution = traces_from_progress([runtime])
    return {
        "index": record["index"],
        "qa_id": record.get("qa_id") or record.get("turn_id"),
        "question": record["message"],
        "answer": runtime["answer"],
        "final_answer": runtime["answer"],
        "execution_status": turn.get("status"),
        "agent_status": turn.get("status"),
        "error": turn.get("error"),
        "termination_reason": result.get("termination_reason"),
        "wall_clock_ms": record.get("wall_clock_ms"),
        "metrics_contract_version": 2,
        "runtime_turns": [runtime],
        "trace_summary": summarize_progress(events),
        "tool_trace": tools,
        "execution_trace": execution,
        "model_call_metrics": [{**m, "conversation_turn": 0} for m in bundle.get("model_calls") or []],
        "predicted_media": resolve_predicted_media(selected_asset_ids(result), assets),
        "retrieved_candidate_media": resolve_predicted_media(retrieved_candidate_asset_ids(result), assets),
        "attachments": result.get("attachments") or [],
        "predicted_source_refs": resolve_sources(selected_asset_ids(result), assets),
        "retrieved_source_refs": resolve_sources(retrieved_candidate_asset_ids(result), assets),
        "read_source_refs": resolve_sources(
            [str(r["asset_id"]) for r in bundle.get("source_reads", [])], assets
        ),
    }
