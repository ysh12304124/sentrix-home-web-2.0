"""Sentrix 图片记忆导出包的独立读取适配器。

PhotoBench 只读取并校验冻结的导出包；不修改 GT、评分或轨迹导出。适配器拒绝缺失
文件、校验和不符和缺字段的记录，避免把不完整快照当成可用素材。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORE_FIELDS = (
    "id",
    "type",
    "image_path",
    "timestamp",
    "location",
    "location_name",
    "short_caption",
    "caption",
    "ocr_text",
    "tags",
    "entities",
    "model_used",
    "processing_version",
    "processed_at",
)
PROVENANCE_FIELDS = ("record_id", "field", "status", "source")


@dataclass(frozen=True, slots=True)
class ImageMemoryPackage:
    records: tuple[dict[str, Any], ...]
    provenance: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{line_number} 不是合法 JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} 必须是对象")
        rows.append(value)
    return tuple(rows)


def load_image_memory_package(directory: Path | str) -> ImageMemoryPackage:
    root = Path(directory)
    manifest_path = root / "export_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("export_manifest.json 缺失")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("export_manifest.json 必须是对象")
    expected = {
        str(item.get("name")): str(item.get("sha256") or "")
        for item in manifest.get("files") or []
        if isinstance(item, dict) and item.get("name")
    }
    for name in ("image_memories.jsonl", "image_memory_provenance.jsonl"):
        path = root / name
        if not path.is_file():
            raise ValueError(f"{name} 缺失")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected.get(name) and expected[name] != digest:
            raise ValueError(f"{name} 校验和不一致")
    records = _read_jsonl(root / "image_memories.jsonl")
    provenance = _read_jsonl(root / "image_memory_provenance.jsonl")
    for index, record in enumerate(records, start=1):
        missing = [field for field in CORE_FIELDS if field not in record]
        if missing:
            raise ValueError(f"image_memories.jsonl:{index} 缺少字段 {missing}")
        if record.get("type") != "image":
            raise ValueError(f"image_memories.jsonl:{index} type 必须是 image")
    for index, item in enumerate(provenance, start=1):
        missing = [field for field in PROVENANCE_FIELDS if field not in item]
        if missing:
            raise ValueError(f"image_memory_provenance.jsonl:{index} 缺少字段 {missing}")
    return ImageMemoryPackage(records=records, provenance=provenance, manifest=manifest)
