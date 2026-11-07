"""Build the fixed 10-question ATM smoke album from a typed ATM media candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

SELECTION = [
    ("4028b70f-061e-4627-839e-9252ac387157", "多张票据 OCR、年度金额汇总"),
    ("25012e70-fc62-42a7-b5a9-5fd5873457a4", "事件定位、日期读取与归一化"),
    ("ed583465-4d70-488a-aad1-795b1780df41", "票据 OCR、均摊计算与币种"),
    ("580a4fee-8595-4453-8368-f52eec0a3954", "中文品牌、跨日多图完整召回"),
    ("dd385f75-d8e6-4b5b-94de-1b38669a87ca", "视频场景检索与视频交付"),
    ("99c89358-c2fc-4f70-aef2-22bb0fefdbf3", "建筑与天气描述、单视频检索"),
    ("17b8b612-213a-4ab3-87f9-356b8cf0d7e5", "图视频联合定位、酒店品牌与关联回答"),
    ("92e4edaa-5b26-44a5-80d2-91acf53b3b9c", "餐厅地点、跨日事件比较与依据总结"),
    ("2314e071-b2b5-4892-a30e-0596c5eafda6", "视频细节识别、机型和航空公司"),
    ("4e057a57-6ece-488c-88d1-70730594e71f", "证据不足时拒绝补猜机构归属"),
]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def build(source: Path, output: Path) -> dict[str, Any]:
    manifest = json.loads((source / "manifest.json").read_text())
    rows = {}
    for split, relative in manifest["qa_sets"].items():
        for line in (source / relative).read_bytes().splitlines(keepends=True):
            row = json.loads(line)
            rows[row["source_qa_id"]] = (row, line, split)
    selected = [rows[qa_id] for qa_id, _ in SELECTION]
    refs = set()
    for row, _, _ in selected:
        for ref in (
            row["retrieval_media_refs"]
            + row["answer_evidence_media_refs"]
            + row["evaluation"].get("target_media_refs", [])
        ):
            refs.add((ref["media_type"], ref["media_id"]))
    assert len(selected) == 10 and 0 < len(refs) < 20
    files = sorted(
        ("photos/" + media_id if kind == "image" else "videos/" + media_id + ".mp4")
        for kind, media_id in refs
    )
    assert all((source / name).is_file() for name in files)
    output.mkdir(parents=True, exist_ok=False)
    (output / "qa").mkdir()
    (output / "audit").mkdir()
    hashes = {}
    for name in files:
        target = output / name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source / name, target)
        hashes[name] = digest(target)
        assert hashes[name] == digest(source / name)
    suites = {
        "atm-smoke-10q": selected,
        "atm-smoke-main-6q": [item for item in selected if "main" in item[2]],
        "atm-smoke-hard-4q": [item for item in selected if "hard" in item[2]],
    }
    qa_sets, versions = {}, {}
    for key, items in suites.items():
        relative = "qa/" + key + ".jsonl"
        (output / relative).write_bytes(b"".join(item[1] for item in items))
        qa_sets[key] = relative
        versions[key] = {
            "qa_sha256": digest(output / relative),
            "count": len(items),
            "evaluation_version": selected[0][0]["evaluation"]["version"],
            "schema_version": "typed-v1",
        }
    result = {
        "album_id": output.name,
        "album_name": "ATM-Bench 小测 · 10题 / 16媒体",
        "photos": [f for f in files if f.startswith("photos/")],
        "videos": [f for f in files if f.startswith("videos/")],
        "faces": [],
        "qa_sets": qa_sets,
        "qa_set_versions": versions,
        "benchmark_id": "atm-bench",
        "source_commit": manifest["source_commit"],
        "usage": "smoke-test-only",
    }
    provenance = [
        {
            "qa_id": row["qa_id"],
            "source_qa_id": row["source_qa_id"],
            "source_qa_set": split,
            "source_qtype": row["evaluation"]["source_qtype"],
            "capability": capability,
            "source_row_sha256": hashlib.sha256(line).hexdigest(),
        }
        for (row, line, split), (_, capability) in zip(selected, SELECTION, strict=True)
    ]
    report = {
        "question_count": 10,
        "image_count": len(result["photos"]),
        "video_count": len(result["videos"]),
        "media_count": len(files),
        "media_bytes": sum((output / f).stat().st_size for f in files),
        "types": dict(Counter(p["source_qtype"] for p in provenance)),
        "source": str(source.resolve()),
        "source_commit": manifest["source_commit"],
        "source_manifest_sha256": digest(source / "manifest.json"),
        "qa_rows_byte_identical": True,
        "media_sha256_identical": True,
        "all_target_and_evidence_refs_included": True,
        "symlinks": False,
    }
    for name, data in (
        ("manifest.json", result),
        ("audit/selection.json", provenance),
        ("audit/media-sha256.json", hashes),
        ("audit/build-report.json", report),
    ):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    matrix = "\n".join(
        f"| {i} | {p['source_qtype']} | {'hard' if 'hard' in p['source_qa_set'] else 'main'} | "
        f"{p['capability']} | `{p['source_qa_id']}` |"
        for i, p in enumerate(provenance, 1)
    )
    (output / "README.md").write_text(f"""# ATM-Bench 10题小测

10题，13张图片和3个视频，共16份媒体。数值3题、列表3题、开放4题；主集6题、hard 4题。

入口 `atm-smoke-10q` 运行全部10题；`atm-smoke-main-6q` 和 `atm-smoke-hard-4q` 可分别检查来源。
所有入口共用这16份媒体，不会导入完整4292媒体库。文件已复制物化，无跨目录软链接。
题干、GT、官方原题及评分定义逐行保留源候选字节；媒体时间、时区、GPS和文件内容原样保留。

这是导入、检索、作答和三类评分的流程冒烟集。缩小语料显著降低检索难度，复用证据有相关性，10题总分不能替代官方全库QS，也不足以评价完整能力或排名。每题其他媒体可充当干扰项；没有额外添加干扰文件。ATM没有选择题，本集不验证PhotoBench选择题匹配。

## 选题与覆盖

| 序号 | 官方题型 | 来源 | 检查能力 | 原题ID |
|---|---|---|---|---|
{matrix}

## 检查范围

13张图片联系表核看，重点核对票据金额50+70+70=190、均摊账单138.04/6、海报机构信息及酒店/餐厅可见文字；3个视频抽帧核看。抽帧检查不能替代完整视频事实审核。餐厅偏好与酒店关联沿用官方GT，记录其推断属性。

构建脚本为 `scripts/build_atm_smoke.py`，参数：
`--source <typed-candidate>/atm-bench-media --output <data-root>/atm-bench-smoke10`。
源提交 `{manifest["source_commit"]}`，逐题来源、SHA和构建报告见 `audit/`。本集未启动真实Agent测评。
""")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
