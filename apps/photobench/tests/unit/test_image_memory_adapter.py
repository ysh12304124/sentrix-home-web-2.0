"""图片记忆导出包的独立读取适配器：校验和、缺字段与拒绝不完整快照。"""

from __future__ import annotations

import hashlib
import json

import pytest

from photobench.infrastructure.image_memory import load_image_memory_package

RECORD = {
    "id": "IMG_0001",
    "type": "image",
    "image_path": "IMG_0001.jpg",
    "timestamp": "2024-01-01",
    "location": {"latitude": 30.0, "longitude": 120.0},
    "location_name": "浙江省杭州市",
    "short_caption": "海边",
    "caption": "详细描述",
    "ocr_text": "",
    "tags": [],
    "entities": [],
    "model_used": "qwen3.8-flash",
    "processing_version": "image-observation-v2",
    "processed_at": "2026-11-24T12:00:00+00:00",
}
PROVENANCE = {
    "record_id": "IMG_0001",
    "field": "ocr_regions",
    "status": "not_provided",
    "source": "not_implemented",
    "regions": None,
    "evidence_refs": [],
}


def _package(tmp_path):
    records = (json.dumps(RECORD, ensure_ascii=False) + "\n").encode()
    provenance = (json.dumps(PROVENANCE, ensure_ascii=False) + "\n").encode()
    manifest = {
        "files": [
            {"name": "image_memories.jsonl", "sha256": hashlib.sha256(records).hexdigest()},
            {
                "name": "image_memory_provenance.jsonl",
                "sha256": hashlib.sha256(provenance).hexdigest(),
            },
        ]
    }
    (tmp_path / "image_memories.jsonl").write_bytes(records)
    (tmp_path / "image_memory_provenance.jsonl").write_bytes(provenance)
    (tmp_path / "export_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_loads_and_validates_package(tmp_path) -> None:
    package = load_image_memory_package(_package(tmp_path))
    assert package.records[0]["id"] == "IMG_0001"
    assert package.provenance[0]["status"] == "not_provided"
    assert package.manifest["files"]


def test_rejects_checksum_mismatch(tmp_path) -> None:
    root = _package(tmp_path)
    (root / "image_memories.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="校验和"):
        load_image_memory_package(root)


def test_rejects_missing_core_field(tmp_path) -> None:
    root = _package(tmp_path)
    broken = dict(RECORD)
    broken.pop("location_name")
    payload = (json.dumps(broken, ensure_ascii=False) + "\n").encode()
    (root / "image_memories.jsonl").write_bytes(payload)
    manifest = json.loads((root / "export_manifest.json").read_text())
    manifest["files"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    (root / "export_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="缺少字段"):
        load_image_memory_package(root)
