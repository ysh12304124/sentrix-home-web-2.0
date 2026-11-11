"""Deterministic process observations, separate from scoring and Agent outcomes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from photobench.domain.evaluation.media import tool_media_aliases
from photobench.domain.evaluation.profiles import evaluation_turn, stable_media_key

VERSION = "answer-process-v1"
LABELS = {
    "candidate_gap": "目标未进入候选或仅部分命中",
    "not_inspected": "候选命中但未记录进一步取证",
    "no_text": "工具未读出文字",
    "tool_parameter": "工具参数限制",
    "budget_limit": "步骤、次数或时间限制",
    "tool_limit": "其他工具限制或异常",
    "semantic_gap": "回答内容或任务要求待复核",
    "delivery_gap": "正式交付为空或不全",
    "record_missing": "记录不足",
}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def media_keys(refs: Any) -> set[str]:
    return {
        stable_media_key(r)
        for r in refs or []
        if isinstance(r, Mapping) and r.get("media_type") in {"image", "video"} and r.get("media_id")
    }


def item_process(
    row: Mapping[str, Any], item: Mapping[str, Any], scores: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """No GT-based identity inference, rescoring, network, or mutation."""
    turns = item.get("runtime_turns") or [item]
    tools = item.get("tool_trace")
    # An explicit tool list can prove an empty trace; missing/debug-only traces cannot.
    tools_known = isinstance(tools, list)
    calls = [
        (f"tool_trace[{i}]", c, c.get("conversation_turn", 0 if len(turns) == 1 else None))
        for i, c in enumerate(tools or [])
        if isinstance(c, Mapping)
    ]
    if tools_known and any(index is None for _, _, index in calls):
        tools_known = False
    if not tools_known:
        calls = [
            (f"runtime_turns[{ti}].tool_trace[{i}]", c, ti)
            for ti, t in enumerate(turns)
            for i, c in enumerate(t.get("tool_trace") or [])
        ]
        tools_known = all(isinstance(t.get("tool_trace"), list) for t in turns)
    candidates = item.get("retrieved_candidate_media")
    targets = media_keys((row.get("evaluation") or {}).get("target_media_refs"))
    # Frozen list definitions call this field target_media; retrieval evidence is independent.
    definition = row.get("evaluation") or item.get("evaluation") or {}
    targets = targets or media_keys(definition.get("target_media"))
    retrieval = media_keys(row.get("retrieval_media_refs") or item.get("retrieval_media_refs"))
    is_list = definition.get("scorer") in {"media_set", "atm_list"}
    if is_list and not targets:
        raw_target = definition.get("target") or []
        targets = {str(k) for k in raw_target if isinstance(k, str) and k.startswith(("image:", "video:"))}
    findings: list[dict[str, Any]] = []
    chain = []

    def add(category: str, status: str, message: str, sources: list[str], **facts: Any) -> None:
        findings.append(
            {
                "category": category,
                "label": LABELS[category],
                "status": status,
                "message": message,
                "sources": sources,
                **facts,
            }
        )

    for ti, turn in enumerate(turns):
        prefix = f"runtime_turns[{ti}]" if item.get("runtime_turns") else "item"
        # Root targets belong to the frozen evaluation turn only. Earlier turns
        # need their own explicit definition; never project the final GT backwards.
        evaluated = ti == evaluation_turn(row)
        frozen_turns = row.get("conversation") or []
        frozen = frozen_turns[ti] if ti < len(frozen_turns) else {}
        turn_retrieval = retrieval if evaluated else media_keys(frozen.get("retrieval_media_refs"))
        turn_targets = (
            targets if evaluated else media_keys((frozen.get("evaluation") or {}).get("target_media_refs"))
        )
        turn_is_list = (
            is_list
            if evaluated
            else (frozen.get("evaluation") or {}).get("scorer") in {"media_set", "atm_list"}
        )
        observation = turn.get("retrieval_observations") or {}
        identities: dict[str, set[str]] = {}
        for ref in candidates or []:
            if not isinstance(ref, Mapping) or not media_keys([ref]):
                continue
            identifiers = [ref.get("asset_id")]
            if ref.get("media_type") == "video":
                # Explicit saved frame-to-parent mapping, never nearest-frame/GT inference.
                identifiers.append(ref.get("source_keyframe_asset_id"))
            for identifier in identifiers:
                if identifier:
                    identities.setdefault(str(identifier), set()).add(stable_media_key(ref))
        asset_keys = {
            identifier: next(iter(keys)) for identifier, keys in identities.items() if len(keys) == 1
        }
        ids = observation.get("asset_ids")
        candidate_complete = (
            observation.get("complete") is True
            and isinstance(candidates, list)
            and isinstance(ids, list)
            and all(str(i) in asset_keys for i in ids)
        )
        seen = (
            {asset_keys[str(i)] for i in ids or [] if str(i) in asset_keys}
            if isinstance(ids, list)
            else set()
        )
        delivered = media_keys(turn.get("predicted_media"))
        delivery_complete = (
            turn.get("media_mapping_complete") is True
            and isinstance(turn.get("predicted_media"), list)
            and isinstance(turn.get("attachments"), list)
        )
        applicable = [("retrieval_evidence", turn_retrieval)]
        if turn_is_list:
            applicable.append(("answer_target", turn_targets))
        for purpose, gt in applicable:
            status = "confirmed" if gt and candidate_complete else "record_missing"
            chain.append(
                {
                    "turn_index": ti,
                    "purpose": purpose,
                    "target": sorted(gt),
                    "candidates": sorted(seen),
                    "hits": sorted(gt & seen),
                    "missing": sorted(gt - seen) if status == "confirmed" else None,
                    "candidate_status": status,
                    "delivered": sorted(delivered),
                    "delivery_status": "confirmed" if delivery_complete else "record_missing",
                }
            )
            if gt and candidate_complete and gt - seen:
                add(
                    "candidate_gap",
                    "confirmed",
                    "冻结目标未全部进入本轮已记录的工具候选。",
                    [prefix + ".retrieval_observations", "retrieved_candidate_media"],
                    turn_index=ti,
                    purpose=purpose,
                    missing=sorted(gt - seen),
                )
            elif not gt or not candidate_complete:
                add(
                    "record_missing",
                    "record_missing",
                    "缺少目标定义或完整候选身份记录，无法判断是否检索遗漏。",
                    [prefix + ".retrieval_observations", "retrieved_candidate_media"],
                    turn_index=ti,
                    purpose=purpose,
                    missing_target=not bool(gt),
                    complete_candidate_flag=observation.get("complete"),
                    unmapped_asset_ids=[str(i) for i in ids or [] if str(i) not in asset_keys],
                )
        if turn_is_list:
            if turn_targets and delivery_complete:
                if turn_targets - delivered:
                    add(
                        "delivery_gap",
                        "confirmed",
                        "本列表题正式交付未覆盖全部答案目标。",
                        [prefix + ".attachments", prefix + ".predicted_media"],
                        turn_index=ti,
                        missing=sorted(turn_targets - delivered),
                    )
            else:
                add(
                    "record_missing",
                    "record_missing",
                    "缺少冻结答案目标或完整附件身份映射，无法判断交付是否齐全。",
                    [prefix + ".attachments", prefix + ".media_mapping_complete"],
                    turn_index=ti,
                )
        aliases = {
            (r["result_set_id"], r["handle"]): stable_media_key(r) for r in tool_media_aliases(item, ti)
        }
        inspected: set[str] = set()
        evidence_sources = []
        for path, call, call_turn in calls:
            if call_turn != ti:
                continue
            args = call.get("arguments") or {}
            result = call.get("result") or {}
            if not isinstance(args, Mapping) or not isinstance(result, Mapping):
                continue
            reason = str(result.get("failure_reason") or result.get("error") or call.get("text") or "")
            name = str(call.get("tool") or "")
            if name in {
                "read_photo_text",
                "view_photo",
                "inspect_photo",
                "read_video",
                "view_video",
                "inspect_media",
                "query_photo_people",
            }:
                key = aliases.get(
                    (str(args.get("result_set_id")), str(args.get("asset_handle")))
                ) or asset_keys.get(str(args.get("asset_id") or result.get("asset_id")))
                if key and call.get("status") == "ok":
                    inspected.add(key)
                evidence_sources.append(path)
            facts = {
                "turn_index": ti,
                "tool": name,
                "step_id": call.get("step_id"),
                "raw_reason": reason,
                "arguments": dict(args),
                "tool_status": call.get("status"),
            }
            if "no_text" in reason.lower():
                add(
                    "no_text",
                    "confirmed",
                    "本次文字读取返回未检测到文本；是否影响回答需结合题目复核。",
                    [path],
                    **facts,
                )
            elif call.get("status") in {"denied", "error", "failed", "timeout"}:
                category = (
                    "tool_parameter"
                    if any(
                        k in reason.lower()
                        for k in (
                            "argument",
                            "parameter",
                            "invalid",
                            "参数",
                            "person_required",
                            "person_not_named",
                        )
                    )
                    else "budget_limit"
                    if any(
                        k in reason.lower()
                        for k in (
                            "budget",
                            "limit",
                            "timeout",
                            "exhaust",
                            "次数",
                            "超时",
                            "预算",
                            "额度",
                            "每轮",
                            "用完",
                            "连续",
                        )
                    )
                    else "tool_limit"
                )
                add(
                    category,
                    "confirmed",
                    "记录中的工具限制或错误；内部原因与任务影响尚未确认。",
                    [path],
                    **facts,
                )
        hits = turn_retrieval & seen
        if candidate_complete and tools_known and hits - inspected:
            add(
                "not_inspected",
                "confirmed",
                "命中候选后，未记录对这些媒体成功执行进一步读取或查看；已有描述可能足够作答。",
                evidence_sources or [prefix + ".trace_bundle"],
                turn_index=ti,
                media=sorted(hits - inspected),
            )
        if not tools_known:
            add(
                "record_missing",
                "record_missing",
                "工具调用记录缺失，无法判断后续取证。",
                ["tool_trace", prefix + ".trace_bundle"],
                turn_index=ti,
            )
        for ri, requirement in enumerate(turn.get("requirements") or []):
            if requirement.get("status") in {"open", "failed", "unmet"}:
                add(
                    "semantic_gap",
                    "needs_review",
                    "产品记录仍有未满足要求，需结合原答卷复核。",
                    [prefix + f".requirements[{ri}]"],
                    turn_index=ti,
                    requirement=dict(requirement),
                )
        termination = str(turn.get("termination_reason") or "")
        if any(k in termination for k in ("budget", "timeout", "max_steps", "step_limit")):
            add(
                "budget_limit",
                "confirmed",
                "本轮记录了预算或时间终止原因。",
                [prefix + ".termination_reason"],
                turn_index=ti,
                raw_reason=termination,
            )
    for key, judge in (scores or {}).items():
        if str(judge.get("qa_id")) != str(item.get("qa_id")):
            continue
        reason = str(judge.get("reason") or judge.get("explanation") or "")
        if any(
            k in reason.lower() for k in ("遗漏", "未回答", "缺少", "未覆盖", "missing", "omit", "not cover")
        ):
            add(
                "semantic_gap",
                "needs_review",
                "已有Judge理由提示内容遗漏，尚需对照完整回答复核。",
                [f'scoring_results["{key}"]'],
                raw_reason=reason,
                turn_index=judge.get("turn_index"),
                scoring_batch_id=judge.get("batch_id"),
            )
    answer_source = {
        "answer": item.get("answer"),
        "turns": [
            {
                key: t.get(key)
                for key in (
                    "answer",
                    "attachments",
                    "requirements",
                    "termination_reason",
                    "predicted_media",
                    "retrieval_observations",
                    "trace_bundle",
                    "tool_trace",
                )
            }
            for t in turns
        ],
        "tools": tools,
        "candidates": candidates,
        "predicted_media": item.get("predicted_media"),
    }
    return {
        "rule_version": VERSION,
        "qa_id": str(item.get("qa_id")),
        "answer_fingerprint": fingerprint(answer_source),
        "definition_fingerprint": fingerprint(row),
        "analysis_status": "completed",
        "turn_count": len(turns),
        "original_answered_with_gap": any(
            t.get("agent_status") == "answered_with_gap"
            or t.get("status") == "answered_with_gap"
            or t.get("termination_reason") == "answered_with_gap"
            for t in turns
        ),
        "chain": chain,
        "findings": findings,
        "system_failure_inferred": False,
    }


def run_process(state: Mapping[str, Any]) -> dict[str, Any]:
    rows = {str(r["qa_id"]): r for r in (state.get("qa_snapshot") or {}).get("rows") or []}
    items = [
        item_process(rows.get(str(i.get("qa_id")), {}), i, state.get("scoring_results"))
        for i in state.get("items") or []
    ]
    categories: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for item in items:
        categories.update({f["category"] for f in item["findings"]})
        statuses.update({f["status"] for f in item["findings"]})
    total = int(state.get("qa_count") or len(items))
    return {
        "rule_version": VERSION,
        "run_id": state.get("run_id"),
        "source_fingerprint": fingerprint(items),
        "items": items,
        "summary": {
            "analyzed_qa": len(items),
            "total_qa": total,
            "coverage": len(items) / total if total else None,
            "original_answered_with_gap": sum(i["original_answered_with_gap"] for i in items),
            "categories": dict(categories),
            "labels": LABELS,
            "statuses": dict(statuses),
            "counts_are_unique_qa": True,
            "categories_overlap": True,
            "agent_calls": 0,
            "judge_calls": 0,
        },
    }


def process_for_qa(state: Mapping[str, Any], qa_id: str) -> dict[str, Any]:
    row: Mapping[str, Any] = next(
        (r for r in (state.get("qa_snapshot") or {}).get("rows") or [] if str(r["qa_id"]) == qa_id), {}
    )
    item: Mapping[str, Any] = next((i for i in state.get("items") or [] if str(i.get("qa_id")) == qa_id), {})
    return item_process(row, item, state.get("scoring_results"))
