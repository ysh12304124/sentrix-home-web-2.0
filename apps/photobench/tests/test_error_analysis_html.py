"""Offline exports preserve saved evidence and the complete filtered scope."""

from __future__ import annotations

import copy
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from test_error_analysis import await_job, item, report, write_run

from photobench.domain.error_analysis.exports import export_report
from photobench.domain.error_analysis.html_report import behavior_heatmap
from photobench.domain.error_analysis.query import filtered_view, select_case
from photobench.domain.error_analysis.rules import case_from_item
from photobench.infrastructure.error_analysis.backend import FileAnalysisBackend


class ReportData(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.reading = False
        self.payload = ""
        self.scripts: list[dict[str, str | None]] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))
            self.reading = dict(attrs).get("id") == "report-data"

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.reading = False

    def handle_data(self, data: str) -> None:
        if self.reading:
            self.payload += data

    def cases(self) -> list[dict[str, Any]]:
        return json.loads(self.payload)["cases"]


def test_html_untrusted_turns_roundtrip_without_injected_markup():
    hostile = '</script><script src="https://evil.invalid/x.js">alert(1)</script>&\u2028'
    c = case_from_item(item(question=hostile, answer=hostile, reference_answer=hostile), 0)
    before = copy.deepcopy(c)
    exported = export_report(report([c]), [c], "html", {})
    parsed = ReportData(exported)
    assert parsed.cases() == [before]
    assert len(parsed.scripts) == 2 and all("src" not in s for s in parsed.scripts)
    assert "&lt;/script&gt;&lt;script src=&quot;" in exported
    assert c == before


def test_empty_scope_is_uncovered_and_embeds_no_cases():
    exported = export_report(report([]), [], "html", {})
    assert ReportData(exported).cases() == []
    assert "<strong>未覆盖</strong>" in exported and "<strong>NaN</strong>" not in exported
    assert "0.0%" not in exported  # no valid denominator to report a rate


def test_dashboard_separates_missing_labels_and_keeps_all_cases_in_collapsed_reader():
    cases = [case_from_item(item(qa_id=f"q{i}", score=2 if i else None), i) for i in range(32)]
    before = copy.deepcopy(cases)
    exported = export_report(report(cases), cases, "html", {})
    assert ReportData(exported).cases() == before
    assert '<details id="case-reader" class="reader-shell">' in exported
    assert 'id="reader-from"' in exported and 'id="reader-to"' in exported
    assert "数据覆盖与分析边界" not in exported
    assert 'id="coverage"' not in exported
    assert "样本分布与表现：" not in exported
    assert 'aria-label="任务质量分布：' in exported
    assert cases == before


def test_retrieval_charts_use_micro_counts_and_exclude_zero_gt():
    from photobench.domain.error_analysis.html_dashboard import retrieval

    cases = [case_from_item(item(qa_id=f"q{i}"), i) for i in range(3)]
    for case, counts in zip(cases, [(1, 2, 1), (0, 1, 3), (0, 7, 0)], strict=True):
        case["retrieval_counts"] = {
            "candidate": dict(zip(("matched", "predicted", "gt"), counts, strict=True))
        }
    before = copy.deepcopy(cases)
    exported = retrieval(cases)
    assert "覆盖2 / 3题" in exported
    assert "25.0%" in exported and "33.3%" in exported
    assert "命中 1 / GT 4" in exported and "命中 1 / 返回 3" in exported
    assert cases == before


def test_turn_composition_and_multiturn_do_not_count_unselected_context():
    from photobench.domain.error_analysis.html_dashboard import multiturn, score_composition

    case = case_from_item(
        item(
            runtime_turns=[
                {"message": "first", "judge": {"score": 0}, "expected_action": "clarify"},
                {"message": "second", "judge": {"score": 2}, "expected_action": "answer"},
            ]
        ),
        0,
    )
    case["turns"][0]["difficulty"] = ["unknown"]
    case["turns"][1]["difficulty"] = ["easy"]
    case["matched_turns"] = [0]
    case["turns"][0]["ability_type"] = ["P1"]
    case["turns"][1]["ability_type"] = ["P2"]
    exported = score_composition([case], {"content_unit": "turn"}, {})
    assert "0分 · 1轮" in exported and "2分 ·" not in exported
    assert 'data-value="P1"' in exported and 'data-value="P2"' not in exported
    assert "范围不完整 1题" in multiturn([case])


