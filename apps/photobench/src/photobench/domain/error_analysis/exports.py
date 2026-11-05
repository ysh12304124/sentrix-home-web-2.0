"""Offline reports: escaped HTML, native charts and spreadsheet-safe CSV."""

from __future__ import annotations

import csv
import html
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.datasets.ability_taxonomy import LEGACY_ABILITY_VERSION, ability_labels
from photobench.domain.datasets.qa_taxonomy import taxonomy_document
from photobench.domain.error_analysis.taxonomy import QUALITY_DIMENSIONS

DISPLAY_LABELS = {
    "task_type": "原始任务标签",
    "content_type": "内容题型",
    "test_scenario": "测试场景",
    "question_type": "题目形式",
    "angle": "考察角度",
    "difficulty": "难度",
    "answerability": "可回答性",
    "media_type": "媒体类型",
    "conversation_type": "对话形式",
    "expected_action": "预期行为",
    "T1_retrieve_media": "媒体检索",
    "T1_media_retrieval": "媒体检索",
    "T2_fact_qa": "事实问答",
    "T3_enumeration": "统计枚举",
    "T5_event_summary": "事件总结",
    "behavior": "行为判断",
    "event_memory_qa": "事件记忆问答",
    "single_evidence_memory_qa": "单证据问答",
    "multi_turn_clarify": "多轮澄清",
    "evidence_insufficient": "证据不足",
    "prompt_injection": "指令注入",
    "media_retrieval": "媒体检索",
    "event_summary": "事件总结",
    "clarification": "澄清提问",
    "fact_retrieval": "事实检索",
    "event_time": "事件时间",
    "event_location": "事件地点",
    "unanswerable_fact": "不可回答事实",
    "answerable_after_clarification": "澄清后可回答",
    "needs_clarification": "需澄清",
    "unknown": "未标注／未知",
    "answer": "直接回答",
    "refuse": "拒答",
    "clarify": "澄清",
    "none": "未作答",
}
_taxonomy = taxonomy_document()
DISPLAY_LABELS.update({key: val["label"] for key, val in _taxonomy["tasks"].items()})
DISPLAY_LABELS.update(
    {key: val for task in _taxonomy["tasks"].values() for key, val in task["subtypes"].items()}
)
for _section in ("actions", "answerability", "difficulty", "special"):
    DISPLAY_LABELS.update(_taxonomy[_section])
