"""Pure case extraction and aggregation. Counts are observations, not causal claims."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.datasets.ability_taxonomy import ABILITY_FIELDS, ability_dimensions, ability_labels
from photobench.domain.error_analysis.taxonomy import (
    ABILITY_DISPLAY_PROJECTION,
    QUALITY_DIMENSIONS,
    analysis_ability_labels,
    category_value,
    content_subtypes,
    content_types,
    project_case_abilities,
    scenarios,
)
from photobench.domain.runs.analysis import judge_score_for_summary, retrieval_metric_eligible

RULE_VERSION = 11
TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled", "interrupted"}
NORMAL = {"answered", "answered_with_gap", "policy_refusal"}
LABELS = {
    "context_soft_limit": "上下文软预算预检",
    "context_window_exceeded": "上下文窗口超限",
    "context_output_insufficient": "输出空间不足",
    "context_unknown": "上下文不足（子类未知）",
    "output_truncated": "模型输出截断",
    "request_timeout": "模型调用超时",
    "service_error": "模型服务错误",
    "response_json_invalid": "服务响应不可解析",
    "tool_argument_json_invalid": "工具参数JSON非法",
    "model_output_format": "模型输出格式错误",
    "parse_failure_unknown": "解析失败（子类未知）",
    "step_budget_exhausted": "步骤预算耗尽",
    "tool_budget_exhausted": "工具预算耗尽",
    "turn_timeout": "整轮执行超时",
    "tool_error": "工具执行异常",
    "evidence_unavailable": "未取得所需记录",
    "policy_denied": "调用被策略拒绝",
    "tool_timeout": "工具执行超时",
    "tool_internal_unavailable": "工具内部模型不可用（原因未记录）",
    "retrieval_degraded": "检索降级",
    "judge_error": "Judge执行失败",
    "judge_json_invalid": "Judge输出不可解析",
    "judge_inconsistent": "Judge判定不一致",
    "execution_error": "任务执行异常",
}
DIMENSIONS = (
    "ability_family",
    "ability_type",
    "benchmark_group",
    "question_type",
    "task_type",
    "content_type",
    "content_subtype",
    "test_scenario",
    "difficulty",
    "angle",
    "answerability",
    "expected_action",
    "media_type",
    "conversation_type",
)


def saved_labels(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) and value else [str(value or "unknown")]


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def snapshot_annotations(item: dict[str, Any], saved: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recover labels only from this run's frozen QA, never the current dataset."""
    if item.get("ability_taxonomy_version") or not saved or not saved.get("ability_taxonomy_version"):
        return item
    if item.get("qa_id") != saved.get("qa_id"):
        return item
    runtime = item.get("runtime_turns") or []
    conversation = saved.get("conversation") or []
    if conversation and (
        len(runtime) != len(conversation)
        or any(
            (t.get("message") or t.get("question")) != (s.get("message") or s.get("question"))
            for t, s in zip(runtime, conversation, strict=True)
        )
    ):
        return item
    if not conversation and item.get("question") != saved.get("question"):
        return item
    result = {**item, **{k: saved[k] for k in ABILITY_FIELDS if k in saved}}
    if runtime and conversation and len(runtime) == len(conversation):
        result["runtime_turns"] = [
            {**t, **{k: s[k] for k in ABILITY_FIELDS if k in s}}
            for t, s in zip(runtime, conversation, strict=True)
        ]
    result["ability_annotation_source"] = "run_qa_snapshot"
    return result


def classify(call: Mapping[str, Any]) -> tuple[str, str]:
    diagnosis = call.get("diagnostics") or {}
    code = diagnosis.get("error_category")
    if code in LABELS:
        return str(code), "structured"
    name, message = str(call.get("error_type") or ""), str(call.get("error") or "")
    text = (name + " " + message).lower()
    # OutputTruncatedError inherits PromptTooLongError: precedence is deliberate.
    if "outputtruncated" in text or call.get("finish_reason") == "length":
        return "output_truncated", "recorded_type_or_finish_reason"
    if "prompttoolong" in text or "context_length" in text or "context window" in text:
        if "软上限" in text:
            return "context_soft_limit", "legacy_text"
        if "只剩" in text or "不足以写完" in text:
            return "context_output_insufficient", "legacy_text"
        if "超出端点" in text or "exceeds" in text:
            return "context_window_exceeded", "legacy_text"
        return "context_unknown", "recorded_type"
    if "timeout" in text or "timed out" in text or "超时" in text:
        return "request_timeout", "legacy_text"
    if "响应结构不可识别" in text or "jsondecode" in text:
        return "response_json_invalid", "legacy_text"
    return "service_error", "recorded_error"


