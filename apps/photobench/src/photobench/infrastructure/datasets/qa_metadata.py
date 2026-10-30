"""``QaMetadataSource`` 的文件实现。

1.0 ``OrchestratorRepository._load_qa_metadata``（``:4885``）的重建，连它依赖的两个
小件一起搬：``load_jsonl``（``:1600``）与 ``load_task_action_policy`` /
``apply_task_action_defaults``（``:1609`` / ``:1629``）。

**数据集根的对应关系**：1.0 是 ``services/photobench/data/``，2.0 是
``apps/photobench/datasets/``（2.0 仓库里已有的那 1.4 GB）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: ``_load_qa_metadata`` 只取这些字段——1.0 ``:4886``。多取一个字段就是多一份内存，
#: 而这张表是「每个 qa_id 一条」的，全库扫描一次。
_FIELDS = (
    "ability_taxonomy_version",
    "ability_family_ids",
    "ability_primary_ids",
    "ability_supporting_ids",
    "benchmark_group",
    "dialogue_management",
    "classification_content_sha256",
    "injection",
    "task_type",
    "question_type",
    "tags",
    "angle",
    "difficulty",
    "answerability",
    "scope",
    "scope_anchor",
    "required_evidence_sources",
    "query_anchors",
    "expected_action",
    "answer",
    "conversation",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """1.0 ``:1600``。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_task_action_policy(path: Path) -> dict[tuple[str, str], str]:
    """1.0 ``:1609``：QA 集级的 ``expected_action`` 默认值。文件坏了就当没有。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        defaults = value.get("defaults") or []
        result = {}
        for entry in defaults:
            album_id = str(entry.get("album_id") or "")
            qa_set = str(entry.get("qa_set") or "")
            action = str(entry.get("expected_action") or "")
            if album_id and qa_set and action in {"answer", "refuse", "clarify"}:
                result[(album_id, qa_set)] = action
        return result
    except (OSError, TypeError, json.JSONDecodeError):
        return {}


def apply_task_action_defaults(
    rows: list[dict[str, Any]], album_id: str, qa_set: str, policy: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    """1.0 ``:1629``：逐题标签优先，没有才补 QA 集默认值。"""
    default = policy.get((album_id, qa_set))
    if default is None:
        return rows
    return [{**row, "expected_action": row.get("expected_action") or default} for row in rows]


class JsonQaMetadataSource:
    """扫一遍 ``<data_root>/*/manifest.json`` 指向的 qa ``.jsonl``。"""

    def __init__(self, data_root: Path, task_action_policy: Path) -> None:
        self._data_root = data_root
        self._policy_path = task_action_policy

    def metadata_by_qa_id(self) -> dict[str, dict[str, Any]]:
        policy = load_task_action_policy(self._policy_path)
        result: dict[str, dict[str, Any]] = {}
        if not self._data_root.exists():
            return result
        for manifest_path in self._data_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for qa_set, relative in (manifest.get("qa_sets") or {}).items():
                    qa_path = manifest_path.parent / relative
                    qa_digest = hashlib.sha256(qa_path.read_bytes()).hexdigest()
                    namespace = (
                        "\0".join(
                            (
                                str(manifest.get("album_id") or manifest_path.parent.name),
                                str(qa_set),
                                qa_digest,
                            )
                        )
                        + "\0"
                    )
                    rows = apply_task_action_defaults(
                        load_jsonl(manifest_path.parent / relative),
                        str(manifest.get("album_id") or manifest_path.parent.name),
                        str(qa_set),
                        policy,
                    )
                    for row in rows:
                        qa_id = str(row.get("qa_id") or "")
                        if qa_id:
                            result[namespace + qa_id] = {
                                field: row[field] for field in _FIELDS if row.get(field) is not None
                            }
            except (OSError, TypeError, json.JSONDecodeError):
                continue
        return result
