"""Saved media hits and answer quality stay paired within the exported QA scope."""

from __future__ import annotations

import copy
from collections import Counter
from html.parser import HTMLParser
from typing import Any

import pytest
from test_error_analysis import item, report

from photobench.domain.error_analysis.exports import export_report
from photobench.domain.error_analysis.html_dashboard import (
    retrieval_heat,
    retrieval_quality,
    retrieval_quality_card,
    retrieval_quality_counts,
)
from photobench.domain.error_analysis.rules import case_from_item


def media_case(score: int | None, candidate: int, delivery: int) -> dict[str, Any]:
    return case_from_item(
        item(
            judge={"score": score},
            candidate_retrieval_counts={"matched": candidate, "predicted": 3, "gt": 2},
            media_retrieval_counts={"matched": delivery, "predicted": 2, "gt": 2},
        ),
        0,
    )


def samples() -> list[dict[str, Any]]:
    return [
        media_case(2, 1, 0),
        media_case(1, 2, 1),
        media_case(0, 1, 1),
        media_case(2, 0, 0),
        media_case(1, 0, 0),
        media_case(0, 0, 0),
    ]


def test_candidate_and_delivery_use_distinct_saved_sets_and_conditional_denominators():
    cases = samples()
    before = copy.deepcopy(cases)
    candidate = retrieval_quality_counts(cases, "candidate")
    delivery = retrieval_quality_counts(cases, "delivery")
    assert candidate["valid"] == delivery["valid"] == 6
    assert candidate["cells"] == {
        (True, 2): 1,
        (True, 1): 1,
        (True, 0): 1,
        (False, 2): 1,
        (False, 1): 1,
        (False, 0): 1,
    }
    assert delivery["cells"] == {(True, 1): 1, (True, 0): 1, (False, 2): 2, (False, 1): 1, (False, 0): 1}
    full = retrieval_quality_card(candidate, "candidate", partial=False)
    partial = retrieval_quality_card(candidate, "candidate", partial=True)
    assert 'aria-label="命中 · 满分：1题，占有效样本16.7%"' in full
    assert 'aria-label="命中 · 未满分：2题，占有效样本33.3%"' in full
    assert "未达标" not in full
    assert "命中组满分率</span><b>33.3%</b><small>1 / 3题" in full
    assert "命中组正确率</span><b>66.7%</b><small>2 / 3题" in partial
    delivered = retrieval_quality_card(delivery, "delivery", partial=False)
    assert "命中组满分率</span><b>0.0%</b><small>0 / 2题" in delivered
    assert "未命中组满分率</span><b>50.0%</b><small>2 / 4题" in delivered
    assert cases == before


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda c: c.update(score=None), "缺少有效主评分"),
        (lambda c: c.update(score=True), "缺少有效主评分"),
        (lambda c: c.update(evaluation={"media_metrics_policy": "exclude"}), "媒体指标不适用"),
        (lambda c: c["dimension_labels"].update(answerability=["insufficient_evidence"]), "媒体指标不适用"),
        (lambda c: c["retrieval_counts"]["candidate"].update(gt=0, matched=0), "缺少有效媒体计数或GT"),
        (lambda c: c["retrieval_counts"]["candidate"].update(matched=3), "缺少有效媒体计数或GT"),
        (lambda c: c["retrieval_counts"]["candidate"].update(matched=float("nan")), "缺少有效媒体计数或GT"),
        (lambda c: c["retrieval_counts"]["candidate"].update(available=False), "缺少有效媒体计数或GT"),
        (lambda c: c.update(matched_turns=[]), "主评分轮不在筛选范围"),
    ],
)
def test_missing_inapplicable_and_out_of_scope_are_excluded_not_counted_as_misses(mutate, reason):
    case = media_case(2, 1, 0)
    mutate(case)
    result = retrieval_quality_counts([case], "candidate")
    assert result["valid"] == 0 and result["excluded"] == {reason: 1}
    exported = retrieval_quality_card(result, "candidate", partial=False)
    assert "没有可配对" in exported and "0.0%" not in exported


def test_multiturn_pairs_only_the_primary_score_when_its_turn_is_selected():
    case = media_case(2, 1, 0)
    case["turns"] = [{"turn": 0, "score": 0}, {"turn": 1, "score": 2}]
    case["evaluation"] = {"applies_to_turn": 1}
    case["matched_turns"] = [0]
    assert retrieval_quality_counts([case], "candidate")["valid"] == 0
    case["matched_turns"] = [1]
    counts = retrieval_quality_counts([case], "candidate")
    assert counts["valid"] == 1 and counts["cells"] == {(True, 2): 1}


@pytest.mark.parametrize("profile", ["atm_qs_v1", "photobench_open_v1"])
def test_html_export_respects_the_selected_profile_and_keeps_the_scope(profile):
    cases = samples()
    exported = export_report(report(cases, scoring_profile=profile), cases, "html", {})
    assert exported.count('class="retrieval-matrix"') == (2 if profile == "atm_qs_v1" else 4)
    assert ("候选检索 × 含部分正确率" in exported) == (profile != "atm_qs_v1")
    assert "最终交付 × 满分率" in exported and "有效6题" in exported


