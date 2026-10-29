"""Versioned system abilities, independent of behavior and legacy task labels."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

LEGACY_ABILITY_VERSION = "ability-2026.10.28-v1"
V2_ABILITY_VERSION = "ability-2026.10.28-v2"
ABILITY_VERSION = "ability-2026.10.29-v3"
LEGACY_FAMILIES = {"P": "信息获取与感知", "R": "推理与计算", "S": "归纳与总结"}
ABILITY_FAMILIES = {**LEGACY_FAMILIES, "H": "抗幻觉", "I": "抗注入攻击"}
LEGACY_ABILITIES = {
    "P1": "记录定位与筛选",
    "P2": "已记录信息读取",
    "P3": "视觉内容识别",
    "P4": "画面文字读取",
    "R1": "计数与去重统计",
    "R2": "数值运算",
    "R3": "比较与排序",
    "R4": "关系与时空推断",
    "S1": "场景综合描述",
    "S2": "单事件概括",
    "S3": "跨事件归纳",
    "S4": "过程与时间线整理",
}
V2_ABILITIES = {
    "P1": "媒体定位与筛选",
    "P2": "记录属性查询",
    "P3": "视觉内容理解",
    "P4": "画面文字识读",
    "R1": "计数与去重统计",
    "R2": "数值计算",
    "R3": "比较与排序",
    "R4": "关系与情境推断",
    "S1": "场景综合描述",
    "S2": "经历与过程总结",
}
SAFETY_SUBTYPES = {"hallucination": "H1", "injection": "I1"}
ABILITIES = {**V2_ABILITIES, "H1": "抗幻觉", "I1": "抗注入攻击"}
BENCHMARK_GROUPS = {"ordinary": "普通能力题", "hallucination": "抗幻觉拒答", "injection": "注入防护"}
ABILITY_VERSIONS = {
    LEGACY_ABILITY_VERSION: {
        "families": LEGACY_FAMILIES,
        "types": LEGACY_ABILITIES,
        "groups": BENCHMARK_GROUPS,
    },
    V2_ABILITY_VERSION: {"families": ABILITY_FAMILIES, "types": V2_ABILITIES, "groups": BENCHMARK_GROUPS},
    ABILITY_VERSION: {"families": ABILITY_FAMILIES, "types": ABILITIES, "groups": BENCHMARK_GROUPS},
}
ABILITY_FIELDS = (
    "ability_taxonomy_version",
    "ability_family_ids",
    "ability_primary_ids",
    "ability_supporting_ids",
    "benchmark_group",
    "dialogue_management",
    "classification_content_sha256",
)


def family_ids(group: str, primary: list[str], action: Any = None) -> list[str]:
    """Primary directions shared by V2 and V3; subtype meaning follows the saved version."""
    if group in {"hallucination", "injection"}:
        return ["H" if group == "hallucination" else "I"]
    return [] if action == "clarify" else sorted({v[0] for v in primary})


def ability_labels(versions: Iterable[str]) -> dict[str, str]:
    """Use saved versions, including explicit names when a report mixes versions."""
    selected = sorted(set(versions) & ABILITY_VERSIONS.keys())
    names: dict[str, list[tuple[str, str]]] = {}
    for version in selected:
        for section in ABILITY_VERSIONS[version].values():
            for key, name in section.items():
                names.setdefault(key, []).append((version.rsplit("-", 1)[-1], name))
    return {
        key: values[0][1] if len({v for _, v in values}) == 1 else "；".join(f"{v}：{n}" for v, n in values)
        for key, values in names.items()
    }


def validate_abilities(row: Mapping[str, Any]) -> None:
    """Reject incomplete or contradictory versioned annotations at import time."""
    version, group = row.get("ability_taxonomy_version"), row.get("benchmark_group")
    if version not in ABILITY_VERSIONS:
        raise ValueError("unsupported ability_taxonomy_version")
    if group not in BENCHMARK_GROUPS:
        raise ValueError("invalid benchmark_group")
    catalog = ABILITY_VERSIONS[version]["types"]
    turns = row.get("conversation") or []
    for record in [row, *turns]:
        if record.get("ability_taxonomy_version") != version:
            raise ValueError("inconsistent ability_taxonomy_version")
        if record.get("benchmark_group") != group:
            raise ValueError("inconsistent benchmark_group")
        primary, supporting = record.get("ability_primary_ids"), record.get("ability_supporting_ids", [])
        for values in (primary, supporting):
            if (
                not isinstance(values, list)
                or any(not isinstance(v, str) or v not in catalog for v in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("invalid ability IDs")
        if set(primary) & set(supporting):
            raise ValueError("primary and supporting abilities overlap")
        if version == ABILITY_VERSION:
            safety = SAFETY_SUBTYPES.get(str(group))
            if safety:
                expected_primary = [] if record.get("expected_action") == "clarify" else [safety]
                if primary != expected_primary or supporting:
                    raise ValueError(
                        "safety group must use its matching subtype without supporting abilities"
                    )
            elif any(v not in V2_ABILITIES for v in primary + supporting):
                raise ValueError("ordinary groups must use ordinary abilities")
        elif group != "ordinary" and primary:
            raise ValueError("safety groups must not contribute ordinary abilities")
        if record is not row and record.get("expected_action") == "clarify" and primary:
            raise ValueError("clarification turn has primary abilities")
        if group == "ordinary" and record.get("expected_action") != "clarify" and not primary:
            raise ValueError("ordinary answer has no primary ability")
        if version in {V2_ABILITY_VERSION, ABILITY_VERSION}:
            expected = family_ids(str(group), primary, record.get("expected_action"))
            if record.get("ability_family_ids") != expected:
                raise ValueError("invalid ability_family_ids")
    if turns and set(row["ability_primary_ids"]) != {
        v for turn in turns for v in turn["ability_primary_ids"]
    }:
        raise ValueError("question abilities differ from answering-turn union")


def ability_dimensions(record: Mapping[str, Any]) -> dict[str, list[str]]:
    """Project saved facts only; missing history remains unknown, V1 stays V1."""
    unknown = {k: ["unknown"] for k in ("ability_family", "ability_type", "benchmark_group")}
    version = record.get("ability_taxonomy_version")
    if version not in ABILITY_VERSIONS:
        return unknown
    group, primary = record.get("benchmark_group"), record.get("ability_primary_ids")
    catalog = ABILITY_VERSIONS[version]["types"]
    if group not in BENCHMARK_GROUPS or not isinstance(primary, list):
        return unknown
    if any(not isinstance(v, str) or v not in catalog for v in primary):
        return unknown
    if version in {V2_ABILITY_VERSION, ABILITY_VERSION}:
        families = family_ids(str(group), primary, record.get("expected_action"))
        if record.get("ability_family_ids") != families:
            return unknown
    else:
        families = sorted({v[0] for v in primary}) if group == "ordinary" else []
    types = (
        (sorted(set(primary)) or ["unknown"])
        if group == "ordinary" or version == ABILITY_VERSION
        else ["not_applicable"]
    )
    if record.get("expected_action") == "clarify":
        types = ["not_applicable"]
        if group == "ordinary":
            families = []
    return {
        "ability_family": families or types,
        "ability_type": types,
        "benchmark_group": [str(group)],
    }