def trace_records(item: Mapping[str, Any], turns: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Preserve exact source positions even when a legacy root list is absent."""
    root = [
        dict(r, _source_path=f"{key}[{i}]") for i, r in enumerate(item.get(key) or []) if isinstance(r, dict)
    ]
    if root:
        return root
    rows = []
    for ti, turn in enumerate(turns):
        nested = (
            (turn.get("trace_bundle") or {}).get("model_calls", [])
            if key == "model_call_metrics"
            else turn.get("tool_trace", [])
        )
        suffix = "trace_bundle.model_calls" if key == "model_call_metrics" else "tool_trace"
        for i, record in enumerate(nested):
            if isinstance(record, dict):
                rows.append(
                    dict(record, conversation_turn=ti, _source_path=f"runtime_turns[{ti}].{suffix}[{i}]")
                )
    return rows


def case_from_item(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    turns = [x for x in item.get("runtime_turns") or [] if isinstance(x, dict)]
    if not turns:
        turns = [dict(item)]
    events: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    inventory: list[dict[str, Any]] = []

    def add(code: str, source: str, turn: int, step: Any = None, **facts: Any) -> None:
        events.append(
            {
                "event_id": f"i{index}:e{len(events)}",
                "code": code,
                "label": LABELS.get(code, code),
                "source": source,
                "turn": turn,
                "step_id": str(step) if step is not None else None,
                "recovery": "unknown",
                "basis": "recorded_fact",
                **context,
                **facts,
            }
        )

    calls = trace_records(item, turns, "model_call_metrics")
    for pos, call in enumerate(calls):
        turn = int(call.get("conversation_turn") or 0)
        step = call.get("step_id")
        role = str(call.get("call_type") or "agent")
        context = {
            "component": role,
            "call_key": f"i{index}:t{turn}:model{pos}",
            "model_call_id": call.get("call_id"),
            "parent_step_id": call.get("parent_step_id"),
            "evidence_refs": [
                {
                    "kind": "model",
                    "path": call["_source_path"],
                    "turn": turn,
                    "index": pos,
                    "call_id": call.get("call_id"),
                    "step_id": step,
                }
            ],
        }
        if (
            call.get("status") in {"error", "failed"}
            or call.get("error")
            or call.get("finish_reason") == "length"
        ):
            code, basis = classify(call)
            following = next(
                (
                    c
                    for c in calls[pos + 1 :]
                    if c.get("conversation_turn", 0) == turn
                    and str(c.get("call_type") or "agent") == role
                    and c.get("step_id") == step
                    and step is not None
                    and c.get("status") in {"complete", "completed", "ok"}
                ),
                None,
            )
            recovery = "recovered" if following is not None or call.get("recovered_by_call_id") else "unknown"
            add(
                code,
                "model_call_metrics",
                turn,
                step,
                basis=basis,
                role=role,
                call_id=call.get("call_id"),
                attempt=call.get("attempt"),
                message=str(call.get("error") or "")[:2000],
                diagnostics=call.get("diagnostics") or {},
                duration_ms=call.get("duration_ms"),
                recovery=recovery,
                recovered_by=(following or {}).get("call_id")
                or ((following or {}).get("attempt") if following else None),
            )
        if call.get("output_format_error"):
            add(
                "model_output_format",
                "model_call_metrics.output_format_error",
                turn,
                step,
                role=role,
                call_id=call.get("call_id"),
                message=call["output_format_error"],
            )
        for fact in call.get("tool_argument_parse_facts") or []:
            if fact.get("json_object_valid") is False:
                add(
                    "tool_argument_json_invalid",
                    "model_call_metrics",
                    turn,
                    step,
                    role=role,
                    tool_call_id=fact.get("call_id"),
                    call_id=call.get("call_id"),
                )
    tools = trace_records(item, turns, "tool_trace")
    for tool_index, tool in enumerate(tools):
        ti = int(tool.get("conversation_turn") or 0)
        result = tool.get("result") or {}
        failure = result.get("failure_reason") if isinstance(result, dict) else None
        context = {
            "tool_name": tool.get("tool") or tool.get("tool_name") or tool.get("name") or "unknown",
            "tool_call_id": tool.get("call_id") or tool.get("tool_call_id"),
            "parent_step_id": tool.get("parent_step_id"),
            "component": "tool",
            "failure_reason": failure
            or (
                result.get("degraded_reason") if isinstance(result, dict) and result.get("degraded") else None
            ),
            "call_key": f"i{index}:t{ti}:tool{tool_index}",
            "evidence_refs": [
                {
                    "kind": "tool",
                    "path": tool["_source_path"],
                    "turn": ti,
                    "index": tool_index,
                    "call_id": tool.get("call_id") or tool.get("tool_call_id"),
                    "step_id": tool.get("step_id"),
                }
            ],
        }
        inventory.append({"turn": ti, "tool_name": context["tool_name"], "call_key": context["call_key"]})
        if tool.get("status") in {"error", "failed", "denied", "partial"} or failure:
            add(
                "evidence_unavailable"
                if failure in {"no_text_detected", "photo_not_found", "asset_not_found"}
                else "policy_denied"
                if tool.get("status") == "denied"
                else "tool_timeout"
                if any(
                    w in str(tool.get("error") or tool.get("text") or "").lower()
                    for w in ("timed out", "timeout", "超时")
                )
                else "tool_error",
                "tool_trace",
                ti,
                tool.get("step_id"),
                role="tool",
                tool=tool.get("tool"),
                message=str(tool.get("text") or tool.get("error") or "")[:2000],
            )
        if isinstance(result, dict):
            internal = result.get("model_call_observations") or []
            if result.get("failure_reason") == "model_unavailable" and not internal:
                add(
                    "tool_internal_unavailable",
                    "tool_trace.result",
                    ti,
                    tool.get("step_id"),
                    role="tool_internal",
                    component="tool_internal",
                )
            for call in internal:
                if call.get("status") == "error":
                    code, basis = classify(call)
                    add(
                        code,
                        "tool_trace.result.model_call_observations",
                        ti,
                        tool.get("step_id"),
                        role="tool_internal",
                        component="tool_internal",
                        model_call_id=call.get("call_id"),
                        call_id=call.get("call_id"),
                        basis=basis,
                        diagnostics=call.get("diagnostics") or {},
                        message=str(call.get("error") or "")[:2000],
                        recovery="recovered" if call.get("recovered_by_call_id") else "unknown",
                    )
        if isinstance(result, dict) and result.get("degraded"):
            add(
                "retrieval_degraded",
                "tool_trace",
                ti,
                tool.get("step_id"),
                role="tool",
                message=str(result.get("degraded_reason") or ""),
            )
    context = {}
    turn_rows: list[dict[str, Any]] = []
    for ti, turn_record in enumerate(turns):
        reason = str(turn_record.get("termination_reason") or "")
        trace = turn_record.get("trace") or []
        formatted = False
        for ei, event in enumerate(trace):
            payload = event.get("payload") or {}
            guard_code = payload.get("code")
            if guard_code == "textual_tool_call":
                formatted = True
                add(
                    "model_output_format",
                    "runtime_turns.trace",
                    ti,
                    payload.get("step_id"),
                    event_index=ei,
                    message=str(event.get("text") or ""),
                    recovery="recovered" if payload.get("recovered_by_call_id") else "unknown",
                )
        terminal_codes = {
            "parse_failure": "parse_failure_unknown",
            "step_budget_exhausted": "step_budget_exhausted",
            "tool_budget_exhausted": "tool_budget_exhausted",
            "timeout": "turn_timeout",
            "model_error": "execution_error",
            "guard_blocked": "execution_error",
            "planner_blocked": "execution_error",
        }
        if reason in terminal_codes and not (reason == "parse_failure" and formatted):
            add(
                terminal_codes[reason],
                "runtime_turns.termination_reason",
                ti,
                recovery="not_recovered",
                role="agent",
            )
        task = turn_record.get("task_judge") or (
            (item.get("task_judges") or [])[ti] if ti < len(item.get("task_judges") or []) else {}
        )
        if not task and len(turns) == 1:
            task = item.get("task_judge") or {}
        expected = turn_record.get("expected_action") or (
            item.get("expected_action") if len(turns) == 1 else None
        )
        mixed_valid = expected != "answer_and_refuse" or task.get("behavior_rule_version") == 2
        annotation = {**item, **turn_record} if len(turns) == 1 else turn_record
        turn_content = content_types(saved_labels(annotation.get("task_type")))
        turn_subtype = content_subtypes(
            str(annotation.get("task_type")), str(annotation.get("question_type"))
        )
        turn_rows.append(
            {
                **ability_dimensions(annotation),
                "ability_taxonomy_version": saved_labels(annotation.get("ability_taxonomy_version")),
                "content_type": turn_content,
                "content_subtype": turn_subtype,
                "difficulty": saved_labels(annotation.get("difficulty")),
                "answerability": saved_labels(annotation.get("answerability")),
                "turn": ti,
                "question": str(turn_record.get("message") or turn_record.get("question") or "")[:8000],
                "answer": str(turn_record.get("answer") or "")[:8000],
                "reference_answer": str(turn_record.get("reference_answer") or "")[:8000],
                "context_turn_count": turn_record.get("context_turn_count"),
                "conversation_id": turn_record.get("conversation_id"),
                "turn_id": turn_record.get("turn_id"),
                "termination_reason": reason,
                "recorded_expected": expected,
                "recorded_actual": task.get("actual_action"),
                "expected": expected
                if expected in {"answer", "refuse", "clarify", "answer_and_refuse"}
                else None,
                "actual": task.get("actual_action")
                if mixed_valid
                and task.get("judge_status") != "failed"
                and task.get("actual_action") in {"answer", "refuse", "clarify", "answer_and_refuse", "none"}
                else None,
                "behavior_correct": task.get("correct")
                if mixed_valid
                and isinstance(task.get("correct"), bool)
                and task.get("judge_status") != "failed"
                and expected in {"answer", "refuse", "clarify", "answer_and_refuse"}
                and task.get("actual_action") in {"answer", "refuse", "clarify", "answer_and_refuse", "none"}
                else None,
                "judge_reason": str(task.get("reason") or "")[:2000],
                "test_scenarios": scenarios(
                    {"question_type": item.get("question_type"), **dict(turn_record)}
                ),
                "score": judge_score_for_summary(turn_record.get("judge")),
            }
        )
        judges = {
            "answer": turn_record.get("judge"),
            "behavior": turn_record.get("task_judge"),
            "evidence": turn_record.get("evidence_judge"),
        }
        if len(turns) == 1:
            judges = {
                "answer": item.get("judge"),
                "behavior": item.get("task_judge"),
                "evidence": item.get("evidence_judge"),
            }
        for role, judge in judges.items():
            if not isinstance(judge, dict):
                continue
            observations = judge.get("call_observations") or []
            for ci, call in enumerate(observations):
                if call.get("status") != "error":
                    continue
                code, basis = classify(call)
                add(
                    code,
                    role + "_judge.call_observations",
                    ti,
                    role="judge",
                    component="judge",
                    call_key=f"i{index}:t{ti}:{role}_judge{ci}",
                    model_call_id=call.get("call_id"),
                    evidence_refs=[
                        {
                            "kind": "judge",
                            "turn": ti,
                            "index": ci,
                            "path": ("" if len(turns) == 1 else f"runtime_turns[{ti}].")
                            + {"answer": "judge", "behavior": "task_judge", "evidence": "evidence_judge"}[
                                role
                            ]
                            + f".call_observations[{ci}]",
                        }
                    ],
                    basis=basis,
                    call_id=call.get("call_id"),
                    diagnostics=call.get("diagnostics") or {},
                    message=str(call.get("error") or "")[:2000],
                    recovery="recovered" if call.get("recovered_by_call_id") else "unknown",
                    recovered_by=call.get("recovered_by_call_id"),
                )
            if (
                judge.get("consistency_status") == "inconsistent"
                or judge.get("reason") == "judge_inconsistent_score_reason"
            ):
                add("judge_inconsistent", role + "_judge", ti, role="judge")
            elif judge.get("judge_status") == "failed" or str(judge.get("reason") or "").startswith(
                "judge_error"
            ):
                text = str(judge.get("reason") or "")
                code = (
                    "judge_json_invalid"
                    if any(x in text.lower() for x in ("json", "parse", "解析"))
                    else "judge_error"
                )
                if not any(c.get("status") == "error" for c in observations):
                    add(code, role + "_judge", ti, role="judge", message=text[:2000])
    for event in events:
        code = event["code"]
        event.setdefault("component", event.get("role") or "agent")
        event["issue_kind"] = (
            "evidence_unavailable"
            if code == "evidence_unavailable"
            else "policy"
            if code == "policy_denied"
            else "degraded"
            if code == "retrieval_degraded"
            else "timeout"
            if "timeout" in code
            else "budget"
            if "budget" in code or code.startswith("context_")
            else "invalid_format"
            if "json" in code or "format" in code or "parse" in code
            else "service_error"
        )
        event["related_event_ids"] = [
            e["event_id"]
            for e in events
            if event.get("call_key") and e.get("call_key") == event["call_key"] and e is not event
        ]
    score = judge_score_for_summary(item.get("judge"))
    reasons = [t["termination_reason"] for t in turn_rows]
    status = (
        "cancelled"
        if any(r == "cancelled" for r in reasons)
        else "failed"
        if any(r and r not in NORMAL for r in reasons)
        else "normal"
        if all(r in NORMAL for r in reasons)
        else "unknown"
    )
    gt_fields = (
        "retrieval_media_refs",
        "retrieval_image_ids",
        "retrieval_video_ids",
        "answer_evidence_media_refs",
        "reference_answer",
        "answer_claims",
        "answer_evidence_image_ids",
        "answer_evidence_video_ids",
        "required_evidence_sources",
    )

    def canonical_gt(record: Mapping[str, Any]) -> dict[str, Any]:
        value = {k: record[k] for k in gt_fields if k in record}
        for prefix in ("retrieval", "answer_evidence"):
            if prefix + "_media_refs" in value:
                value.pop(prefix + "_image_ids", None)
                value.pop(prefix + "_video_ids", None)
        if value.get("answer_claims"):
            value["answer_claims"] = [
                {k: v for k, v in claim.items() if k not in {"evidence_image_ids", "evidence_video_ids"}}
                for claim in value["answer_claims"]
            ]
        return value

    gt = canonical_gt(item)
    turn_gt = [canonical_gt(t) for t in turns]
    if any(turn_gt):
        gt["turns"] = turn_gt
    media = {str(x.get("media_type")) for x in item.get("retrieval_media_refs") or [] if isinstance(x, dict)}
    if item.get("retrieval_image_ids"):
        media.add("image")
    if item.get("retrieval_video_ids"):
        media.add("video")
    source_labels = {
        k: saved_labels(item.get(k))
        for k in DIMENSIONS
        if k not in {"content_type", "content_subtype", "test_scenario"}
    }
    dimension_labels = {k: list(v) for k, v in source_labels.items()}
    dimension_labels = {
        k: list(dict.fromkeys(category_value(k, value) for value in values))
        for k, values in dimension_labels.items()
    }
    dimension_labels["angle"] = list(
        dict.fromkeys(
            "unknown" if v in {"event_summary", "enumeration", "media_retrieval"} else v
            for v in dimension_labels["angle"]
        )
    )
    dimension_labels["content_type"] = content_types(dimension_labels["task_type"])
    dimension_labels["content_subtype"] = content_subtypes(
        str(item.get("task_type")), str(item.get("question_type"))
    )
    dimension_labels["test_scenario"] = sorted({v for t in turn_rows for v in t["test_scenarios"]})
    dimension_labels.update(ability_dimensions(item))
    dimension_labels["ability_taxonomy_version"] = saved_labels(item.get("ability_taxonomy_version"))
    dimensions = {k: " / ".join(values) for k, values in dimension_labels.items()}
    dimensions.update(
        media_type="mixed" if len(media) > 1 else next(iter(media), "unknown"),
        conversation_type="multi" if len(turns) > 1 else "single",
    )
    dimension_labels.update({k: [dimensions[k]] for k in ("media_type", "conversation_type")})
    retrieval = item.get("candidate_retrieval_counts") or {}
    delivery = item.get("media_retrieval_counts") or {}
    signals = []
    content_applicable = retrieval_metric_eligible(item) and dimension_labels["content_type"] != [
        "not_applicable"
    ]
    if not content_applicable:
        retrieval, delivery = {}, {}
    if retrieval.get("gt", 0) > 0:
        if retrieval.get("matched") == 0:
            signals.append("candidate_miss")
        elif delivery.get("gt", 0) > 0 and delivery.get("matched") == 0:
            signals.append("candidate_hit_not_delivered")
        if delivery.get("matched", 0) > 0 and score in (0, 1):
            signals.append("delivered_low_score")
    missing = []
    if score is None:
        missing.append("quality_score")
    if not calls and not item.get("execution_trace") and not any(t.get("trace") for t in turns):
        missing.append("trace")
    if not any(gt.get(k) for k in gt):
        missing.append("gt")
    if content_applicable and not any(
        item.get(k) for k in ("retrieval_media_refs", "retrieval_image_ids", "retrieval_video_ids")
    ):
        missing.append("retrieval_gt")
    if not item.get("question_type") and dimension_labels["ability_type"] == ["unknown"]:
        missing.append("question_type")
    if any(t["behavior_correct"] is None for t in turn_rows):
        missing.append("behavior")
    judge_records = turns if len(turns) > 1 else [item]
    if any(
        t.get(key) and t[key].get("reason") != "not_applicable" and "call_observations" not in t[key]
        for t in judge_records
        for key in ("judge", "task_judge", "evidence_judge")
    ):
        missing.append("judge_call_records")
    if any(
        (t.get("tool") or t.get("name")) == "inspect_photo"
        and "model_call_observations" not in (t.get("result") or {})
        for t in tools
    ):
        missing.append("tool_model_call_records")
    agent_limits = []
    for record in turns:
        budget = (record.get("trace_bundle") or {}).get("budget") or {}
        if budget.get("max_steps") is not None and budget.get("max_tool_calls") is not None:
            agent_limits.append({k: budget[k] for k in ("max_steps", "max_tool_calls")})
    context_windows = sorted(
        {
            int((c.get("diagnostics") or {})["context_window_tokens"])
            for c in calls
            if (c.get("diagnostics") or {}).get("context_window_tokens")
        }
    )
    judge_definitions = []
    for record in turns:
        for key in ("judge", "task_judge", "evidence_judge"):
            judge = (item.get(key) if len(turns) == 1 else record.get(key)) or {}
            payload = (judge.get("attempts") or [{}])[0].get("input") or judge.get("input") or {}
            if payload:
                judge_definitions.append(
                    fingerprint(
                        {
                            "role": key,
                            "model": payload.get("model"),
                            "temperature": payload.get("temperature"),
                            "max_tokens": payload.get("max_tokens"),
                            "system": [
                                m.get("content")
                                for m in payload.get("messages", [])
                                if m.get("role") == "system"
                            ],
                        }
                    )
                )
    case = {
        "judge_definitions": sorted(set(judge_definitions)),
        "agent_limits": agent_limits,
        "context_windows": context_windows,
        "index": index,
        "qa_id": str(item.get("qa_id") or ""),
        "question": str(
            item.get("display_question")
            or ((turns[0].get("message") or turns[0].get("question")) if len(turns) > 1 else None)
            or item.get("question")
            or ""
        )[:8000],
        "reference_answer": str(item.get("reference_answer") or "")[:8000],
        "answer": str(item.get("answer") or "")[:8000],
        "text_truncated": any(
            len(str(item.get(k) or "")) > 8000 for k in ("question", "answer", "reference_answer")
        ),
        "identity": fingerprint(
            {
                "question": item.get("question"),
                "gt": gt,
                "conversation": [{k: t.get(k) for k in ("message", "reference_answer")} for t in turns],
                "context_mode": item.get("conversation_context_mode"),
            }
        ),
        "gt_fingerprint": fingerprint(gt),
        "annotation_fingerprint": fingerprint(
            {
                "item": {
                    k: item.get(k)
                    for k in (
                        *ABILITY_FIELDS,
                        "task_type",
                        "question_type",
                        "expected_action",
                        "answerability",
                        "difficulty",
                        "injection",
                    )
                },
                "turns": [
                    {
                        k: t.get(k)
                        for k in (
                            *ABILITY_FIELDS,
                            "task_type",
                            "question_type",
                            "expected_action",
                            "answerability",
                            "difficulty",
                            "injection",
                        )
                    }
                    for t in turns
                ],
            }
        ),
        "score": score,
        "execution": status,
        "dimensions": dimensions,
        "dimension_labels": dimension_labels,
        "ability_taxonomy_version": item.get("ability_taxonomy_version"),
        "ability_annotation_source": item.get("ability_annotation_source")
        or ("run_item" if item.get("ability_taxonomy_version") else "unrecorded"),
        "ability_labels": ability_labels([str(item.get("ability_taxonomy_version"))]),
        "source_labels": source_labels,
        "events": events,
        "turns": turn_rows,
        "signals": signals,
        "missing": missing,
        "model_calls": len(calls),
        "tool_model_calls": sum(
            len((t.get("result") or {}).get("model_call_observations") or []) for t in tools
        ),
        "judge_calls": sum(
            len((t.get(key) or {}).get("call_observations") or [])
            for t in (turns if len(turns) > 1 else [item])
            for key in ("judge", "task_judge", "evidence_judge")
        ),
        "tool_calls": len(tools),
        "tool_inventory": inventory,
        "tool_json_total": sum(len(c.get("tool_argument_parse_facts") or []) for c in calls),
        "tool_json_invalid": sum(e["code"] == "tool_argument_json_invalid" for e in events),
        "retrieval_counts": {"candidate": retrieval, "delivery": delivery},
    }
    return project_case_abilities(case)


class Aggregator:
    """Only compact cases are retained by storage; aggregate uses bounded counters."""

    def __init__(self) -> None:
        self.total = 0
        self.ability_versions: set[str] = set()
        self.counts: Counter[str] = Counter()
        self.groups: dict[str, dict[str, Counter[str]]] = {k: {} for k in QUALITY_DIMENSIONS}
        self.turn_groups: dict[str, dict[str, Counter[str]]] = {
            k: {}
            for k in (
                "ability_family",
                "ability_taxonomy_version",
                "ability_type",
                "benchmark_group",
                "content_type",
                "content_subtype",
                "difficulty",
                "answerability",
            )
        }
        self.annotation_hash = hashlib.sha256()
        self.behavior_groups: dict[str, Counter[str]] = {}
        self.behavior_scenarios: dict[str, Counter[str]] = {}
        self.errors: dict[str, Counter[str]] = {}
        self.matrix: Counter[tuple[str, str]] = Counter()
        self.heatmap: Counter[tuple[str, str]] = Counter()
        self.ability_heatmap: Counter[tuple[str, str]] = Counter()
        self.gt_hash = hashlib.sha256()
        self.judge_definitions: set[str] = set()
        self.agent_limits: dict[str, dict[str, Any]] = {}
        self.context_windows: set[int] = set()
        self.partitions: dict[str, dict[str, Counter[str]]] = {"qa": {}, "turn": {}}
        self.group_errors: dict[tuple[str, str, str, str], int] = Counter()

    def add(self, case: dict[str, Any]) -> None:
        self.total += 1
        if case.get("ability_taxonomy_version"):
            self.ability_versions.add(case["ability_taxonomy_version"])
        self.judge_definitions.update(case["judge_definitions"])
        self.agent_limits.update({fingerprint(v): v for v in case["agent_limits"]})
        self.context_windows.update(case["context_windows"])
        self.gt_hash.update((case["qa_id"] + case["gt_fingerprint"]).encode())
        self.annotation_hash.update((case["qa_id"] + case["annotation_fingerprint"]).encode())
        self.counts["score_" + str(case["score"])] += 1
        self.counts["execution_" + case["execution"]] += 1
        for key in (
            "model_calls",
            "tool_model_calls",
            "judge_calls",
            "tool_calls",
            "tool_json_total",
            "tool_json_invalid",
        ):
            self.counts[key] += case[key]
        if case["events"]:
            self.counts["affected_items"] += 1
        for m in case["missing"]:
            self.counts["missing_" + m] += 1
        for signal in case["signals"]:
            self.counts[signal] += 1
        for unit, record in [("qa", case["dimension_labels"]), *[("turn", t) for t in case["turns"]]]:
            if record["ability_type"] not in (["unknown"], ["not_applicable"]):
                self.counts[unit + "_ability_valid"] += 1
                score = case["score"] if unit == "qa" else record["score"]
                self.counts[unit + "_ability_score_" + str(score)] += 1
            if record["ability_family"] not in (["unknown"], ["not_applicable"]):
                self.counts[unit + "_ability_family_valid"] += 1
                score = case["score"] if unit == "qa" else record["score"]
                self.counts[unit + "_ability_family_score_" + str(score)] += 1
            for family in record["ability_family"]:
                if family not in {"unknown", "not_applicable"}:
                    score = case["score"] if unit == "qa" else record["score"]
                    self.counts[f"{unit}_ability_family_{family}_score_{score}"] += 1
        for dim in QUALITY_DIMENSIONS:
            values = case["dimension_labels"][dim]
            for value in sorted(set(values)):
                row = self.groups[dim].setdefault(value, Counter())
                row["total"] += 1
                row["score_" + str(case["score"])] += 1
                row["failed"] += case["execution"] == "failed"
                row["affected"] += bool(case["events"])
        # Inspect each membership set: equal column totals alone cannot prove a partition.
        for unit, record, events in [
            ("qa", case["dimension_labels"], case["events"]),
            *[("turn", t, [e for e in case["events"] if e["turn"] == t["turn"]]) for t in case["turns"]],
        ]:
            dimensions = QUALITY_DIMENSIONS if unit == "qa" else tuple(self.turn_groups)
            for dim in dimensions:
                values = set(record.get(dim) or ["unknown"])
                stats = self.partitions[unit].setdefault(dim, Counter())
                stats["total"] += 1
                stats["multi_labeled"] += len(values) > 1
                stats["unknown"] += bool(values & {"unknown", "pending_review"})
                stats["not_applicable"] += "not_applicable" in values
                stats["labeled"] += bool(values - {"unknown", "pending_review", "not_applicable"})
                for value in values:
                    for code in {e["code"] for e in events}:
                        group_key = (unit, dim, value, code)
                        self.group_errors[group_key] = self.group_errors.get(group_key, 0) + 1
        for code in sorted({e["code"] for e in case["events"]}):
            es = [e for e in case["events"] if e["code"] == code]
            row = self.errors.setdefault(code, Counter())
            row["events"] += len(es)
            row["affected_items"] += 1
            row["final_failed_items"] += case["execution"] == "failed"
            row["recovered_items"] += all(e["recovery"] == "recovered" for e in es)
            for value in set(case["dimension_labels"]["content_type"]):
                self.heatmap[value, code] += 1
            for value in set(case["dimension_labels"]["ability_family"]):
                self.ability_heatmap[value, code] += 1
        if case["events"] and all(e["recovery"] == "recovered" for e in case["events"]):
            self.counts["fully_recovered_items"] += 1
        self.counts["events"] += len(case["events"])
        for channel, counts in case["retrieval_counts"].items():
            if counts.get("gt", 0) > 0:
                self.counts[channel + "_valid_items"] += 1
                for name in ("gt", "matched", "predicted"):
                    self.counts[channel + "_" + name] += counts.get(name, 0)
        for turn in case["turns"]:
            for dim, groups in self.turn_groups.items():
                for value in set(turn[dim]):
                    row = groups.setdefault(value, Counter())
                    row["total"] += 1
                    row["score_" + str(turn["score"])] += 1
                    row["affected"] += any(e["turn"] == turn["turn"] for e in case["events"])
                    row["failed"] += bool(
                        turn["termination_reason"] and turn["termination_reason"] not in NORMAL
                    )
            self.counts["turns"] += 1
            self.counts["turn_score_" + str(turn["score"])] += 1
            expected, actual = turn["expected"], turn["actual"]
            self.matrix[str(expected or "unknown"), str(actual or "unknown")] += 1
            valid = isinstance(turn["behavior_correct"], bool)
            for groups, values in (
                (self.behavior_groups, [str(expected or "unknown")]),
                (self.behavior_scenarios, turn["test_scenarios"]),
            ):
                for value in set(values):
                    row = groups.setdefault(value, Counter())
                    row["total"] += 1
                    row["valid"] += valid
                    row["correct"] += valid and turn["behavior_correct"] is True
                    row["incorrect"] += valid and turn["behavior_correct"] is False
                    row["missing"] += not valid
            if valid:
                self.counts["behavior_valid"] += 1
                self.counts["behavior_correct"] += turn["behavior_correct"]

    def report(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "rule_version": RULE_VERSION,
            "ability_taxonomy_versions": sorted(self.ability_versions),
            "ability_labels": analysis_ability_labels(self.ability_versions),
            "ability_display_projection": ABILITY_DISPLAY_PROJECTION,
            "source": {
                **metadata,
                "judge_definition_fingerprints": sorted(self.judge_definitions),
                "agent_limits": [self.agent_limits[k] for k in sorted(self.agent_limits)],
                "context_windows": sorted(self.context_windows),
            },
            "gt_fingerprint": self.gt_hash.hexdigest(),
            "annotation_fingerprint": self.annotation_hash.hexdigest(),
            "group_partitions": {
                unit: {
                    dim: {
                        **dict(stats),
                        "exclusive": stats["multi_labeled"] == 0,
                        "exhaustive": stats["total"] > 0,
                    }
                    for dim, stats in dimensions.items()
                }
                for unit, dimensions in self.partitions.items()
            },
            "group_errors": [
                {"unit": unit, "dimension": dim, "value": value, "code": code, "count": n}
                for (unit, dim, value, code), n in sorted(self.group_errors.items())
            ],
            "turn_groups": {
                k: [{"value": v, **dict(c)} for v, c in rows.items()] for k, rows in self.turn_groups.items()
            },
            "item_count": self.total,
            "counts": dict(self.counts),
            "denominators": {
                "qa": self.total,
                "ability_valid": self.counts["qa_ability_valid"],
                "ability_family_valid": self.counts["qa_ability_family_valid"],
                "turn_ability_family_valid": self.counts["turn_ability_family_valid"],
                "turn_ability_valid": self.counts["turn_ability_valid"],
                "content_valid": sum(
                    c["total"]
                    for v, c in self.groups["content_type"].items()
                    if v not in {"unknown", "not_applicable", "pending_review"}
                ),
                "content_not_applicable": self.groups["content_type"].get("not_applicable", Counter())[
                    "total"
                ],
                "content_pending": self.groups["content_type"].get("pending_review", Counter())["total"],
                "content_missing": self.groups["content_type"].get("unknown", Counter())["total"],
                "quality_valid": self.total - self.counts["score_None"],
                "quality_missing": self.counts["score_None"],
                "turns": self.counts["turns"],
                "turn_quality_valid": self.counts["turns"] - self.counts["turn_score_None"],
                "behavior_valid": self.counts["behavior_valid"],
                "behavior_missing": self.counts["turns"] - self.counts["behavior_valid"],
                "model_calls": self.counts["model_calls"],
                "tool_model_calls": self.counts["tool_model_calls"],
                "judge_calls": self.counts["judge_calls"],
                "tool_calls": self.counts["tool_calls"],
                "tool_json_recorded": self.counts["tool_json_total"],
            },
            "groups": {
                k: [{"value": v, **dict(c)} for v, c in rows.items()] for k, rows in self.groups.items()
            },
            "behavior_groups": [{"value": v, **dict(c)} for v, c in self.behavior_groups.items()],
            "behavior_scenarios": [{"value": v, **dict(c)} for v, c in self.behavior_scenarios.items()],
            "errors": [
                {"code": k, "label": LABELS.get(k, k), **dict(v)}
                for k, v in sorted(self.errors.items(), key=lambda kv: -kv[1]["affected_items"])
            ],
            "behavior_matrix": [
                {"expected": e, "actual": a, "count": n} for (e, a), n in self.matrix.items()
            ],
            "heatmap": [{"content_type": q, "code": c, "count": n} for (q, c), n in self.heatmap.items()],
            "ability_heatmap": [
                {"ability_family": q, "code": c, "count": n} for (q, c), n in self.ability_heatmap.items()
            ],
            "definitions": [
                "能力标签按明确版本读取；题级是回答轮主能力并集，轮级采用本轮能力与得分。旧内容标签仍用原口径。",
                "抗幻觉H/H1、抗注入攻击I/I1的两级名称相同；v2已记录的H/I安全题在分析视图中同样归入H1/I1，原始标签快照保留。v1保留原三类口径，澄清轮不计二级，支撑能力不计主标签。",
                "一级、二级能力分母分别按题或轮去重；多标签可重叠，历史未记录不推断。混合版本报告显式展示各版名称，标签代码汇总不得解释为同口径比较。",
                "不适用、待核定、未记录题型单列，不进入有效内容题型的占比分母。",
                "证据指纹与标签指纹分开保存；标签变化不会标成GT变化，标签不同不提供改善汇总。",
                "内容类型仅取明确的内容任务标签；行为测试、预期动作、测试场景不作为内容类型。未标注内容类型不推断。",
                "0/1/2分表示任务完成质量（含回答、拒答与澄清）；行为符合率独立使用逐轮行为判定，不从质量分推算。",
                "行为覆盖以全部对话轮为分母（含预期未知）；行为符合率以有效行为判定轮数为分母。案例列表按题去重。",
                "异常事件数、受影响题数、最终执行失败题数分别统计；错误类别及多标签题型/角度可重叠，不能相加。",
                "恢复仅根据同轮同一步重试成功或明确恢复事件判定，未知不计成功。",
                "分数分母为有效Judge题数；行为分母为有效逐轮行为判定数；未知与未覆盖不补零。",
                "检索/交付信号用于定位，不作确定因果结论。",
                "调用分母统计已保存的调用记录；历史未采集的内部调用数量保持未知，见数据缺口。",
            ],
        }


def matches(case: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    from photobench.domain.error_analysis.query import select_case

    return select_case(case, filters) is not None


def compare_reports(
    left: dict[str, Any], right: dict[str, Any], a: Sequence[dict[str, Any]], b: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    counts_a, counts_b = Counter(x["qa_id"] for x in a), Counter(x["qa_id"] for x in b)
    one = {x["qa_id"]: x for x in a if x["qa_id"] and counts_a[x["qa_id"]] == 1}
    two = {x["qa_id"]: x for x in b if x["qa_id"] and counts_b[x["qa_id"]] == 1}
    differences = []
    for k in (
        "judge",
        "judge_definition_fingerprints",
        "budgets",
        "agent_limits",
        "context_windows",
        "model",
        "device",
        "build_revision",
        "qa_snapshot",
    ):
        if left["source"].get(k) != right["source"].get(k):
            differences.append(k)
    comparable = (
        not any(
            k in differences
            for k in ("judge", "judge_definition_fingerprints", "budgets", "agent_limits", "context_windows")
        )
        and bool(left["source"].get("judge"))
        and bool(right["source"].get("judge"))
        and bool((left["source"].get("budgets") or {}).get("execution"))
        and bool((right["source"].get("budgets") or {}).get("execution"))
        and all(
            source.get("agent_limits")
            and source.get("context_windows")
            and source.get("judge_definition_fingerprints")
            for source in (left["source"], right["source"])
        )
    )
    rows = []
    for qa in sorted(one.keys() | two.keys()):
        if counts_a[qa] > 1 or counts_b[qa] > 1:
            continue
        x, y = one.get(qa), two.get(qa)
        state = (
            "unmatched"
            if x is None or y is None
            else "changed"
            if x["identity"] != y["identity"]
            else "paired"
        )
        delta = (
            y["score"] - x["score"]
            if state == "paired"
            and x is not None
            and y is not None
            and x["score"] is not None
            and y["score"] is not None
            else None
        )
        rows.append(
            {
                "qa_id": qa,
                "pairing": state,
                "score_delta": delta,
                "execution_changed": bool(x and y and x["execution"] != y["execution"]),
                "errors_added": sorted(
                    {e["code"] for e in (y or {}).get("events", [])}
                    - {e["code"] for e in (x or {}).get("events", [])}
                ),
                "errors_removed": sorted(
                    {e["code"] for e in (x or {}).get("events", [])}
                    - {e["code"] for e in (y or {}).get("events", [])}
                ),
                "gt_changed": bool(x and y and x["gt_fingerprint"] != y["gt_fingerprint"]),
                "annotations_changed": bool(
                    x and y and x.get("annotation_fingerprint") != y.get("annotation_fingerprint")
                ),
                "quality_change": ("improved" if delta > 0 else "regressed" if delta < 0 else "unchanged")
                if delta is not None
                else "unknown",
                "left": x,
                "right": y,
            }
        )
    duplicates = sorted(
        k for k in counts_a.keys() | counts_b.keys() if not k or counts_a[k] > 1 or counts_b[k] > 1
    )
    if any(row["annotations_changed"] for row in rows if row["pairing"] == "paired"):
        differences.append("annotations")
        comparable = False
    return {
        "sources": {"left": left["source"], "right": right["source"]},
        "differences": differences,
        "comparable": comparable,
        "duplicates": duplicates,
        "pairing_counts": dict(Counter(x["pairing"] for x in rows)),
        "rows": rows,
        "quality_changes": dict(Counter(x["quality_change"] for x in rows if x["pairing"] == "paired"))
        if comparable
        else None,
        "missing_configuration": [
            k
            for k in ("judge", "judge_definition_fingerprints", "agent_limits", "context_windows")
            if not left["source"].get(k) or not right["source"].get(k)
        ],
        "note": "仅配对题比较；设备/模型变化不作因果归因。Judge或预算不同/未知时不提供改善汇总。",
    }
