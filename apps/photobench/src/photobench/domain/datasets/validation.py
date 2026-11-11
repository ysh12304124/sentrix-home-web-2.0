"""Preflight reports preserve original QA bytes and keep the two GT purposes separate."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.contract import media_key

VERSION = "qa-media-preflight-v1"


def validate_targets(rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    inventory = Counter(
        media_key({"media_type": kind, "media_id": name})
        for collection, kind in (("photos", "image"), ("videos", "video"))
        for name in manifest.get(collection) or []
    )
    inventory_known = "photos" in manifest or "videos" in manifest
    issues: list[dict[str, Any]] = []

    def issue(qa: str, field: str, code: str, value: Any, message: str, level: str = "error") -> None:
        issues.append(
            {"qa_id": qa, "field": field, "code": code, "value": value, "message": message, "severity": level}
        )

    def refs(qa: str, field: str, values: Any) -> set[str]:
        found: set[str] = set()
        if not isinstance(values, list):
            issue(qa, field, "invalid_refs", values, "媒体引用必须为列表")
            return found
        for index, ref in enumerate(values):
            at = f"{field}[{index}]"
            if not isinstance(ref, Mapping) or ref.get("media_type") not in {"image", "video"}:
                issue(qa, at, "invalid_media_type", ref, "需明确image或video类型")
                continue
            raw = ref.get("media_id") or ref.get("id")
            if not isinstance(raw, str) or not raw.strip():
                issue(qa, at, "empty_media_id", raw, "媒体ID缺失")
                continue
            key = media_key({**ref, "media_id": raw.strip()})
            if inventory_known and key not in inventory:
                pieces = re.split(r"[\s,;，；|]+", raw.strip())
                code = (
                    "multiple_ids"
                    if len(pieces) > 1
                    and all(
                        media_key({"media_type": ref["media_type"], "media_id": p}) in inventory
                        for p in pieces
                    )
                    else "unknown_media_id"
                )
                issue(qa, at, code, raw, "媒体ID不在清单中；多个ID需拆成独立引用，禁止猜映射")
            elif inventory.get(key, 0) > 1:
                issue(qa, at, "ambiguous_media_id", raw, "清单中同类型媒体身份不唯一")
            if key in found or raw != raw.strip():
                issue(
                    qa,
                    at,
                    "normalized_reference",
                    raw,
                    "集合计算会去重并忽略ID首尾空格，原题目与引用保留",
                    "warning",
                )
            found.add(key)
        return found

    def record(qa: str, value: Mapping[str, Any], prefix: str = "") -> None:
        purposes: dict[str, set[str]] = {}
        for purpose in ("retrieval", "answer_evidence", "evidence"):
            field = purpose + "_media_refs"
            if field in value:
                purposes[purpose] = refs(qa, prefix + field, value[field])
            else:
                legacy: list[dict[str, Any]] = []
                for suffix, kind in (("image_ids", "image"), ("video_ids", "video")):
                    legacy_field = f"{purpose}_{suffix}"
                    names = value.get(legacy_field, [])
                    if not isinstance(names, list):
                        issue(qa, prefix + legacy_field, "invalid_refs", names, "媒体ID必须为列表")
                        continue
                    legacy.extend({"media_type": kind, "media_id": name} for name in names)
                if legacy:
                    purposes[purpose] = refs(qa, prefix + purpose + "_legacy_ids", legacy)
        definition = value.get("evaluation") or {}
        if not isinstance(definition, Mapping):
            issue(qa, prefix + "evaluation", "invalid_definition", definition, "评分定义必须为对象")
            definition = {}
        if definition.get("scorer") in {"media_set", "atm_list"}:
            field = prefix + "evaluation.target_media_refs"
            target = refs(qa, field, definition.get("target_media_refs"))
            if not target:
                issue(
                    qa,
                    field,
                    "missing_list_target",
                    definition.get("target_media_refs"),
                    "列表评分需明确非空答案目标集合",
                )
            if "retrieval" in purposes and target != purposes["retrieval"]:
                issue(
                    qa,
                    field,
                    "distinct_target_and_evidence",
                    {"answer_target": sorted(target), "retrieval_evidence": sorted(purposes["retrieval"])},
                    "答案目标与检索证据不同，请审核；两者分别使用，不自动合并",
                    "warning",
                )
        for field in ("answer_claims", "conversation"):
            children = value.get(field, [])
            if not isinstance(children, list):
                issue(qa, prefix + field, "invalid_records", children, "题目子记录必须为列表")
                continue
            for i, child in enumerate(children):
                if isinstance(child, Mapping):
                    record(qa, child, prefix + f"{field}[{i}].")
                else:
                    issue(qa, prefix + f"{field}[{i}]", "invalid_record", child, "题目子记录必须为对象")

    for row in rows:
        qa = str(row.get("qa_id") or "")
        record(qa, row)
    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "version": VERSION,
        "can_start": not errors,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "inventory_checked": inventory_known,
        "issues": issues,
        "qa_count": len(rows),
        "source_modified": False,
    }


def require_valid_targets(rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    report = validate_targets(rows, manifest)
    if not report["can_start"]:
        first = next(i for i in report["issues"] if i["severity"] == "error")
        raise ValueError(
            f"QA {first['qa_id']} {first['field']}: {first['message']} ({first['value']!r}); "
            f"{report['error_count']} errors; inspect qa-dataset validation report"
        )
    return report
