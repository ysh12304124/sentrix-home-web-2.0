"""在固定10题ATM小测上补充六道邮件题，生成独立16题版本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from photobench.domain.datasets.validation import validate_targets
from photobench.domain.evaluation.contract import validate_evaluation

# 保留官方原题和评分定义；额外覆盖来源列表交付及图文关联。
SELECTION = [
    ("0f49a8fe-59a7-4c8b-abf0-cb3ee6671db4", "atm-bench", "单封邮件事实与原文引用"),
    ("ffb30df6-4265-44db-8bf4-edc6e3745445", "atm-bench", "邮件日期与题干时间定位"),
    ("ad27afed-8fed-4132-9f69-7594cd73e166", "atm-bench", "跨三封邮件汇总金额与币种"),
    ("8dac62f4-f877-4216-85d4-cc7532b3e563", "atm-bench", "区分订单总价与单品价格，证据不足拒答"),
    ("6d857c33-c8ff-41be-a3b5-cfb66788fc0a", "atm-bench", "邮件ID列表交付与来源集合评分"),
    (
        "ef912ea0-634f-4c1f-88d5-9e0543708258",
        "atm-bench-hard",
        "两封预约邮件关联三张照片，分别核媒体交付和邮件引用",
    ),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(path: Path) -> list[tuple[dict[str, Any], bytes]]:
    return [(json.loads(line), line) for line in path.read_bytes().splitlines(keepends=True) if line.strip()]


def write_readme(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text())
    rows = [row for row, _ in lines(output / manifest["qa_sets"]["atm-smoke-16q"])]
    selection = json.loads((output / "audit/selection.json").read_text())
    matrix = "\n".join(
        f"| {index} | {entry['qtype']} | {entry['capability']} | `{entry['qa_id']}` |"
        for index, entry in enumerate(selection, 11)
    )
    (output / "README.md").write_text(
        f"""# ATM-Bench 16题小测

从原10题扩充六道涉邮件题：数值5题、列表5题、开放6题；原有10题与三个旧子集逐字节保留。
材料为16张图片、3个视频、9封邮件，共28份，均为独立普通文件，无目录外软链接。

## 使用入口

| 题集 | 题数 | 用途 |
|---|---:|---|
| atm-smoke-16q | 16 | 默认入口，图视频与邮件综合小测 |
| atm-smoke-email-6q | 6 | 本轮新增邮件与图文关联题 |
| atm-smoke-10q | 10 | 原10题回归 |
| atm-smoke-main-6q | 6 | 原主集题目 |
| atm-smoke-hard-4q | 4 | 原Hard题目 |

以上入口共享28份材料；旧10题包保留原16份图视频，历史结果口径不变。
本集用于全链路功能检查，缩小资料库降低了检索难度，成绩只描述本小测。

## 新增题型与能力

| 序号 | 官方题型 | 检查内容 | 原题标识 |
|---|---|---|---|
{matrix}

邮件列表题要求交付来源ID，正文写出ID不能代替来源交付；Fancett题要求交付三张图片，另独立统计两封邮件的候选、阅读和最终引用。硬盘价格题保留官方开放题/拒答参考答案，订单总价不能充当单品价格。Cairo题三封邮件总价64.08+187.99+180.00=432.07美元。

## 导入与维护

相册ID为`{output.name}`，默认题集为`atm-smoke-16q`。图、视频、邮件分别按manifest导入；documents/source-metadata.jsonl逐封保存来源ID、时间、标题、摘要及官方原对象。
`originals/emails.json`为九封邮件的官方格式JSON材料包，仅含id/timestamp/short_summary/detail；可用于邮件导入接口验证。问答与GT保存在qa目录，未写入邮件正文或摘要。

