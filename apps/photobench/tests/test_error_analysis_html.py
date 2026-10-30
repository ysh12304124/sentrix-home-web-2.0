"""Offline exports preserve saved evidence and the complete filtered scope."""

from __future__ import annotations

import copy
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from test_error_analysis import await_job, item, report, write_run

from photobench.domain.error_analysis.exports import export_report
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
