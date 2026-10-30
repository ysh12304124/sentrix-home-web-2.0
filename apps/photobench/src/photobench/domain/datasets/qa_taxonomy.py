"""Versioned, orthogonal QA annotations shared by validation, analysis and the UI."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from photobench.domain.datasets.ability_taxonomy import (
    ABILITIES,
    ABILITY_FAMILIES,
    ABILITY_VERSION,
    ABILITY_VERSIONS,
    BENCHMARK_GROUPS,
    validate_abilities,
)

TAXONOMY_VERSION = "1.0"
QA_SCHEMA_VERSION = "2.0"
TASKS: dict[str, dict[str, Any]] = {
    "T1_retrieve_media": {
        "label": "媒体检索",
        "subtypes": {"retrieve_record": "指定记录", "retrieve_collection": "条件合集"},
    },
    "T2_fact_qa": {
        "label": "事实查询",
        "subtypes": {
            "fact_time": "时间",
            "fact_place": "地点",
            "fact_identity": "人物身份与关系",
            "fact_attribute": "事物特征",
            "fact_environment": "环境状态",
            "fact_activity": "活动情况",
            "fact_reason": "原因用途",
            "fact_multiple": "多项事实",
        },
    },
    "T3_enumeration": {
        "label": "统计整理",
        "subtypes": {
            "count": "计数",
            "enumerate": "枚举",
            "count_and_enumerate": "计数并枚举",
            "compare": "比较",
            "order": "排序",
        },
    },
    "T5_event_summary": {
        "label": "内容概述",
        "subtypes": {
            "describe_scene": "画面描述",
            "summarize_event": "事件总结",
            "summarize_timeline": "时间线总结",
        },
    },
}
ACTIONS = {"answer": "回答", "clarify": "澄清", "refuse": "拒答", "answer_and_refuse": "回答并拒绝越权部分"}
ANSWERABILITY = {
    "answerable": "可回答",
    "needs_clarification": "需澄清",
    "insufficient_evidence": "证据不足",
    "partially_answerable": "部分可回答",
    "not_applicable": "不适用",
}
DIFFICULTY = {"easy": "简单", "medium": "中等", "hard": "困难"}
SUBTYPE_TASK = {sub: task for task, definition in TASKS.items() for sub in definition["subtypes"]}


def taxonomy_document() -> dict[str, Any]:
    return {
        "version": TAXONOMY_VERSION,
        "schema_version": QA_SCHEMA_VERSION,
        "tasks": TASKS,
        "supported_schema_versions": ["2.0", "3.0"],
        "abilities": {
            "version": ABILITY_VERSION,
            "families": ABILITY_FAMILIES,
            "types": ABILITIES,
            "groups": BENCHMARK_GROUPS,
        },
        "ability_versions": ABILITY_VERSIONS,
        "actions": ACTIONS,
        "answerability": ANSWERABILITY,
        "difficulty": DIFFICULTY,
        "special": {"not_applicable": "不适用", "pending_review": "待核定"},
    }


def validate_qa_annotations(row: dict[str, Any]) -> None:
    """Validate only explicitly versioned inputs; legacy labels retain their meaning."""
    has_abilities = "ability_taxonomy_version" in row
    if has_abilities:
        validate_abilities(row)
    for record in [row, *(row.get("conversation") or [])]:
        task, subtype = record.get("task_type"), record.get("question_type")
        special = task in {"not_applicable", "pending_review"} and task == subtype
        if (
            not (has_abilities and task is None and subtype is None)
            and not special
            and SUBTYPE_TASK.get(str(subtype)) != task
        ):
            raise ValueError(f"invalid task/subtype for {row.get('qa_id')}: {task}/{subtype}")
        if record.get("expected_action") not in {*ACTIONS, "pending_review"}:
            raise ValueError(f"invalid expected_action for {row.get('qa_id')}")
        if record.get("answerability") not in {*ANSWERABILITY, "pending_review"}:
            raise ValueError(f"invalid answerability for {row.get('qa_id')}")
        if record.get("difficulty") not in {*DIFFICULTY, "pending_review"}:
            raise ValueError(f"invalid difficulty for {row.get('qa_id')}")
        if not isinstance(record.get("injection"), bool):
            raise ValueError(f"missing injection flag for {row.get('qa_id')}")
    turns = row.get("conversation") or []
    if turns:
        for key in (
            "task_type",
            "question_type",
            "expected_action",
            "answerability",
            "difficulty",
            "injection",
        ):
            if row.get(key) != turns[-1].get(key):
                raise ValueError(f"final turn annotation differs for {row.get('qa_id')}: {key}")


def qa_fingerprint(value: Any) -> str:
    """Fingerprint the loaded definition, including policy defaults and turn annotations."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Public projections retain provenance without returning the full question bank."""
    return {key: value for key, value in snapshot.items() if key != "rows"}
