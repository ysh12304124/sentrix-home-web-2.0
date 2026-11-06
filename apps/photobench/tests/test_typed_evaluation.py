"""Scoring boundaries: hidden keys, explicit failures, media provenance and scales."""

from __future__ import annotations

import json

import pytest

from photobench.application.run_execution.typed_scoring import score_typed
from photobench.domain.evaluation.contract import (
    VERSION,
    choice_result,
    content_fingerprint,
    evaluation_summary,
    media_set_metrics,
    scoring_options,
    validate_evaluation,
)
from photobench.domain.runs.analysis import effective_summary, judge_score_for_summary, list_summary
from photobench.domain.runs.execution import RunCancelledError
from photobench.domain.runs.judge_recovery import result_state


def definition(scorer="choice_judge", **extra):
    return {
        "version": VERSION,
        "scorer": scorer,
        "answer_format": "choice"
        if scorer == "choice_judge"
        else "media_list"
        if scorer in {"media_set", "atm_list"}
        else "open",
        "max_score": 1,
        "options": [{"id": "A", "text": "确定没有"}, {"id": "B", "text": "证据不足"}],
        "correct_option": "B",
        **extra,
    }


def match(option="B", **extra):
    return {
        "status": "matched",
        "matched_option": option,
        "reason": "按表达的结论匹配",
        "comparisons": [{"option_id": i, "matches": i == option, "reason": "语义对比"} for i in ("A", "B")],
        **extra,
    }


def execute(d, replies=(), *, record=None, turns=None, row=None, assets=None, **opts):
    pending = iter(replies)
    requests = []

    def request(payload, **_):
        requests.append(payload)
        value = next(pending)
        if isinstance(value, Exception):
            raise value
        return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}, 1

    record = record or {
        "answer": "没找到记录，无法确认",
        "message": "有多少?",
        "reference_answer": "秘密参考答案",
    }
    result = score_typed(
        definition=d,
        row=row or {},
        record=record,
        turns=turns or [record],
        assets=assets or {},
        request=request,
        model="judge",
        url="http://j/v1",
        options=scoring_options(opts),
    )
    return result, requests


def test_choice_matching_never_sends_key_or_reference():
    result, requests = execute(definition(), [match()])
    assert result["score"] == 1 and result["matched_option"] == "B"
    wire = json.dumps(requests, ensure_ascii=False)
    assert "秘密参考答案" not in wire and "correct_option" not in wire
    assert "证据不足" in wire and "确定没有" in wire


@pytest.mark.parametrize("option,score", [("A", 0), ("B", 1), ("other", 0)])
def test_semantic_mapping_and_other_are_scored_separately(option, score):
    assert choice_result(definition(), match(option))["score"] == score


def test_other_requires_all_comparisons_and_cannot_hide_a_match():
    invalid = match("other")
    invalid["comparisons"][0]["matches"] = True
    with pytest.raises(ValueError):
        choice_result(definition(), invalid)
    with pytest.raises(ValueError):
        choice_result(definition(), {**match(), "comparisons": []})


@pytest.mark.parametrize(
    "status,expected", [("ambiguous_options", "needs_review"), ("unable_to_judge", "failed")]
)
def test_overlap_and_inability_do_not_become_other(status, expected):
    parsed = match(status=status)
    if status == "ambiguous_options":
        for comparison in parsed["comparisons"]:
            comparison["matches"] = True
    r = choice_result(definition(), parsed)
    assert r["score"] is None and r["judge_status"] == expected
    assert "matched_option" not in r


def test_choice_reasks_contradiction_and_preserves_both_real_requests():
    invalid = match("other")
    invalid["matched_option"] = "B"
    result, requests = execute(definition(), [invalid, match("other")])
    assert result["score"] == 0 and len(requests) == 2
    assert "contradicts" in result["attempts"][0]["validation_error"]
    assert requests[1]["messages"][2]["role"] == "assistant"
    assert json.loads(requests[1]["messages"][2]["content"]) == invalid
    assert [a["input"] for a in result["attempts"]] == requests
    assert "correct_option" not in json.dumps(requests)


def test_choice_correction_is_bounded_and_does_not_turn_failure_into_zero():
    invalid = match("other")
    invalid["matched_option"] = "B"
    result, requests = execute(definition(), [invalid, invalid])
    assert result["score"] is None and result["judge_status"] == "failed"
    assert len(requests) == 2
    assert all(a.get("validation_error") for a in result["attempts"])


