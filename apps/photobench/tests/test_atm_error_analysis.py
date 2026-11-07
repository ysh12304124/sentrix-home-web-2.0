"""ATM report classifications and diagnostic evidence use the selected saved scores."""

from __future__ import annotations

import copy
import json

import pytest
from test_error_analysis import item, report
from test_error_analysis_html import ReportData
from test_scoring_profiles import setup, state, task

from photobench.domain.error_analysis.diagnosis import evidence_package
from photobench.domain.error_analysis.exports import export_report
from photobench.domain.error_analysis.query import filtered_view, select_case
from photobench.domain.error_analysis.rules import case_from_item
from photobench.domain.error_analysis.scoring import scoring_evidence, scoring_question_type
from photobench.domain.evaluation.profile_view import profile_state
from photobench.domain.evaluation.profiles import ATM, OPEN, scoring_targets


def atm_case(kind, scorer, **details):
    definition = {"scorer": scorer, "source_qtype": kind, "answer_format": "open", "max_score": 1}
    result = {
        "scorer": scorer,
        "score": 0,
        "normalized_score": 0,
        "max_score": 1,
        "judge_status": "completed",
        "reason": "saved reason",
        **details,
    }
    return case_from_item(item(qa_id=kind, evaluation=definition, evaluation_result=result, judge=result), 0)


@pytest.mark.parametrize(
    "kind,scorer",
    [
        ("number", "atm_number"),
        ("list_recall", "atm_list"),
        ("open_end", "atm_open"),
    ],
)
def test_native_types_group_filter_export_without_changing_photo_taxonomy(kind, scorer):
    c = atm_case(kind, scorer)
    before = copy.deepcopy(c)
    r = report([c], scoring_profile=ATM)
    assert c["dimensions"]["scoring_question_type"] == kind
    assert c["dimensions"]["content_type"] == "unknown"
    assert r["groups"]["scoring_question_type"][0]["value"] == kind
    assert r["turn_groups"]["scoring_question_type"][0]["value"] == kind
    filters = {"scoring_question_type": kind}
    view = filtered_view([c], r, filters, 1, 25)
    assert view["total"] == view["summary"]["item_count"] == 1
    assert select_case(c, {"scoring_question_type": "wrong"}) is None
    exported = export_report(view["summary"], view["items"], "html", filters)
    assert "ATM原生题型 · 样本量与质量" in exported
    assert 'data-dimension="scoring_question_type"' in exported
    assert ReportData(exported).cases()[0]["scoring_evidence"]["result"]["reason"] == "saved reason"
    assert c == before


def test_list_differences_are_saved_set_facts_not_rejudging():
    c = atm_case(
        "list_recall",
        "atm_list",
        target=["image:a", "video:b"],
        predicted=["image:a", "image:c"],
        formula="|P ∩ GT| / |P ∪ GT|",
    )
    package = evidence_package(c, {})
    saved = json.loads(next(r["excerpt"] for r in package["records"] if r["id"] == "i0:scoring"))
    assert saved["list_comparison"]["missing"] == ["video:b"]
    assert saved["list_comparison"]["extra"] == ["image:c"]
    assert saved["result"]["score"] == 0  # Never recompute even when set facts suggest another score.
    assert saved["result"]["formula"] == "|P ∩ GT| / |P ∪ GT|"


def test_native_list_delivery_metrics_and_missing_data():
    c = atm_case(
        "list_recall",
        "atm_list",
        media_metrics={"delivery": {"target": ["image:a"], "predicted": [], "missing": ["image:a"]}},
    )
    assert c["scoring_evidence"]["list_comparison"]["missing"] == ["image:a"]
    assert c["scoring_evidence"]["list_comparison"]["source"] == "result.media_metrics.delivery"
    missing = atm_case("list_recall", "atm_list")
    assert "list_comparison" not in missing["scoring_evidence"]
    assert any("缺少" in x for x in missing["scoring_evidence"]["limitations"])


