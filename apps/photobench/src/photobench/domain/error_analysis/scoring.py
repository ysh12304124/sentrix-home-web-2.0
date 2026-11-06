"""Read-only scoring evidence for analysis; never rejudge or change saved scores."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ATM_TYPES = {"number": "ATM数值题", "list_recall": "ATM列表题", "open_end": "ATM开放题"}
SCORER_TYPES = {"atm_number": "number", "atm_list": "list_recall", "atm_open": "open_end"}


def scoring_question_type(record: Mapping[str, Any]) -> str:
    definition = record.get("evaluation") or {}
    result = record.get("evaluation_result") or record.get("judge") or {}
    for source in (definition, result):
        kind = source.get("source_qtype")
        if kind in ATM_TYPES:
            return str(kind)
        if source.get("scorer") in SCORER_TYPES:
            return SCORER_TYPES[source["scorer"]]
    # A profile's purpose is sufficient only with explicit ATM profile provenance.
    if result.get("profile_id") == "atm_qs_v1":
        return {"number": "number", "list": "list_recall", "open": "open_end"}.get(
            str(result.get("purpose") or result.get("scorer") or ""), "unknown"
        )
    return "unknown"


def scoring_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    definition = record.get("evaluation") or {}
    result = record.get("evaluation_result") or record.get("judge") or {}
    evidence: dict[str, Any] = {
        "question_type": scoring_question_type(record),
        "result": {
            key: result[key]
            for key in (
                "profile_id",
                "rule_version",
                "batch_id",
                "purpose",
                "scorer",
                "source_qtype",
                "answer_format",
                "score",
                "max_score",
                "normalized_score",
                "status",
                "judge_status",
                "reason",
                "formula",
                "target",
                "predicted",
                "intersection",
                "missing",
                "extra",
                "matched_option",
                "comparisons",
                "choice_match",
                "semantic_aux",
                "official_prediction",
                "official_answer",
                "parsed",
                "deterministic",
                "semantic_score",
                "number_aux",
                "number_aux_disagrees",
                "choice_semantic_aux",
                "media_metrics",
            )
            if key in result
        },
        "definition": dict(definition),
        "scoring_rule": result.get("scoring_rule"),
        "limitations": [],
    }
    if not result.get("scorer"):
        evidence["limitations"].append("未记录结构化评分器，不能据此确认ATM判分方式。")
    if not result.get("reason") and not result.get("formula"):
        evidence["limitations"].append("未记录判分理由或公式，不能从分数推断扣分原因。")
    if (
        result.get("profile_id")
        and not (result.get("scoring_rule") or {}).get("frozen_prompt_template")
        and (result.get("purpose") or result.get("scorer")) != "list"
    ):
        evidence["limitations"].append("本记录未包含该批次的评分提示词模板。")
    # Compare only explicitly saved canonical media keys, not inferred visual identities.
    sets = result if "target" in result else (result.get("media_metrics") or {}).get("delivery", {})
    target, predicted = sets.get("target"), sets.get("predicted")
    if (
        isinstance(target, list)
        and isinstance(predicted, list)
        and all(isinstance(value, str) for value in [*target, *predicted])
    ):
        gt, pred = set(target), set(predicted)
        evidence["list_comparison"] = {
            "intersection": sorted(gt & pred),
            "missing": sorted(gt - pred),
            "extra": sorted(pred - gt),
            "source": "result" if sets is result else "result.media_metrics.delivery",
            "basis": "保存的目标与交付规范媒体ID集合；只列差异，不重新计分或推断顺序规则。"
            "媒体差异是否导致主分扣分，须核对实际评分器。",
        }
    elif evidence["question_type"] == "list_recall":
        evidence["limitations"].append("缺少已保存的目标或交付集合，无法逐项确认漏项和多余项。")
    if evidence["question_type"] == "number":
        evidence["limitations"].append(
            "数值判断以实际保存的Judge理由或公式为准；单位和容差未记录时保持未知。"
        )
    evidence["note"] = "definition为题包原始定义；实际评分标准以result的profile_id、batch_id及scorer为准。"
    return evidence


def project_case_scoring(case: Mapping[str, Any]) -> dict[str, Any]:
    """Derive classifications for old reports without writing back to stored cases."""
    kind = scoring_question_type(case)
    turns = case.get("turns", [])
    primary_turn = (case.get("evaluation") or {}).get("applies_to_turn", turns[-1]["turn"] if turns else 0)
    projected_turns = []
    for turn in turns:
        record = {
            "evaluation": case.get("evaluation") if turn["turn"] == primary_turn else None,
            "evaluation_result": turn.get("evaluation_result"),
        }
        if not record["evaluation_result"] and len(turns) == 1:
            record["evaluation_result"] = case.get("evaluation_result")
        projected_turns.append(
            {
                **turn,
                "scoring_question_type": [scoring_question_type(record)],
                "scoring_evidence": scoring_evidence(record),
            }
        )
    return {
        **case,
        "turns": projected_turns,
        "dimensions": {**case.get("dimensions", {}), "scoring_question_type": kind},
        "dimension_labels": {**case.get("dimension_labels", {}), "scoring_question_type": [kind]},
        "scoring_evidence": scoring_evidence(case)
        if case.get("evaluation") or case.get("evaluation_result")
        else None,
    }