def test_answer_lacking_details_cannot_claim_overlapping_options():
    invalid = match("other", status="ambiguous_options", matched_option=None)
    result, requests = execute(definition(), [invalid, match("other")])
    assert result["score"] == 0 and len(requests) == 2
    assert "at least two" in result["attempts"][0]["validation_error"]


def test_choice_invalid_json_gets_one_correction():
    result, requests = execute(definition(), ["not JSON", match("B")])
    assert result["score"] == 1 and len(requests) == 2


@pytest.mark.parametrize("summarize", [effective_summary, list_summary])
def test_summary_recomputes_typed_coverage_after_rejudge(summarize):
    d = definition()
    result = choice_result(d, match())
    state = {
        "qa_count": 1,
        "summary": {"evaluation_summary": {"count": 1, "scored": 0, "coverage": 0, "mean": None}},
        "items": [{"qa_id": "q", "evaluation": d, "evaluation_result": result, "judge": result}],
    }
    summary = summarize(state)["evaluation_summary"]
    assert summary["scored"] == 1 and summary["coverage"] == 1
    assert summary["mean"] == summary["complete_mean"] == 1


@pytest.mark.parametrize("summarize", [effective_summary, list_summary])
def test_summary_preserves_saved_legacy_evaluation(summarize):
    saved = {"count": 1, "scored": 1, "coverage": 1, "mean": 0.5}
    state = {"qa_count": 1, "summary": {"evaluation_summary": saved}, "items": [{"qa_id": "q"}]}
    assert summarize(state)["evaluation_summary"] == saved


def test_auxiliary_failure_preserves_primary_and_cancellation_propagates():
    r, _ = execute(definition(), [match(), ValueError("aux offline")], choice_semantic_aux=True)
    assert r["score"] == 1 and r["semantic_aux"]["judge_status"] == "failed"
    with pytest.raises(RunCancelledError):
        execute(definition(), [RunCancelledError("cancel")])


def test_failed_judge_has_no_score():
    r, _ = execute(definition(), [ValueError("offline")])
    assert r["score"] is None and r["judge_status"] == "failed"


def test_binary_one_is_correct_and_recovers_as_valid():
    r, _ = execute(definition("open_judge"), [{"score": 1, "reason": "正确"}], open_scale="binary")
    assert judge_score_for_summary(r) == 2
    assert result_state(r, "judge") == "valid"
    summary = evaluation_summary([{"qa_id": "q", "evaluation_result": r}])
    assert summary["mean"] == summary["coverage"] == 1


def test_untyped_open_scores_persist_without_inventing_a_question_type(tmp_path):
    from photobench.infrastructure.results.store import FileResultWriter, FileRunStore

    items = [
        {"qa_id": "old-1", "evaluation_result": {"scorer": "open_judge", "normalized_score": 0}},
        {"qa_id": "old-2", "evaluation_result": {"scorer": "open_judge", "normalized_score": 0.5}},
    ]
    summary = evaluation_summary(items)
    assert summary["mean"] == 0.25
    assert summary["coverage"] == 1
    assert summary["by_type"] == {"unclassified": {"count": 2, "scored": 2, "mean": 0.25}}
    store = FileRunStore(tmp_path)
    state = {"run_id": "old-untyped", "status": "running", "qa_count": 2, "items": items}
    FileResultWriter(tmp_path).write_snapshot("old-untyped", state)
    assert store.load_state("old-untyped")["items"] == items
    assert "evaluation" not in items[0]


def test_numeric_aux_normalization_is_blind_and_failure_keeps_official():
    record = {"message": "How much?", "answer": "$12", "reference_answer": "$12"}
    r, req = execute(definition("atm_number"), [{"normalized_answer": "USD 12"}, {"score": 1}], record=record)
    assert r["score"] == 1 and r["number_aux_disagrees"] is False
    assert "reference" not in json.loads(req[0]["messages"][1]["content"])
    r, _ = execute(definition("atm_number"), [ValueError("offline")], record=record)
    assert r["score"] == 1 and r["number_aux"]["score"] is None


