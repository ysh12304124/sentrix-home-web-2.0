"""Source-level recall and citation metrics do not count duplicate chunks or infer history."""

import pytest

from photobench.domain.evaluation.text_retrieval import project_text_metrics, text_retrieval_metrics
from photobench.domain.runs.analysis import aggregate_text_retrieval, capability_summary
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
    for turns in ([], [{}]):
        result = text_retrieval_metrics(gt, turns)
        assert result["retrieval"]["recall"] is None
        assert result["recall_at_k"]["5"] is None
    assert (
        text_retrieval_metrics(
            {"retrieval_media_refs": [{"media_type": "image", "media_id": "image-1"}]}, [{}]
        )
        is None
    )


def test_legacy_text_tool_and_citation_feed_the_original_counters():
    row = {
        "retrieval_media_refs": [{"media_type": "email", "media_id": "mail-one"}],
        "media_retrieval_counts": {"gt": 1, "predicted": 0, "matched": 0},
        "runtime_turns": [
            {
                "trace_bundle": {
                    "text_retrieval": {"version": 1, "attempted": False, "complete": False},
                    "text_evidence": [{"source_type": "email", "evidence_id": "mail-one"}],
                }
            }
        ],
        "tool_trace": [
            {
                "tool": "search_memories",
                "status": "ok",
                "result": {
                    "preview": [
                        {"media_kind": "text", "file_name": "mail-one.txt"},
                        {"media_kind": "text", "file_name": "extra.txt"},
                    ]
                },
            }
        ],
    }
    projected = project_text_metrics(row)
    assert projected["media_retrieval_counts"]["matched"] == 1
    assert projected["candidate_retrieval_counts"]["predicted"] == 2
    summary = capability_summary([row])
    assert summary["retrieval_recall_macro"] == 1
    assert summary["text_retrieval_summary"]["citation"]["recall"] == 1
    assert row["media_retrieval_counts"]["matched"] == 0  # read projection never overwrites history


def test_no_search_and_no_citation_are_zero_when_turn_facts_exist():
    row = {"retrieval_media_refs": [{"media_type": "email", "media_id": "mail-one"}]}
    trace = {
        "text_retrieval": {"version": 1, "attempted": False, "complete": False},
        "text_evidence": [],
        "text_citations_recorded": False,
    }
    metrics = text_retrieval_metrics(row, [{"trace_bundle": trace}])
    assert metrics["retrieval"]["recall"] == metrics["citation"]["recall"] == 0


def test_email_summary_uses_the_same_per_question_average_as_original():
    rows = []
    for gt, prediction in ((["one"], ["one"]), (["two", "three", "four"], [])):
        trace = {
            "text_retrieval": {
                "version": 1,
                "complete": True,
                "sources": [{"source_type": "email", "evidence_id": p} for p in prediction],
            },
            "text_evidence": [{"source_type": "email", "evidence_id": p} for p in prediction],
        }
        row = {
            "retrieval_media_refs": [{"media_type": "email", "media_id": p} for p in gt],
            "runtime_turns": [{"trace_bundle": trace}],
        }
        rows.append(row)
    summary = capability_summary(rows)
    assert summary["retrieval_recall_macro"] == 0.5
    assert summary["text_retrieval_summary"]["retrieval"]["recall"] == 0.5
    assert summary["text_retrieval_summary"]["citation"]["recall"] == 0.5


def test_email_identity_is_supported_by_the_existing_scoring_index():
    from photobench.domain.evaluation.media import delivered_media_aliases
    from photobench.domain.evaluation.profiles import MediaIndex, stable_media_key

    source = {"media_type": "email", "media_id": "mail-one.txt", "asset_id": "a"}
    assert stable_media_key(source) == "email:mail-one"
    assert MediaIndex([source]).resolve({"media_type": "email", "media_id": "mail-one"}) == "email:mail-one"
    assert delivered_media_aliases(
        {"predicted_media": [source], "attachments": [{"handle": "photo_1", "asset_id": "a"}]}
    ) == [{"handle": "photo_1", "media_type": "email", "media_id": "mail-one"}]