def test_number_aux_disagreement_and_reason_survive_large_trace():
    c = atm_case(
        "number",
        "atm_number",
        number_aux={"score": 1, "reason": "equivalent units"},
        number_aux_disagrees=True,
    )
    package = evidence_package(c, {"tool_trace": [{"result": "x" * 100000}]})
    row = next(r for r in package["records"] if r["id"] == "i0:scoring")
    assert not row["truncated"]
    saved = json.loads(row["excerpt"])
    assert saved["result"]["number_aux_disagrees"] is True
    assert saved["result"]["number_aux"]["reason"] == "equivalent units"
    assert any("容差" in x for x in saved["limitations"])


def test_selected_profile_keeps_frozen_rule_and_not_other_batch_evidence():
    s = state("atm_number", source_qtype="number")
    svc, batches, _, _ = setup(
        s, [OPEN, ATM], [task(), {"score": 2}, {"score": 0, "reason": "unit mismatch"}]
    )
    before = copy.deepcopy(s["items"])
    for batch in batches:
        svc.score(batch, scoring_targets(s, batch, "all")[0])
    projected = profile_state(s, ATM, batches[1]["batch_id"])
    c = case_from_item(projected["items"][0], 0)
    e = c["scoring_evidence"]
    assert e["result"]["profile_id"] == ATM
    assert e["result"]["batch_id"] == batches[1]["batch_id"]
    assert e["result"]["score"] == 0
    assert e["result"]["reason"] == "unit mismatch"
    assert e["scoring_rule"]["frozen_prompt_template"] == batches[1]["config"]["prompts"]["number"]
    assert e["scoring_rule"]["rule_version"] == batches[1]["rule_version"]
    assert s["items"] == before
    open_case = case_from_item(profile_state(s, OPEN)["items"][0], 0)
    assert open_case["scoring_evidence"]["result"]["score"] == 2
    assert open_case["scoring_evidence"]["result"]["profile_id"] == OPEN
    assert open_case["dimensions"]["scoring_question_type"] == "number"


def test_old_report_projects_new_dimension_without_rewriting_artifact():
    c = atm_case("open_end", "atm_open", reason="</script><script>unsafe()</script>")
    c["dimensions"].pop("scoring_question_type")
    c["dimension_labels"].pop("scoring_question_type")
    c.pop("scoring_evidence")
    for turn in c["turns"]:
        turn.pop("scoring_question_type")
        turn.pop("scoring_evidence")
    before = copy.deepcopy(c)
    r = report([c], scoring_profile=ATM)
    view = filtered_view([c], r, {"scoring_question_type": "open_end"}, 1, 25)
    exported = export_report(view["summary"], view["items"], "html", {})
    assert "<script>unsafe()" not in exported
    assert (
        ReportData(exported).cases()[0]["scoring_evidence"]["result"]["reason"]
        == "</script><script>unsafe()</script>"
    )
    assert c == before


def test_profile_purpose_inference_requires_explicit_atm_profile():
    assert scoring_question_type({"evaluation_result": {"scorer": "number", "profile_id": ATM}}) == "number"
    assert scoring_question_type({"evaluation_result": {"scorer": "number", "profile_id": OPEN}}) == "unknown"
    assert scoring_evidence({})["limitations"]


def test_multi_turn_type_filter_only_matches_scored_turn():
    result = {"scorer": "atm_number", "score": 1, "max_score": 1, "normalized_score": 1}
    c = case_from_item(
        item(
            evaluation={
                "scorer": "atm_number",
                "source_qtype": "number",
                "answer_format": "open",
                "applies_to_turn": 1,
            },
            evaluation_result=result,
            judge=result,
            runtime_turns=[
                {"message": "clarify", "judge": {"score": 2}},
                {"message": "number", "judge": result},
            ],
        ),
        0,
    )
    selected = select_case(c, {"scoring_question_type": "number", "content_unit": "turn"})
    assert selected["matched_turns"] == [1]


def test_api_accepts_type_filter_for_view_export_and_ai_scope():
    from photobench.api.routes.error_analysis import AnalysisQuery, AnalysisRequest

    assert AnalysisQuery(scoring_question_type="number").conditions()["scoring_question_type"] == "number"
    assert AnalysisRequest(
        kind="ai",
        parent_job="parent",
        model_role="judge",
        scope="filter",
        filters={"scoring_question_type": "number"},
    ).filters == {"scoring_question_type": "number"}
