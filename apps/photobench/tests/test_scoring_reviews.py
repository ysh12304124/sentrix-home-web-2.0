import copy

import pytest

from photobench.domain.evaluation.reviews import scoring_review


def fixture():
    result = {
        "profile_id": "atm_qs_v1",
        "score": 0,
        "answer_fingerprint": "sha",
        "requests": [{"call_id": "request"}],
    }
    raw = {
        "profile_id": "atm_qs_v1",
        "batch_id": "b",
        "qa_id": "q",
        "turn_index": 0,
        "answer_fingerprint": "sha",
        "verdict": "reason_error_score_unchanged",
        "note": "日期正确，但仍缺地点",
        "reviewer": "审核人",
    }
    return {"scoring_results": {"b:q:0": result}}, raw


def test_audit_preserves_zero_and_pins_actual_result_and_request():
    state, raw = fixture()
    before = copy.deepcopy(state)
    audit = scoring_review(state, raw, "2026-11-12T00:00:00")
    assert state == before and audit["score_unchanged"] is True
    assert audit["request_call_ids"] == ["request"] and audit["result_key"] == "b:q:0"
    assert audit["result_fingerprint"] and audit["review_id"]


@pytest.mark.parametrize(
    "patch",
    [
        {"batch_id": "other"},
        {"profile_id": "photobench_open_v1"},
        {"turn_index": True},
        {"answer_fingerprint": "changed"},
        {"reviewer": ""},
        {"note": ""},
        {"verdict": "score=1"},
    ],
)
def test_audit_rejects_stale_or_unidentified_review(patch):
    state, raw = fixture()
    with pytest.raises(ValueError):
        scoring_review(state, raw | patch, "now")