构建时在Sentrix代码根设置`PYTHONPATH=apps/photobench/src:apps/api/src`，
使用`.venv/bin/python scripts/extend_atm_smoke.py`，参数为：
`--base <原10题包> --sources <完整图视频邮件包> --output <新目录>/atm-bench-smoke16`。
输出目录需为新目录。
审核与SHA见audit/selection.json、build-report.json、material-sha256.json和preflight.json。生成器保持源QA行与媒体字节，保留原始评分定义和内容指纹。未启动真实模型测评。
""",
        encoding="utf-8",
    )
    table = "\n".join(
        f"| {i} | {row['evaluation']['source_qtype']} | {row['question'].replace('|', '｜').strip()} | "
        f"{row['answer'].replace('|', '｜').replace(chr(10), '<br>')} |"
        for i, row in enumerate(rows, 1)
    )
    (output / "QA-overview.md").write_text(
        "# 16题问题与参考答案\n\n仅供核对，不作为导入材料。\n\n"
        "| 序号 | 官方题型 | 原题 | 参考答案 |\n|---|---|---|---|\n" + table + "\n",
        encoding="utf-8",
    )


def build(base: Path, sources: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    original = json.loads((base / "manifest.json").read_text())
    full = json.loads((sources / "manifest.json").read_text())
    legacy = lines(base / original["qa_sets"]["atm-smoke-10q"])
    assert len(legacy) == 10
    added, selection = [], []
    for identifier, suite, capability in SELECTION:
        row, raw = next(
            (row, raw)
            for row, raw in lines(sources / full["qa_sets"][suite])
            if row["source_qa_id"] == identifier
        )
        added.append((row, raw))
        selection.append(
            {
                "qa_id": row["qa_id"],
                "source_qa_set": suite,
                "qtype": row["evaluation"]["source_qtype"],
                "capability": capability,
                "source_row_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    selected = legacy + added
    assert len({row["qa_id"] for row, _ in selected}) == 16
    source_ids = {
        ref["source_id"]
        for row, _ in added
        for ref in [
            *row["retrieval_source_refs"],
            *row["answer_evidence_source_refs"],
            *row["evaluation"].get("target_source_refs", []),
        ]
    }
    metadata = [
        row
        for row, _ in lines(sources / "documents/source-metadata.jsonl")
        if row["external_source_id"] in source_ids
    ]
    assert {row["external_source_id"] for row in metadata} == source_ids
    file_origins = {name: base for key in ("photos", "videos") for name in original[key]}
    for row, _ in added:
        for ref in [
            *row["retrieval_media_refs"],
            *row["answer_evidence_media_refs"],
            *row["evaluation"].get("target_media_refs", []),
        ]:
            folder = "photos" if ref["media_type"] == "image" else "videos"
            name = next(name for name in full[folder] if Path(name).stem == Path(ref["media_id"]).stem)
            file_origins.setdefault(name, sources)
    for row in metadata:
        file_origins["documents/" + row["file_name"]] = sources
    assert all((root / name).is_file() for name, root in file_origins.items())
    output.mkdir(parents=True)
    for folder in ("qa", "audit", "photos", "videos", "documents", "originals"):
        (output / folder).mkdir()
    hashes = {}
    for name, root in sorted(file_origins.items()):
        shutil.copy2(root / name, output / name)
        hashes[name] = sha(output / name)
        assert hashes[name] == sha(root / name)
    manifest = {
        "album_id": output.name,
        "album_name": "ATM-Bench 小测 · 16题 · 图视频与邮件",
        **{
            key: sorted(name for name in file_origins if name.startswith(key + "/"))
            for key in ("photos", "videos", "documents")
        },
        "document_source_refs": [
            {"source_type": "email", "source_id": value} for value in sorted(source_ids)
        ],
        "faces": [],
        "qa_sets": {},
        "qa_set_versions": {},
        "benchmark_id": "atm-bench",
        "source_protocol": "source-citations-v1",
        "usage": "smoke-test-only",
    }
    suites = {"atm-smoke-16q": selected}
    suites.update({key: lines(base / name) for key, name in original["qa_sets"].items()})
    suites["atm-smoke-email-6q"] = added
    preflight = {}
    for key, items in suites.items():
        relative = "qa/" + key + ".jsonl"
        (output / relative).write_bytes(b"".join(raw for _, raw in items))
        rows = [row for row, _ in items]
        for row in rows:
            validate_evaluation(row)
        preflight[key] = validate_targets(rows, manifest)
        assert preflight[key]["error_count"] == 0, preflight[key]
        manifest["qa_sets"][key] = relative
        manifest["qa_set_versions"][key] = {
            "qa_sha256": sha(output / relative),
            "count": len(rows),
            "evaluation_version": rows[0]["evaluation"]["version"],
            "schema_version": "typed-v1",
        }
    (output / "documents/source-metadata.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in metadata), encoding="utf-8"
    )
    for folder in ("photos", "videos"):
        selected_metadata = [
            row
            for row, _ in lines(sources / folder / "source-metadata.jsonl")
            if folder + "/" + row["file_name"] in file_origins
        ]
        assert len(selected_metadata) == len(manifest[folder])
        (output / folder / "source-metadata.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected_metadata), encoding="utf-8"
        )
    write_json(output / "originals/emails.json", [row["metadata"]["original_record"] for row in metadata])
    report = {
        "question_count": 16,
        "legacy_questions_unchanged": 10,
        "added_questions": 6,
        "qtypes": dict(Counter(row["evaluation"]["source_qtype"] for row, _ in selected)),
        "added_qtypes": dict(Counter(row["evaluation"]["source_qtype"] for row, _ in added)),
        "counts": {key: len(manifest[key]) for key in ("photos", "videos", "documents")},
        "qa_rows_byte_identical": True,
        "materials_sha256_identical": True,
        "sources": {str(root): sha(root / "manifest.json") for root in (base, sources)},
        "all_target_and_evidence_refs_included": True,
        "symlinks": False,
    }
    for path, value in [
        ("manifest.json", manifest),
        ("audit/build-report.json", report),
        ("audit/selection.json", selection),
        ("audit/preflight.json", preflight),
        ("audit/material-sha256.json", hashes),
    ]:
        write_json(output / path, value)
    shutil.copy2(base / "audit/selection.json", output / "audit/legacy-selection.json")
    write_readme(output)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.sources, args.output), ensure_ascii=False, indent=2))