def test_native_atm_and_projected_fractional_list_scores_only_report_full_credit():
    case = case_from_item(
        item(
            judge={
                "score": 0.5,
                "max_score": 1,
                "normalized_score": 0.5,
                "scorer": "atm_list",
                "judge_status": "completed",
            },
            evaluation={"scorer": "atm_list"},
            candidate_retrieval_counts={"matched": 1, "predicted": 1, "gt": 2},
        ),
        0,
    )
    assert case["score"] == 1
    exported = retrieval_quality([case], {})
    assert exported.count('class="retrieval-matrix"') == 1  # delivery has no saved counts
    assert "含部分正确率" not in exported
    assert 'aria-label="命中 · 满分：0题，占有效样本0.0%"' in exported
    case["evaluation_result"]["profile_id"] = "photobench_open_v1"
    assert "含部分正确率" in retrieval_quality([case], {})


def test_zero_sample_and_empty_hit_group_remain_uncovered():
    empty = retrieval_quality_card(retrieval_quality_counts([], "candidate"), "candidate", partial=False)
    assert "0.0%" not in empty and "没有可配对" in empty
    counts = retrieval_quality_counts([media_case(2, 0, 0)], "candidate")
    rendered = retrieval_quality_card(counts, "candidate", partial=False)
    assert "命中组满分率</span><b>未覆盖</b><small>0 / 0题" in rendered
    assert "未命中组满分率</span><b>100.0%</b><small>1 / 1题" in rendered


class Mosaic(HTMLParser):
    def __init__(self, markup: str):
        super().__init__()
        self.matrix: dict[str, str | None] = {}
        self.columns: list[dict[str, str | None]] = []
        self.cells: list[dict[str, str | None]] = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get("class", "").split()
        if "retrieval-matrix" in classes:
            self.matrix = values
        if "matrix-column" in classes:
            self.columns.append(values)
        if "matrix-cell" in classes:
            self.cells.append(values)


def test_mosaic_geometry_uses_group_width_and_conditional_height_with_main_diagonal():
    counts = {
        "cells": Counter({(True, 2): 177, (True, 0): 258, (False, 2): 11, (False, 0): 185}),
        "valid": 631,
        "excluded": {},
    }
    markup = retrieval_quality_card(counts, "candidate", partial=False)
    mosaic = Mosaic(markup)
    assert mosaic.matrix["style"] == "grid-template-columns:minmax(0,435fr) minmax(0,196fr)"
    assert [c["style"] for c in mosaic.columns] == [
        "grid-template-rows:minmax(0,177fr) minmax(0,258fr)",
        "grid-template-rows:minmax(0,11fr) minmax(0,185fr)",
    ]
    # Column-major DOM order: hit/full at top left, miss/fail at bottom right.
    assert [c["class"] for c in mosaic.cells] == [
        "matrix-cell hit correct",
        "matrix-cell hit wrong",
        "matrix-cell miss correct",
        "matrix-cell miss wrong",
    ]
    assert [c["data-count"] for c in mosaic.cells] == ["177", "258", "11", "185"]
    assert mosaic.cells[2]["title"] == "未命中 · 满分：11题，占有效样本1.7%"
    assert mosaic.cells[2]["aria-label"] == mosaic.cells[2]["title"]
    assert mosaic.cells[2]["tabindex"] == "0"
    assert 'class="matrix-legend"' not in markup


def test_heat_darkens_with_share_and_is_shared_across_categories_and_cards():
    colors = [retrieval_heat(i / 100) for i in range(101)]
    channels = [tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) for color in colors]
    assert colors[0] == "#eff6ff" and colors[-1] == "#164576"
    assert all(
        all(x >= y for x, y in zip(a, b, strict=True)) and sum(a) > sum(b)
        for a, b in zip(channels, channels[1:], strict=False)
    )
    counts = {"cells": Counter({(True, 2): 1, (False, 0): 1}), "valid": 2, "excluded": {}}
    candidate = Mosaic(retrieval_quality_card(counts, "candidate", partial=False))
    delivery = Mosaic(retrieval_quality_card(counts, "delivery", partial=False))
    assert candidate.cells[0]["style"] == candidate.cells[3]["style"] == delivery.cells[0]["style"]


@pytest.mark.parametrize("hit", [True, False])
def test_zero_groups_and_cells_have_zero_weight_and_no_keyboard_stop(hit):
    counts = {"cells": Counter({(hit, 2): 1}), "valid": 1, "excluded": {}}
    mosaic = Mosaic(retrieval_quality_card(counts, "candidate", partial=False))
    assert sum(int(c["data-count"]) for c in mosaic.cells) == 1
    assert sum(c.get("tabindex") == "0" for c in mosaic.cells) == 1
    for cell in mosaic.cells:
        if cell["data-count"] == "0":
            assert "tabindex" not in cell
    assert "0fr" in mosaic.matrix["style"]
