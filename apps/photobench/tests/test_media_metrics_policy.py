"""Frozen per-question policy excludes safety cases from media metrics only."""

import copy

import pytest
from test_scoring_profiles import media, setup, state, task

from photobench.domain.evaluation.contract import (
    VERSION,
    content_fingerprint,
    evaluation_summary,
    validate_evaluation,
)
from photobench.domain.evaluation.media import media_metrics_for_sets, saved_media_metrics
from photobench.domain.evaluation.profiles import (
    ATM,
    OPEN,
    media_ranking,
    media_ranking_summary,
    scoring_targets,
)
from photobench.domain.runs.analysis import retrieval_metric_eligible, retrieval_metric_projection


@pytest.mark.parametrize("policy", [None, "include", "exclude"])
def test_policy_only_changes_media_applicability(policy):
    definition = {} if policy is None else {"media_metrics_policy": policy}
    ref = media("fork.jpg")
    row = {"evaluation": definition, "retrieval_media_refs": [ref], "answerability": "answerable"}
    record = {"predicted_media": [ref], "media_mapping_complete": True}
    original = copy.deepcopy(row)
    metrics = media_metrics_for_sets(definition, row, record, [ref], retrieval_available=True)
    for value in metrics.values():
        assert value["recall"] == (None if policy == "exclude" else 1)
        if policy == "exclude":
            assert value["status"] == "not_applicable" and value["gt"] == 0
            assert all(value[key] is None for key in ("precision", "f1", "jaccard"))
    assert retrieval_metric_eligible(row) is (policy != "exclude")
    assert row == original


def test_excluded_saved_and_ranked_media_keep_unknown_values():
    ref = media("fork.jpg", asset_id="fork")
    row = {"evaluation": {"media_metrics_policy": "exclude"}, "retrieval_media_refs": [ref]}
    item = {**row, "retrieved_candidate_media": [ref], "retrieved_candidate_asset_ids": ["fork"]}
    assert saved_media_metrics(row, item, 0)["tools"]["status"] == "not_applicable"
    assert media_ranking(item)["status"] == "not_applicable"
    summary = media_ranking_summary([item])
    assert summary["included"] == summary["unavailable"] == 0
    assert summary["recall_at_5"] is None
    projection = retrieval_metric_projection(item)
    assert projection["retrieval_metrics_applicable"] is False
    assert projection["retrieval_metrics_reason"] == "excluded_by_evaluation_policy"


@pytest.mark.parametrize(
    "policy,scorer", [("bad", "open_judge"), (False, "open_judge"), ("exclude", "media_set")]
)
def test_policy_validation_rejects_invalid_or_media_set_exclusion(policy, scorer):
    row = {"question": "问题", "answer": "答案"}
    row["evaluation"] = {
        "version": VERSION,
        "scorer": scorer,
        "answer_format": "media_list" if scorer == "media_set" else "open",
        "max_score": 1,
        "applies_to_turn": 0,
        "content_sha256": content_fingerprint(row),
        "media_metrics_policy": policy,
    }
    with pytest.raises(ValueError, match="Invalid media metrics policy"):
        validate_evaluation(row)


@pytest.mark.parametrize("profile,reply,score", [(OPEN, {"score": 2}, 2), (ATM, {"accuracy": True}, 1)])
def test_excluded_media_does_not_skip_answer_scoring(profile, reply, score):
    s = state(media_metrics_policy="exclude")
    row = s["qa_snapshot"]["rows"][0]
    row.update(retrieval_media_refs=[media("fork.jpg")], answer_evidence_media_refs=[media("fork.jpg")])
    service, batches, http, _ = setup(s, [profile], [task(), reply])
    result = service.score(batches[0], scoring_targets(s, batches[0], "all")[0])
    assert result["score"] == score and len(http.calls) == 2
    assert result["media_metrics"]["delivery"]["status"] == "not_applicable"
    assert row["answer_evidence_media_refs"] == [media("fork.jpg")]


def test_excluded_question_stays_in_answer_mean_and_leaves_media_denominator():
    items = []
    for exclude, score in [(False, 0), (True, 1)]:
        definition = {"answer_format": "open", "scorer": "open_judge", "max_score": 1}
        if exclude:
            definition["media_metrics_policy"] = "exclude"
        row = {"evaluation": definition, "retrieval_media_refs": [media("fork.jpg")]}
        metrics = media_metrics_for_sets(
            definition,
            row,
            {"predicted_media": [], "media_mapping_complete": True},
            [],
            retrieval_available=True,
        )
        items.append(
            {
                **row,
                "evaluation_result": {
                    "scorer": "open_judge",
                    "normalized_score": score,
                    "max_score": 1,
                    "media_metrics": metrics,
                },
            }
        )
    summary = evaluation_summary(items)
    assert summary["count"] == summary["scored"] == 2
    assert summary["mean"] == 0.5
    assert summary["media_metrics"]["delivery"]["recall"] == 0
    assert summary["media_metrics"]["tools"]["recall"] == 0