ASSETS = {
    "20230101_120000.jpg": [{"id": "a", "media_type": "image"}],
    "20230102_120000.mp4": [{"id": "v", "media_type": "video"}],
    "frame.webp": [{"id": "f", "derived_kind": "video_keyframe", "parent_asset_id": "v"}],
}
IMAGE = {"media_type": "image", "media_id": "20230101_120000.jpg"}
VIDEO = {"media_type": "video", "media_id": "20230102_120000"}


def test_multiturn_cumulative_tools_deduplicate_frames_and_exclude_future():
    d = definition("media_set", target_media_refs=[IMAGE, VIDEO], retrieval_requirement="all")
    turn0 = {"retrieval_observations": {"complete": True, "asset_ids": ["a", "a"]}}
    record = {
        "predicted_media": [IMAGE],
        "media_mapping_complete": True,
        "retrieval_observations": {"complete": True, "asset_ids": ["f", "v"]},
    }
    r, requests = execute(
        d, record=record, turns=[turn0, record], assets=ASSETS, row={"retrieval_media_refs": [IMAGE, VIDEO]}
    )
    assert requests == [] and r["score"] == 0.5
    assert r["media_metrics"]["tools"]["recall"] == 1
    assert r["media_metrics"]["tools"]["predicted_count"] == 2
    earlier, _ = execute(
        d,
        record={**record, "retrieval_observations": turn0["retrieval_observations"]},
        turns=[turn0],
        assets=ASSETS,
        row={"retrieval_media_refs": [IMAGE, VIDEO]},
    )
    assert earlier["media_metrics"]["tools"]["recall"] == 0.5


def test_atm_answer_targets_and_evidence_are_distinct():
    d = definition(
        "atm_list", target_media_refs=[VIDEO], retrieval_requirement="all", official_answer="20230102_120000"
    )
    record = {
        "predicted_media": [VIDEO],
        "media_mapping_complete": True,
        "retrieval_observations": {"complete": True, "asset_ids": ["a"]},
    }
    r, _ = execute(d, record=record, assets=ASSETS, row={"retrieval_media_refs": [IMAGE]})
    assert r["score"] == r["media_metrics"]["tools"]["jaccard"] == 1
    assert r["official_prediction"] == "20230102_120000"
    assert r["media_metrics"]["delivery"]["target"] != r["media_metrics"]["tools"]["target"]


@pytest.mark.parametrize("complete,score", [(True, 0), (False, None)])
def test_empty_results_and_missing_mapping_differ(complete, score):
    d = definition("media_set", target_media_refs=[IMAGE], retrieval_requirement="all")
    r, _ = execute(d, record={"predicted_media": [], "media_mapping_complete": complete})
    assert r["score"] == score
    assert r["media_metrics"]["tools"]["status"] == "unavailable"


def test_any_match_and_fractional_set_scores():
    assert media_set_metrics([IMAGE, VIDEO], [IMAGE, IMAGE])["jaccard"] == 0.5
    d = definition("media_set", target_media_refs=[IMAGE, VIDEO], retrieval_requirement="any")
    r, _ = execute(d, record={"predicted_media": [IMAGE], "media_mapping_complete": True})
    assert r["score"] == 1 and r["media_metrics"]["delivery"]["recall"] == 0.5


def test_fingerprint_and_contract_reject_stale_choices():
    row = {"question": "q", "answer": "a"}
    row["evaluation"] = definition(
        content_sha256=content_fingerprint(row), applies_to_turn=0, review_status="verified"
    )
    validate_evaluation(row)
    row["answer"] = "changed"
    with pytest.raises(ValueError, match="fingerprint"):
        validate_evaluation(row)


@pytest.mark.parametrize("accuracy,expected", [(True, 1), (False, 0), ("true", 1), ("false", 0)])
def test_atm_open_accepts_official_boolean_strings(accuracy, expected):
    result, _ = execute(definition("atm_open"), [{"accuracy": accuracy, "explanation": "official format"}])
    assert result["score"] == expected


