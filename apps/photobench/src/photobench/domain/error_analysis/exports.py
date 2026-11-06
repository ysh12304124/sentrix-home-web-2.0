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
from photobench.domain.error_analysis.html_dashboard import score_labels
from photobench.domain.error_analysis.html_report import document as _document
from photobench.domain.error_analysis.html_report import render_report
from photobench.domain.error_analysis.scoring import ATM_TYPES, project_case_scoring
from photobench.domain.error_analysis.taxonomy import QUALITY_DIMENSIONS

DISPLAY_LABELS = {
    **ATM_TYPES,
    "scoring_question_type": "ATM原生题型",
    "gt_conflict": "GT证据冲突",
    "service_error": "服务请求失败",
    "execution_failure": "答题执行失败",
    "context_budget": "上下文或预算不足",
    "format_error": "输出格式错误",
    "tool_failure": "工具调用失败",
    "retrieval_gap": "检索证据不足",
    "incorrect_answer": "回答错误",
    "unsupported_claim": "无依据推断",
    "incomplete_answer": "回答不完整",
    "judge_failure": "评分失败",
    "record_missing": "执行记录缺失",
    "no_issue": "未发现明确问题",
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
    "normal": "正常结束",
    "failed": "执行失败",
    "recovered": "已恢复",
    "not_recovered": "未恢复",
    "unrecovered": "未恢复",
    "single": "单轮",
    "multi": "多轮",
    "image": "图片",
    "video": "视频",
    "mixed": "图片与视频混合",
    "content_unit": "统计单位",
    "qa": "按题",
    "turn": "按轮",
    "execution": "执行结果",
    "score": "质量分",
    "ability_taxonomy_version": "能力标签版本",
    "quality_score": "原质量评分",
    "trace": "调用轨迹",
    "gt": "参考答案",
    "retrieval_gt": "检索GT",
    "judge_call_records": "Judge调用记录",
    "tool_model_call_records": "工具内部模型调用记录",
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
SCORE_LABELS = {"2": "正确", "1": "部分正确／集合未满分", "0": "错误", "None": "未评分"}


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


def export_report(
    report: dict[str, Any], cases: Sequence[dict[str, Any]], format: str, filters: Mapping[str, Any]
) -> str:
    cases = [project_case_scoring(case) for case in cases]
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
                    "scoring_question_type",
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
                    "evaluation_result",
                    "evaluation_definition",
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
                        c["dimensions"].get("scoring_question_type", "unknown"),
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
                        json.dumps(c.get("evaluation_result"), ensure_ascii=False),
                        json.dumps(c.get("evaluation"), ensure_ascii=False),
                    ]
                    for c in cases
                ],
            ]
        )
    if format != "html":
        raise ValueError("format must be html, json or csv")
    display_scores = score_labels(cases) if any(c.get("evaluation") for c in cases) else SCORE_LABELS
    return render_report(report, cases, filters, labels, display_scores, QUALITY_DIMENSIONS)


def export_auxiliary(report: dict[str, Any], kind: str, format: str) -> str:
    """Comparison / AI artifacts keep factual provenance in each export."""
    if format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if kind == "ai_compare":
        if format == "html":
            raise ValueError("AI对比请在页面中查看执行轨迹，不提供HTML导出。")
        if format == "csv":
            return _csv(
                [
                    ["qa_id", "valid", "result", "error"],
                    *[
                        [
                            c["qa_id"],
                            c["valid"],
                            json.dumps(c.get("result"), ensure_ascii=False),
                            c.get("error"),
                        ]
                        for c in report["cases"]
                    ],
                ]
            )
        raise ValueError("unknown format")
    if kind == "ai" and report.get("schema_version") != 3:
        message = "此分析为旧版格式，请重新生成分析。"
        if format == "html":
            return _document("<p>" + message + "</p>")
        if format == "csv":
            return _csv([["提示"], [message]])
        raise ValueError("unknown format")
    if kind == "ai":
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
        diagnoses = [
            (c, c["result"]["diagnosis"])
            for c in report["cases"]
            if c["valid"] and c["result"].get("diagnosis")
        ]
        if format == "csv":
            return _csv(
                [
                    ["scope_or_index", "qa_id", "kind", "text", "evidence_refs", "limitations"],
                    ["metadata", "", "source_and_coverage", json.dumps(meta, ensure_ascii=False), "", ""],
                    *[
                        [
                            str(c["index"]),
                            c["qa_id"],
                            "diagnosis",
                            json.dumps(d, ensure_ascii=False),
                            ";".join(d["evidence_refs"]),
                            d["limitations"],
                        ]
                        for c, d in diagnoses
                    ],
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
            body = "<h2>AI 逐题诊断与汇总</h2><p>" + _escape(report["note"]) + "</p>"
            body += _table(["指标", "数量"], [[k, v] for k, v in report["coverage"].items()])
            if report.get("summary_error"):
                body += "<p>汇总生成失败：" + _escape(report["summary_error"]) + "</p>"
            for case, diagnosis in diagnoses:
                body += (
                    "<section><h3>"
                    + _escape(case["qa_id"])
                    + " · 诊断结论</h3><p>"
                    + _escape(diagnosis["summary"])
                    + "</p>"
                )
                body += (
                    _table(
                        ["项目", "内容"],
                        [
                            [
                                "错误类型",
                                " / ".join(
                                    DISPLAY_LABELS.get(code, code) for code in diagnosis["error_types"]
                                ),
                            ],
                            ["原因分析", diagnosis["cause"]],
                            [
                                "确定程度",
                                {"confirmed": "记录支持", "likely": "原因推测", "unknown": "原因未确认"}[
                                    diagnosis["certainty"]
                                ],
                            ],
                            ["处理建议", diagnosis["next_step"]],
                            ["判断限制", diagnosis["limitations"]],
                            ["GT图片来源", json.dumps(case.get("gt_images", []), ensure_ascii=False)],
                            [
                                "GT证据缺口",
                                json.dumps(case.get("gt_missing_evidence", []), ensure_ascii=False),
                            ],
                            ["关键依据", " / ".join(diagnosis["evidence_refs"])],
                        ],
                    )
                    + "</section>"
                )
            if diagnoses:
                body += "<details><summary>全部分析依据</summary>"
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
            if diagnoses:
                body += "</details>"
            body += (
                "<details><summary>来源、失败记录与实际用量</summary><pre>"
                + _escape(json.dumps({**meta, "cases": report["cases"]}, ensure_ascii=False, indent=2))
                + "</pre></details>"
            )
            return _document(body)
        raise ValueError("unknown format")
    rows = report.get("rows", [])
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
            + "逐题对比"
            + "</h2><pre>"
            + _escape(json.dumps(report, ensure_ascii=False, indent=2))
            + "</pre>"
        )
    raise ValueError("unknown format")
