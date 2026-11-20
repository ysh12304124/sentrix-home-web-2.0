"""Source-level recall and citation metrics do not count duplicate chunks or infer history."""

import pytest

from photobench.domain.evaluation.text_retrieval import aggregate_text_retrieval, text_retrieval_metrics
from photobench.domain.runs.execution import retrieved_candidate_asset_ids, selected_asset_ids


def test_two_retrieved_one_cited_and_duplicate_chunks_count_once():
    gt = [{"media_type": "email", "media_id": x} for x in ("mail-one", "mail-two")]
    sources = [
        {"asset_id": x, "source_type": "email", "evidence_id": x}
        for x in ("mail-one", "mail-one", "mail-two")
    ]
    trace = {
        "text_citations_recorded": True,
        "text_retrieval": {"version": 1, "complete": True, "sources": sources, "asset_ids": ["a", "b"]},
        "text_evidence": [sources[0]],
    }
    metrics = text_retrieval_metrics({"retrieval_media_refs": gt}, [{"trace_bundle": trace}])
    assert metrics["retrieval"]["recall"] == 1
    assert metrics["retrieval"]["precision"] == 1
    assert metrics["citation"]["recall"] == 0.5
    assert metrics["citation"]["f1"] == pytest.approx(2 / 3)
    assert metrics["recall_at_k"] == {"1": 0.5, "5": 1, "10": 1}
    assert selected_asset_ids({"trace": trace}) == ["mail-one"]
    assert retrieved_candidate_asset_ids({"trace": trace}) == ["a", "b"]
    summary = aggregate_text_retrieval(
        [{"retrieval_media_refs": gt, "text_retrieval_metrics": metrics}, {"retrieval_media_refs": gt}]
    )["text_retrieval_summary"]
    assert summary["question_count"] == 2 and summary["recorded_count"] == 1
    assert summary["citation"]["sample_count"] == 1 and summary["citation"]["recall"] == 0.5


def test_old_or_failed_traces_remain_unavailable_and_media_only_not_changed():
    gt = {"retrieval_media_refs": [{"media_type": "email", "media_id": "mail-one"}]}
    for turns in ([], [{}], [{"trace": {"text_retrieval": {"version": 1, "complete": False}}}]):
        result = text_retrieval_metrics(gt, turns)
        assert result["retrieval"]["recall"] is None
        assert result["recall_at_k"]["5"] is None
    assert (
        text_retrieval_metrics(
            {"retrieval_media_refs": [{"media_type": "image", "media_id": "image-1"}]}, [{}]
        )
        is None
    )