def test_behavior_heatmap_shows_row_percentages_and_keeps_unknown_actions():
    matrix = [
        dict(expected="clarify", actual="answer", count=3),
        dict(expected="clarify", actual="clarify", count=1),
        dict(expected="answer", actual="answer", count=2),
        dict(expected="unknown", actual="none", count=1),
    ]
    source = behavior_heatmap(matrix, {"clarify": "澄清", "answer": "回答", "unknown": "未记录"})
    assert 'aria-label="预期澄清，实际回答：3轮，占本行75.0%"' in source
    assert 'aria-label="预期回答，实际回答：2轮，占本行100.0%"' in source
    assert 'data-expected="unknown" data-actual="none"' in source
    assert 'data-count="0"' in source and " disabled" in source
    assert "<img" not in source and "<svg" not in source
    assert "当前范围未记录回答行为" in behavior_heatmap([], {})


def test_turn_quality_uses_selected_turns_and_keeps_context():
    c = case_from_item(
        item(
            runtime_turns=[
                {"message": "first", "judge": {"score": 0}, "expected_action": "clarify"},
                {"message": "second", "judge": {"score": 2}, "expected_action": "answer"},
            ]
        ),
        0,
    )
    filters = {"content_unit": "turn", "score": "0"}
    selected = select_case(c, filters)
    assert selected is not None
    summary = filtered_view([c], report([c]), filters, 1, 25)["summary"]
    exported = export_report(summary, [selected], "html", filters)
    assert "统计单位：轮" in exported and "缺分 0轮" in exported
    saved = ReportData(exported).cases()[0]
    assert saved["matched_turns"] == [0] and len(saved["turns"]) == 2
    assert [t["score"] for t in saved["turns"]] == [0, 2]


def test_backend_export_keeps_all_matches_and_marks_matching_turns(tmp_path: Path):
    rows = [
        item(
            qa_id=f"q{i}",
            runtime_turns=[
                {
                    "message": f"first-{i}",
                    "expected_action": "clarify",
                    "judge": {"score": 0},
                    "task_judge": {"actual_action": "answer", "correct": False},
                },
                {
                    "message": "second",
                    "expected_action": "answer",
                    "judge": {"score": 2},
                    "task_judge": {"actual_action": "answer", "correct": True},
                },
            ],
        )
        for i in range(121)
    ]
    rows[-1]["runtime_turns"][0]["task_judge"] = {"actual_action": "clarify", "correct": True}
    path = write_run(tmp_path, items=rows)
    original = (path / "results.jsonl").read_bytes()
    backend = FileAnalysisBackend(tmp_path)
    backend.start(lambda: False)
    try:
        job = backend.submit("r", "rules", {})
        assert await_job(backend, job["job_id"])["status"] == "completed"
        filters = {"expected_action": "clarify", "actual_action": "answer"}
        view = backend.view(job["job_id"], filters, 1, 25)
        saved = ReportData(backend.export(job["job_id"], "html", filters)).cases()
        assert len(saved) == view["total"] == 120
        assert {c["qa_id"] for c in saved} == {f"q{i}" for i in range(120)}
        assert all(c["matched_turns"] == [0] and len(c["turns"]) == 2 for c in saved)
        assert view["summary"]["denominators"]["turns"] == 120
        assert (path / "results.jsonl").read_bytes() == original
    finally:
        backend.close()


