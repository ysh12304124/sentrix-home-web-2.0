"""Register the original ATM media archive with PhotoBench, without running evaluation.

Run with PYTHONPATH=apps/photobench/src .venv/bin/python scripts/prepare_atm_bench.py
--source /path/to/ATM-Bench. Existing albums are never overwritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_benchmark_adapters import build_atm, write_json

from photobench.infrastructure.datasets.albums import JsonAlbumSource


def batch_media(root: Path, manifest: dict) -> None:
    """Keep each flat import directory below the API's 1000-file request cap."""
    mapping = {}
    for collection in ("photos", "videos"):
        paths = manifest[collection]
        for index, old in enumerate(paths):
            new = f"{collection}/batch-{index // 900:03d}/{Path(old).name}"
            target = root / new
            target.parent.mkdir(exist_ok=True)
            (root / old).rename(target)
            mapping[old] = new
        manifest[collection] = [mapping[old] for old in paths]
    audit = root / "audit/media-sha256.json"
    hashes = json.loads(audit.read_text())
    for entry in hashes:
        entry["path"] = mapping[entry["path"]]
    write_json(audit, hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--data-root", type=Path, default=Path(__file__).resolve().parents[1] / "apps/photobench/datasets"
    )
    args = parser.parse_args()
    source = args.source.resolve()
    root = args.data_root.resolve() / "atm-bench-media"
    if root.exists():
        parser.error(f"Refusing to overwrite existing album: {root}")
    for relative in (
        "data/atm-bench/atm-bench.json",
        "data/atm-bench/atm-bench-hard.json",
        "data/raw_memory/image",
        "data/raw_memory/video",
    ):
        if not (source / relative).exists():
            parser.error(f"Missing source: {source / relative}")
    counts = build_atm(source, args.data_root.resolve(), media_mode="clone")
    manifest = json.loads((root / "manifest.json").read_text())
    batch_media(root, manifest)
    manifest["album_name"] = "ATM-Bench 媒体版 · 631题 / 困难23题"
    manifest["evaluation_notes"] = {
        "scope": "media-only subset; not official full ATM-Bench reproduction",
        "media_corpus": "all 3759 images and 533 videos, including distractors",
        "excluded": "Email-dependent questions and one invalid list answer; see audit/exclusions.json",
        "delivery_limit": 6,
        "delivery_policy": "Delivery cap unchanged; targets above six have constrained recall.",
        "memory_source": "raw media; official preprocessed memory is not imported",
    }
    write_json(root / "manifest.json", manifest)
    albums = JsonAlbumSource(args.data_root.resolve(), Path("/nonexistent/task-action-policy.json"))
    limits = []
    for split in manifest["qa_sets"]:
        rows = albums.qa_rows(root.name, split)
        for row in rows:
            targets = row["evaluation"].get("target_media_refs") or row["retrieval_media_refs"]
            if len(targets) > 6:
                limits.append(
                    {
                        "qa_set": split,
                        "qa_id": row["qa_id"],
                        "required_media": len(targets),
                        "delivery_cap": 6,
                    }
                )
        for collection in ("photos", "videos"):
            for relative in manifest[collection]:
                if not albums.media_file(root.name, collection, relative.split("/", 1)[1]):
                    raise ValueError(f"Media is not accessible: {relative}")
        integrity = albums.integrity(root.name, manifest, split)
        if integrity["missing_files"]:
            raise ValueError(integrity["missing_files"])
    write_json(root / "audit/delivery-limit.json", limits)
    report = {
        "album": str(root),
        "counts_by_type": counts,
        "questions_above_delivery_cap": len(limits),
        "copied_media_unchanged": True,
        "validation": "passed",
    }
    write_json(root / "audit/registration.json", report)
    (root / "README.md").write_text(
        "# ATM-Bench 媒体版\n\n"
        "完整原始媒体库：3759图片、533视频。主集631题，困难集23题；保留原文与原始ID。\n\n"
        "主集媒体题原有632题；1题官方列表答案含缺失媒体，单独排除并记录于audit/exclusions.json。\n"
        "不包含依赖邮件的题目，不导入官方生成的记忆描述，不是官方完整1013题复现。\n\n"
        "现有最多交付6条媒体的限制不变。超出限制的题目见audit/delivery-limit.json；"
        "其召回率受交付上限约束。列表题、数值题、开放题使用项目已有ATM分类评分器。\n\n"
        "页面选择本数据集和题集，首次使用全链路模式；构建成功后才可复用。"
        "建议先使用项目smoke脚本验证小批流程，避免直接导入全部媒体。\n\n"
        "媒体为独立文件（优先APFS克隆），源数据保持不变。数据许可见原数据集卡；仅限许可范围使用。\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