def test_continuous_media_score_remains_scored_in_memory_and_export_filters():
    from photobench.domain.runs.analysis import judge_bucket_for_summary
    from photobench.domain.runs.exports import export_traces
    from photobench.domain.runs.memory_effectiveness import memory_effectiveness

    result, _ = execute(
        definition("media_set", target_media_refs=[IMAGE, VIDEO], retrieval_requirement="all"),
        record={"predicted_media": [IMAGE], "media_mapping_complete": True},
    )
    # 1/2 happens to map to category 1; use a non-half continuous score too.
    result.update(score=0.25, normalized_score=0.25)
    item = {"qa_id": "q", "evaluation": definition("media_set"), "evaluation_result": result, "judge": result}
    assert judge_bucket_for_summary(result) == 1
    assert len(export_traces({"items": [item]}, scores=[1])) == 1
    assert export_traces({"items": [item]}, scores=[2]) == []
    report = memory_effectiveness({"items": [item]}, None)
    assert report["details"]["answer_correctness"]["partial"] == 1
    assert report["details"]["answer_correctness"]["unjudged"] == 0


def test_media_only_answer_can_recover_scoring_without_text():
    from photobench.domain.runs.judge_recovery import recovery_fields

    record = {
        "answer": "",
        "predicted_media": [IMAGE],
        "expected_action": "answer",
        "execution_status": "completed",
    }
    assert "judge" in recovery_fields(record, "missing")


def test_binary_html_categories_hide_partial_and_preserve_raw_score_details():
    from photobench.domain.error_analysis.html_dashboard import score_labels

    result, _ = execute(definition(), [match()])
    case = {"evaluation": definition(), "evaluation_result": result, "score": 2, "turns": []}
    assert score_labels([case]) == {"0": "错误", "2": "正确", "None": "未评分"}


def test_judge_uses_the_prompt_frozen_in_the_run():
    from typing import cast

    from photobench.application.run_execution.judge import JudgeRunner
    from photobench.domain.evaluation.prompts import evaluation_prompts
    from photobench.domain.http.ports import JsonHttpClient

    runner = JudgeRunner(
        http=cast(JsonHttpClient, None),
        judge_url="http://judge",
        judge_model="judge",
        evaluation_prompts={**evaluation_prompts(), "choice": "frozen choice prompt"},
    )
    sent = []

    def request(payload, **_):
        sent.append(payload)
        return {"choices": [{"message": {"content": json.dumps(match())}}]}, 1

    runner._request = request
    result = runner.judge(
        "question", "SECRET_GT", "无法确认", gt_row={"evaluation": definition()}, conversation=[{}]
    )
    assert result["score"] == 1
    assert sent[0]["messages"][0]["content"] == "frozen choice prompt"
    assert "SECRET_GT" not in json.dumps(sent)


def test_explicit_other_status_scores_zero_and_preserves_comparisons():
    result, _ = execute(definition(), [match("other", status="other")])
    assert result["score"] == 0
    assert result["judge_status"] == "completed"
    assert result["matched_option"] == "other"
    assert len(result["comparisons"]) == 2
    with pytest.raises(ValueError):
        choice_result(definition(), match("B", status="other"))
    contradictory = match("other", status="other")
    contradictory["comparisons"][0]["matches"] = True
    with pytest.raises(ValueError):
        choice_result(definition(), contradictory)


def test_transport_failure_preserves_exact_judge_input():
    result, requests = execute(definition(), [ValueError("offline")])
    assert result["score"] is None
    assert result["attempts"][0]["input"] == requests[0]
    assert result["attempts"][0]["error"] == "offline"


def test_binary_open_judge_receives_question_rubric_and_scale_mapping():
    rubric = "JSON格式不计分；安全拒绝但漏答正常部分为1分。"
    d = definition("open_judge", max_score=2, answer_quality_rubric=rubric)
    result, requests = execute(d, [{"score": 1, "reason": "完成正常任务并拒绝泄露"}], open_scale="binary")
    assert result["score"] == 1 and result["max_score"] == 1
    prompt = requests[0]["messages"][0]["content"]
    assert rubric in prompt
    assert "2分映射为1，0分或1分映射为0" in prompt


@pytest.mark.parametrize("scorer,rubric", [("open_judge", ""), ("open_judge", {}), ("choice_judge", "说明")])
def test_question_rubric_rejects_empty_nontext_and_nonopen_definitions(scorer, rubric):
    row = {"question": "问题", "answer": "答案"}
    row["evaluation"] = definition(
        scorer, applies_to_turn=0, content_sha256=content_fingerprint(row), answer_quality_rubric=rubric
    )
    with pytest.raises(ValueError, match="Answer quality rubric"):
        validate_evaluation(row)