DISPLAY_LABELS.update(_taxonomy["abilities"]["families"])
DISPLAY_LABELS.update(_taxonomy["abilities"]["types"])
DISPLAY_LABELS.update(_taxonomy["abilities"]["groups"])
DISPLAY_LABELS.update(ability_family="一级能力", ability_type="二级能力", benchmark_group="测试分组")
DISPLAY_LABELS["content_subtype"] = "细分题型"
SCORE_LABELS = {"2": "满分（2分）", "1": "部分完成（1分）", "0": "未完成（0分）", "None": "未评分"}


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "未记录"), quote=True)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{_escape(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{_escape(v)}</td>" for v in row) + "</tr>" for row in rows)
        + "</tbody></table>"
    )


def _csv(rows: Sequence[Sequence[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow(
            ["'" + str(x) if str(x or "").lstrip().startswith(("=", "+", "-", "@")) else x for x in row]
        )
    return out.getvalue()


def _document(body: str) -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>PhotoBench 错误分析</title><style>"
        "body{font:15px system-ui;max-width:1120px;margin:30px auto;padding:20px;color:#203040}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}"
        "details{border:1px solid #ccd5df;padding:12px;margin:12px 0}"
        "meter{width:40%;margin:8px}summary{cursor:pointer}li{margin:8px}"
        "table{border-collapse:collapse;width:100%;margin:16px 0}"
        "td,th{border:1px solid #ddd;text-align:left;padding:8px;overflow-wrap:anywhere}"
        ".stack{display:flex;width:90%;height:28px;background:#ddd;margin:8px 0}"
        ".stack span{min-width:0;overflow:hidden;color:#fff;text-align:center}"
        ".s0{background:#c44}.s1{background:#b80}.s2{background:#287c65}.sNone{background:#777}"
        "</style></head><body><h1>PhotoBench 错误分析</h1>" + body + "</body></html>"
    )


def export_report(
    report: dict[str, Any], cases: Sequence[dict[str, Any]], format: str, filters: Mapping[str, Any]
) -> str:
    labels = {
        **DISPLAY_LABELS,
        **(
            report.get("ability_labels")
            or (ability_labels([LEGACY_ABILITY_VERSION]) if report.get("rule_version") == 7 else {})
        ),
    }
    if format == "json":
        return json.dumps(
            {"report": report, "filters": dict(filters), "cases": cases}, ensure_ascii=False, indent=2
        )
    if format == "csv":
        return _csv(
            [
                [
                    "qa_id",
                    "question",
                    "score",
                    "execution",
                    "question_type",
                    "task_type",
                    "content_type",
                    "content_subtype",
                    "ability_family",
                    "ability_type",
                    "benchmark_group",
                    "test_scenario",
                    "behavior_turns",
                    "angle",
                    "difficulty",
                    "outcome",
                    "errors",
                    "answer",
                    "reference_answer",
                    "ability_taxonomy_version",
                    "ability_family_label",
                    "ability_type_label",
                ],
                *[
                    [
                        c["qa_id"],
                        c["question"],
                        c["score"],
                        c["execution"],
                        c["dimensions"]["question_type"],
                        c["dimensions"].get("task_type", "unknown"),
                        c["dimensions"].get("content_type", "unknown"),
                        c["dimensions"].get("content_subtype", "unknown"),
                        c["dimensions"].get("ability_family", "unknown"),
                        c["dimensions"].get("ability_type", "unknown"),
                        c["dimensions"].get("benchmark_group", "unknown"),
                        c["dimensions"].get("test_scenario", "unknown"),
                        json.dumps(c["turns"], ensure_ascii=False),
                        c["dimensions"].get("angle", "unknown"),
                        c["dimensions"].get("difficulty", "unknown"),
                        SCORE_LABELS[str(c["score"])],
                        ";".join(e["code"] for e in c["events"]),
                        c["answer"],
                        c["reference_answer"],
                        c.get("ability_taxonomy_version"),
                        " / ".join(
                            c.get("ability_labels", labels).get(v, v)
                            for v in c["dimensions"].get("ability_family", "unknown").split(" / ")
                        ),
                        " / ".join(
                            c.get("ability_labels", labels).get(v, v)
                            for v in c["dimensions"].get("ability_type", "unknown").split(" / ")
                        ),
                    ]
                    for c in cases
                ],
            ]
        )
    if format != "html":
        raise ValueError("format must be html, json or csv")
    source = {
        "source": report["source"],
        "ability_taxonomy_versions": report.get("ability_taxonomy_versions"),
        "rule_version": report["rule_version"],
        "generated_at": report.get("generated_at"),
        "gt_fingerprint": report["gt_fingerprint"],
        "annotation_fingerprint": report.get("annotation_fingerprint"),
        "result_fingerprint": report.get("result_fingerprint"),
        "stale": report.get("stale", False),
        "filters": dict(filters),
    }
    body = (
        f"<p>全部样本 {report.get('all_items', report['item_count'])}；本次筛选 {len(cases)} 题。"
        "统计图与案例使用相同筛选条件。离线可读，无网络依赖。</p>"
        "<h2>来源与筛选</h2><pre>" + _escape(json.dumps(source, ensure_ascii=False, indent=2)) + "</pre>"
        "<h2>统计口径</h2><ul>" + "".join(f"<li>{_escape(x)}</li>" for x in report["definitions"]) + "</ul>"
        "<h2>总览与有效分母</h2>"
        + _table(["指标", "数量"], [[k, v] for k, v in report["counts"].items()])
        + _table(["分母", "数量"], [[k, v] for k, v in report["denominators"].items()])
    )
    body += (
        "<h2>任务完成质量（当前范围）</h2><p>2分：完成，1分：部分完成，0分：未完成。适用于回答、拒答和澄清；行为是否符合预期另行统计。</p>"
        + _table(
            ["任务完成质量", "题数"],
            [[label, report["counts"].get("score_" + value, 0)] for value, label in SCORE_LABELS.items()],
        )
    )
    maximum = max((r["affected_items"] for r in report["errors"]), default=1)
    body += "<h2>运行异常分布（独立于质量分，类别可重叠）</h2>"
    for r in report["errors"]:
        body += (
            f'<div>{_escape(r["label"])} <meter min="0" max="{maximum}" '
            f'value="{r["affected_items"]}"></meter> {r["affected_items"]}题 / {r["events"]}次；'
            f"最终执行失败 {r['final_failed_items']}题，已恢复 {r['recovered_items']}题</div>"
        )
    unit = "轮" if filters.get("content_unit") == "turn" else "题"
    groups = report.get("turn_groups", {}) if unit == "轮" else report["groups"]
    for dim in QUALITY_DIMENSIONS:
        rows = groups.get(dim, [])
        body += f"<h2>样本分布与表现：{_escape(labels.get(dim, dim))}</h2>"
        for row in rows:
            label = _escape(
                "未标注内容题型"
                if dim == "content_type" and row["value"] == "unknown"
                else labels.get(row["value"], row["value"])
            )
            body += (
                f"<p>{label} · 共{row['total']}{unit} · 未评分{row.get('score_None', 0)}</p>"
                '<div class="stack">'
            )
            for score in ["2", "1", "0", "None"]:
                n = row.get("score_" + score, 0)
                width = n / max(1, row["total"]) * 100
                label = SCORE_LABELS[score]
                body += f'<span class="s{score}" style="width:{width:.3f}%">{label} {n}</span>'
            body += "</div>"
    if report["rule_version"] < 4:
        body += "<p>旧分类口径：请重新生成分析以查看内容题型与回答行为。</p>"
    body += "<h2>按得分看内容题型</h2>"
    content = groups.get("content_type", [])
    for score, label in SCORE_LABELS.items():
        total = report["counts"].get(("turn_score_" if unit == "轮" else "score_") + score, 0)
        missing = sum(
            r.get("score_" + score, 0)
            for r in content
            if r["value"] in {"unknown", "not_applicable", "pending_review"}
        )
        valid = total - missing
        body += (
            f"<h3>{label}</h3><p>全部{total}{unit}；有效内容题型{valid}{unit}；不适用、待核或未记录{missing}{unit}。"
            f"占比使用有效内容{unit}。</p>"
        )
        body += _table(
            ["内容题型", f"{unit}数", f"占有效内容{unit}的比例"],
            [
                [
                    labels.get(r["value"], r["value"]),
                    r.get("score_" + score, 0),
                    f"{r.get('score_' + score, 0) / valid * 100:.1f}%" if valid else "未覆盖",
                ]
                for r in content
                if r["value"] not in {"unknown", "not_applicable", "pending_review"}
            ],
        )
    if report["rule_version"] >= 7:
        prefix = "turn" if unit == "轮" else "qa"
        for dimension, title, denominator in (
            ("ability_family", "一级能力", "ability_family" if report["rule_version"] >= 8 else "ability"),
            ("ability_type", "二级能力", "ability"),
        ):
            body += f"<h2>按得分看{title}（题或轮去重）</h2>"
            for score, label in SCORE_LABELS.items():
                valid = report["counts"].get(f"{prefix}_{denominator}_score_{score}", 0)
                body += f"<h3>{label}</h3><p>有效{valid}{unit}；多标签比例可超过100%。</p>"
                body += _table(
                    ["能力", f"{unit}数", "占有效能力样本"],
                    [
                        [
                            labels.get(r["value"], r["value"]),
                            r.get("score_" + score, 0),
                            f"{r.get('score_' + score, 0) / valid * 100:.1f}%" if valid else "未覆盖",
                        ]
                        for r in groups.get(dimension, [])
                        if r["value"] not in {"unknown", "not_applicable"}
                    ],
                )
    if report["rule_version"] >= 7:
        body += "<h2>一级能力 × 运行异常（按题）</h2>" + _table(
            ["一级能力", "类别（可重叠）", "受影响题数"],
            [
                [labels.get(r["ability_family"], r["ability_family"]), r["code"], r["count"]]
                for r in report.get("ability_heatmap", [])
            ],
        )
    body += "<h2>原内容题型 × 运行异常</h2>" + _table(
        ["内容题型", "类别（可重叠）", "受影响题数"],
        [
            [
                labels.get(
                    r.get("content_type", r.get("question_type", "unknown")),
                    r.get("content_type", r.get("question_type", "unknown")),
                ),
                r["code"],
                r["count"],
            ]
            for r in report["heatmap"]
        ],
    )
    body += "<h2>回答行为（按对话轮）</h2><p>行为是否符合预期与质量分独立。点击案例筛选按题去重。</p>"
    for key, title in (("behavior_groups", "预期行为分布"), ("behavior_scenarios", "测试场景")):
        body += f"<h3>{title}</h3>" + _table(
            [
                "类别",
                "轮数",
                "占全部轮数" if key == "behavior_groups" else "场景可重叠",
                "符合",
                "不符合",
                "未判定",
                "符合率 / 有效判定",
            ],
            [
                [
                    labels.get(r["value"], r["value"]),
                    r["total"],
                    f"{r['total'] / report['denominators']['turns'] * 100:.1f}%"
                    if report["denominators"]["turns"] and key == "behavior_groups"
                    else "—",
                    r["correct"],
                    r["incorrect"],
                    r["missing"],
                    (f"{r['correct'] / r['valid'] * 100:.1f}%" if r["valid"] else "未覆盖")
                    + f" / {r['valid']}轮",
                ]
                for r in report.get(key, [])
            ],
        )
    body += "<h2>预期 × 实际行为（全部对话轮，包含未判定）</h2>" + _table(
        ["预期", "实际", "轮数"],
        [
            [
                labels.get(r["expected"], r["expected"]),
                labels.get(r["actual"], r["actual"]),
                r["count"],
            ]
            for r in report["behavior_matrix"]
        ],
    )
    body += "<h2>筛选案例</h2>"
    for c in cases:
        body += (
            f"<details><summary>{_escape(c['qa_id'])} · {_escape(c['execution'])} · "
            f"分数{_escape(c['score'])}</summary>"
            f"<h3>问题</h3><pre>{_escape(c['question'])}</pre>"
            f"<h3>参考答案</h3><pre>{_escape(c['reference_answer'])}</pre>"
            f"<h3>模型回答</h3><pre>{_escape(c['answer'])}</pre>"
            f"<p>摘要文本截短：{c['text_truncated']}；完整轨迹另行导出。</p><ul>"
        )
        for e in c["events"]:
            body += (
                f"<li>{_escape(e['event_id'])} · 轮{e['turn']} / {_escape(e.get('step_id'))} · "
                f"{_escape(e['label'])} · {_escape(e['recovery'])}"
                f"<pre>{_escape(json.dumps(e, ensure_ascii=False, indent=2))}</pre></li>"
            )
        body += (
            "</ul><pre>" + _escape(json.dumps(c["turns"], ensure_ascii=False, indent=2)) + "</pre></details>"
        )
    return _document(body)


def export_auxiliary(report: dict[str, Any], kind: str, format: str) -> str:
    """Comparison / AI artifacts keep factual provenance in each export."""
    if format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if kind == "ai" and report.get("schema_version") == 2:
        meta = {
            k: report.get(k)
            for k in (
                "scope",
                "coverage",
                "issues",
                "facts",
                "model",
                "provider_id",
                "prompt_version",
                "configuration_fingerprint",
                "requests",
                "summary_status",
                "summary_error",
            )
        }
        claim_rows = [("overview", "", c) for c in (report.get("overview") or {}).get("claims", [])]
        claim_rows += [
            (str(case["index"]), case["qa_id"], c)
            for case in report["cases"]
            if case["valid"]
            for c in case["result"].get("claims", [])
        ]
        if format == "csv":
            return _csv(
                [
                    ["scope_or_index", "qa_id", "kind", "text", "evidence_refs", "limitations"],
                    ["metadata", "", "source_and_coverage", json.dumps(meta, ensure_ascii=False), "", ""],
                    *[
                        [i, qa, c["kind"], c["text"], ";".join(c["evidence_refs"]), c["limitations"]]
                        for i, qa, c in claim_rows
                    ],
                    *[
                        [str(c["index"]), c["qa_id"], c.get("status"), c.get("error"), "", ""]
                        for c in report["cases"]
                        if not c["valid"]
                    ],
                ]
            )
        if format == "html":
            body = "<h2>AI 问题总览与逐题诊断</h2><p>" + _escape(report["note"]) + "</p>"
            body += _table(["指标", "数量"], [[k, v] for k, v in report["coverage"].items()])
            if report.get("summary_error"):
                body += "<p>汇总失败：" + _escape(report["summary_error"]) + "</p>"
            for _i, qa, c in claim_rows:
                body += (
                    "<section><h3>"
                    + _escape(qa or "问题总览")
                    + " · "
                    + _escape(
                        {
                            "observation": "记录观察",
                            "hypothesis": "可能原因（待验证）",
                            "recommendation": "建议验证",
                        }[c["kind"]]
                    )
                    + "</h3><p>"
                    + _escape(c["text"])
                    + "</p><p>限制："
                    + _escape(c["limitations"])
                    + "</p><p>证据："
                    + _escape(" / ".join(c["evidence_refs"]))
                    + "</p></section>"
                )
            body += (
                "<details><summary>来源、失败记录与实际用量</summary><pre>"
                + _escape(json.dumps({**meta, "cases": report["cases"]}, ensure_ascii=False, indent=2))
                + "</pre></details>"
            )
            return _document(body)
        raise ValueError("unknown format")
    rows = report.get("rows", []) if kind == "compare" else report.get("cases", [])
    cells = [["qa_id", "pairing", "quality_change", "execution_changed", "valid", "details"]]
    cells.extend(
        [
            [
                r.get("qa_id"),
                r.get("pairing"),
                r.get("quality_change"),
                r.get("execution_changed"),
                r.get("valid"),
                json.dumps(r, ensure_ascii=False),
            ]
            for r in rows
        ]
    )
    if format == "csv":
        return _csv(cells)
    if format == "html":
        return _document(
            "<h2>"
            + ("逐题对比" if kind == "compare" else "AI 诊断（建议）")
            + "</h2><pre>"
            + _escape(json.dumps(report, ensure_ascii=False, indent=2))
            + "</pre>"
        )
    raise ValueError("unknown format")
