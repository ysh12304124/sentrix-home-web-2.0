"""Explicit equivalent task labels; raw QA and GT keep their original values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from photobench.domain.datasets.ability_taxonomy import (
    ABILITIES,
    SAFETY_SUBTYPES,
    V2_ABILITY_VERSION,
    ability_labels,
)
from photobench.domain.datasets.qa_taxonomy import SUBTYPE_TASK

ABILITY_DISPLAY_PROJECTION = "safety-secondary-v1"


def analysis_ability_labels(versions: Iterable[str]) -> dict[str, str]:
    versions = set(versions)
    labels = ability_labels(versions)
    if V2_ABILITY_VERSION in versions:
        labels.update({key: ABILITIES[key] for key in SAFETY_SUBTYPES.values()})
    return labels


def project_case_abilities(case: Mapping[str, Any]) -> dict[str, Any]:
    """Give saved V2 safety groups same-name subtypes in analysis views only."""

    def project(values: dict[str, Any], version: Any, action: Any) -> dict[str, Any]:
        if version != V2_ABILITY_VERSION or action == "clarify":
            return values
        groups = values.get("benchmark_group", [])
        subtype = SAFETY_SUBTYPES.get(groups[0]) if len(groups) == 1 else None
        if (
            subtype
            and values.get("ability_family") == [subtype[0]]
            and values.get("ability_type") == ["not_applicable"]
        ):
            return {
                **values,
                "ability_type": [subtype],
                "saved_ability_type": values["ability_type"],
                "ability_display_projection": ABILITY_DISPLAY_PROJECTION,
            }
        return values

    dimensions = case.get("dimension_labels", {})
    projected = project(
        dimensions,
        case.get("ability_taxonomy_version"),
        case.get("dimensions", {}).get("expected_action"),
    )
    turns = []
    for turn in case.get("turns", []):
        versions = turn.get("ability_taxonomy_version", [])
        turns.append(project(turn, versions[0] if len(versions) == 1 else None, turn.get("expected")))
    if projected is dimensions and all(a is b for a, b in zip(turns, case.get("turns", []), strict=True)):
        return dict(case)
    labels = {**case.get("ability_labels", {}), **analysis_ability_labels([V2_ABILITY_VERSION])}
    return {
        **case,
        "dimension_labels": {
            k: v
            for k, v in projected.items()
            if k
            not in {
                "saved_ability_type",
                "ability_display_projection",
            }
        },
        "dimensions": {**case["dimensions"], "ability_type": " / ".join(projected["ability_type"])},
        "saved_ability_type": dimensions.get("ability_type"),
        "ability_display_projection": ABILITY_DISPLAY_PROJECTION,
        "ability_labels": labels,
        "turns": turns,
    }


TASK_ALIASES = {
    "T1_media_retrieval": "T1_retrieve_media",
    "media_retrieval": "T1_retrieve_media",
    "媒体检索": "T1_retrieve_media",
    "fact_qa": "T2_fact_qa",
    "事实问答": "T2_fact_qa",
    "enumeration": "T3_enumeration",
    "统计枚举": "T3_enumeration",
    "event_summary": "T5_event_summary",
    "事件总结": "T5_event_summary",
    "行为判断": "behavior",
}


def category_value(dimension: str, value: str) -> str:
    return TASK_ALIASES.get(value, value) if dimension == "task_type" else value


# Content tasks and behavioral test scenarios are independent source annotations.
CONTENT_TYPES = {"T1_retrieve_media", "T2_fact_qa", "T3_enumeration", "T5_event_summary"}
SCENARIOS = {
    "multi_turn_clarify",
    "evidence_insufficient",
    "prompt_injection",
    "clarification",
    "unanswerable_fact",
}
QUALITY_DIMENSIONS = (
    "ability_taxonomy_version",
    "ability_family",
    "ability_type",
    "benchmark_group",
    "content_type",
    "content_subtype",
    "difficulty",
    "answerability",
    "media_type",
    "conversation_type",
)


def content_types(tasks: list[str]) -> list[str]:
    values = list(
        dict.fromkeys(TASK_ALIASES.get(v, v) for v in tasks if TASK_ALIASES.get(v, v) in CONTENT_TYPES)
    )
    return values or [v for v in tasks if v in {"not_applicable", "pending_review"}] or ["unknown"]


def test_scenarios(forms: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in forms if v in SCENARIOS)) or ["unknown"]


def content_subtypes(task: str, subtype: str) -> list[str]:
    if SUBTYPE_TASK.get(subtype) == task or task == subtype in {"not_applicable", "pending_review"}:
        return [subtype]
    return ["unknown"]


def scenarios(record: dict[str, object]) -> list[str]:
    raw = record.get("question_type")
    values = test_scenarios([str(v) for v in raw] if isinstance(raw, list) else [str(raw or "")])
    if record.get("injection") is True:
        values.append("prompt_injection")
    if record.get("answerability") == "needs_clarification":
        values.append("clarification")
    if record.get("answerability") == "insufficient_evidence":
        values.append("evidence_insufficient")
    return sorted(set(values) - {"unknown"}) or ["unknown"]