def test_saved_v2_safety_labels_project_consistently_without_rewriting_artifacts(tmp_path: Path):
    from test_ability_taxonomy import v2_labels

    from photobench.domain.error_analysis.taxonomy import project_case_abilities

    rows = [
        item(qa_id="h", **v2_labels([], "hallucination", "refuse")),
        item(qa_id="i", **v2_labels([], "injection", "refuse")),
        item(qa_id="ordinary", **v2_labels(["P1"])),
        item(qa_id="clarification", **v2_labels([], action="clarify")),
    ]
    write_run(tmp_path, items=rows)
    cases = [case_from_item(row, i) for i, row in enumerate(rows)]
    # Reproduce the persisted rule-10 V2 representation, not a newly generated report.
    for case in cases[:2]:
        case["dimension_labels"]["ability_type"] = ["not_applicable"]
        case["dimensions"]["ability_type"] = "not_applicable"
        for turn in case["turns"]:
            turn["ability_type"] = ["not_applicable"]
            turn.pop("ability_display_projection", None)
            turn.pop("saved_ability_type", None)
        case.pop("ability_display_projection", None)
        case.pop("saved_ability_type", None)
    original_cases = copy.deepcopy(cases)
    saved_report = report(cases)
    saved_report["rule_version"] = 10
    saved_report.pop("ability_display_projection", None)
    backend = FileAnalysisBackend(tmp_path)
    key = "0123456789abcdef0123456789abcdef"
    directory = tmp_path / ".analysis" / key
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "cases.jsonl": "".join(json.dumps(case) + "\n" for case in cases),
        "report.json": json.dumps(saved_report),
        "job.json": json.dumps(dict(job_id=key, run_id="r", kind="rules", status="completed")),
    }
    for name, contents in artifacts.items():
        (directory / name).write_text(contents)
    try:
        assert project_case_abilities(cases[0])["dimensions"]["ability_type"] == "H1"
        assert cases == original_cases
        backend._latest["r"] = key
        result = backend.result(key)["report"]
        assert backend.latest("r")["report"] == result
        assert result["saved_rule_version"] == 10
        assert result["denominators"]["ability_valid"] == 3
        assert (
            result["groups"]["ability_type"]
            == backend.view(key, {}, 1, 10)["summary"]["groups"]["ability_type"]
        )
        for code, qid, label in [("H1", "h", "抗幻觉"), ("I1", "i", "抗注入攻击")]:
            for unit in ["qa", "turn"]:
                filters = dict(ability_type=code, content_unit=unit)
                view = backend.view(key, filters, 1, 25)
                exported = json.loads(backend.export(key, "json", filters))
                assert view["total"] == len(exported["cases"]) == 1
                assert exported["cases"][0]["qa_id"] == qid
                assert label in backend.export(key, "csv", filters)
                html = backend.export(key, "html", filters)
                assert ReportData(html).cases()[0]["dimension_labels"]["ability_type"] == [code]
                assert label in html
        remaining = backend.view(key, dict(ability_type="not_applicable"), 1, 25)
        assert [case["qa_id"] for case in remaining["items"]] == ["clarification"]
        for name, contents in artifacts.items():
            assert (directory / name).read_text() == contents
    finally:
        backend.close()


def test_score_composition_uses_donuts_only_for_exclusive_content_and_omits_empty_groups():
    from photobench.domain.error_analysis.html_dashboard import score_composition

    cases = [case_from_item(item(qa_id=f"q{i}", score=0), i) for i in range(2)]
    for case, content, abilities in zip(cases, ["find", "describe"], [["P1", "P2"], ["P1"]], strict=True):
        case["dimension_labels"]["content_type"] = [content]
        case["dimension_labels"]["ability_type"] = abilities
    before = copy.deepcopy(cases)
    exported = score_composition(cases, {}, {"find": "查找", "describe": "描述"})
    assert cases == before
    assert 'aria-label="内容题型：' in exported
    assert 'class="composition-row"' in exported
    assert "2题 · 100.0%" in exported and "1题 · 50.0%" in exported
    assert "1分 ·" not in exported and "未评分 ·" not in exported
    cases[0]["dimension_labels"]["content_type"] = ["find", "describe"]
    assert 'aria-label="内容题型：' not in score_composition(cases, {}, {})
    assert before[0]["dimension_labels"]["content_type"] == ["find"]
    for case in cases:
        case["dimension_labels"] = {}
    assert score_composition(cases, {}, {}) == ""
