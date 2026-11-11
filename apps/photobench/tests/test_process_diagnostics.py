"""Evidence availability, scoring isolation, and deterministic derived persistence."""

import copy
import json

from photobench.domain.error_analysis.process import item_process, run_process
from photobench.domain.runs.exports import export_sft, export_traces
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore


def fixture():
    refs = [
        {"media_type": "image", "media_id": "a.jpg", "asset_id": "asset_a"},
        {"media_type": "video", "media_id": "b.mp4", "asset_id": "asset_b"},
    ]
    row = {
        "qa_id": "q",
        "evaluation": {"scorer": "atm_list", "target_media_refs": refs},
        "retrieval_media_refs": refs,
    }
    turn = {
        "answer": "photo_1",
        "status": "partial",
        "termination_reason": "answered_with_gap",
        "attachments": [],
        "predicted_media": [],
        "media_mapping_complete": True,
        "retrieval_observations": {"complete": True, "asset_ids": ["asset_a", "asset_b"]},
        "requirements": [],
    }
    item = {
        "qa_id": "q",
        "answer": "photo_1",
        "runtime_turns": [turn],
        "retrieved_candidate_media": refs,
        "tool_trace": [],
    }
    return row, item


def test_known_candidates_and_empty_delivery_keep_distinct_gt_and_do_not_infer_failure():
    row, item = fixture()
    row["retrieval_media_refs"] = row["retrieval_media_refs"][:1]
    before = copy.deepcopy((row, item))
    result = item_process(row, item)
    assert (row, item) == before
    assert result["analysis_status"] == "completed" and result["original_answered_with_gap"]
    assert result["system_failure_inferred"] is False
    assert [(c["purpose"], len(c["target"])) for c in result["chain"]] == [
        ("retrieval_evidence", 1),
        ("answer_target", 2),
    ]
    assert {f["category"] for f in result["findings"]} == {"not_inspected", "delivery_gap"}
    assert next(f for f in result["findings"] if f["category"] == "delivery_gap")["status"] == "confirmed"


def test_text_answer_requires_no_attachment_and_missing_mapping_is_unknown():
    row, item = fixture()
    row["evaluation"] = {"scorer": "atm_number"}
    assert not any(f["category"] == "delivery_gap" for f in item_process(row, item)["findings"])
    del item["runtime_turns"][0]["retrieval_observations"]
    del item["tool_trace"]
    result = item_process(row, item)
    assert not any(f["category"] in {"candidate_gap", "not_inspected"} for f in result["findings"])
    assert any(f["status"] == "record_missing" for f in result["findings"])


def test_no_text_is_observation_not_semantic_zero_and_counts_unique_qa():
    row, item = fixture()
    item["tool_trace"] = [
        {
            "tool": "read_photo_text",
            "status": "denied",
            "result": {"failure_reason": "no_text_detected"},
            "step_id": "s",
            "arguments": {},
        }
    ] * 2
    state = {"run_id": "r", "qa_count": 1, "items": [item], "qa_snapshot": {"rows": [row]}}
    result = run_process(state)
    assert result == run_process(state)
    assert result["summary"]["categories"]["no_text"] == 1
    assert len([f for f in result["items"][0]["findings"] if f["category"] == "no_text"]) == 2
    assert result["summary"]["categories_overlap"] and result["summary"]["agent_calls"] == 0
    assert all(f["sources"] for f in result["items"][0]["findings"])
    item["runtime_turns"][0]["answer"] = "changed"
    assert run_process(state)["source_fingerprint"] != result["source_fingerprint"]


def test_judge_omission_is_only_review_lead():
    row, item = fixture()
    result = item_process(row, item, {"key": {"qa_id": "q", "reason": "缺少所问地点。", "score": 0}})
    f = next(f for f in result["findings"] if f["category"] == "semantic_gap")
    assert f["status"] == "needs_review" and f["sources"] == ['scoring_results["key"]']


def test_derived_storage_and_export_leave_run_snapshot_untouched(tmp_path):
    row, item = fixture()
    state = {
        "run_id": "r",
        "status": "completed",
        "qa_count": 1,
        "items": [item],
        "qa_snapshot": {"rows": [row]},
    }
    writer = FileResultWriter(tmp_path)
    writer.write_snapshot("r", state)
    path = tmp_path / "r" / "run.json"
    before = path.read_bytes()
    store = FileRunStore(tmp_path)
    result = run_process(state)
    store.save_process_diagnostics("r", result)
    assert path.read_bytes() == before
    assert json.loads((tmp_path / ".process-diagnostics/r.json").read_text()) == result
    assert export_traces(state)[0]["process_diagnostics"] == result["items"][0]
    assert export_sft(state)["items"][0]["process_diagnostics"] == result["items"][0]


def test_partial_or_debug_only_tools_cannot_prove_no_further_inspection():
    row, item = fixture()
    del item["tool_trace"]
    item["runtime_turns"][0]["trace_bundle"] = {"debug_calls": []}
    result = item_process(row, item)
    assert not any(f["category"] == "not_inspected" for f in result["findings"])
    assert any(f["category"] == "record_missing" for f in result["findings"])


def test_final_list_targets_are_not_applied_to_intermediate_turns():
    row, item = fixture()
    row["conversation"] = [{"question": "First"}, {"question": "Final"}]
    row["evaluation"]["applies_to_turn"] = 1
    item["runtime_turns"] = [copy.deepcopy(item["runtime_turns"][0]) for _ in range(2)]
    result = item_process(row, item)
    assert [c["turn_index"] for c in result["chain"] if c["purpose"] == "answer_target"] == [1]
    assert all(
        f["turn_index"] == 1
        for f in result["findings"]
        if f["category"] in {"candidate_gap", "delivery_gap", "not_inspected"}
    )


def test_scoring_projection_does_not_change_process_observations():
    from photobench.domain.evaluation.profile_view import profile_state

    row, item = fixture()
    state = {"qa_snapshot": {"rows": [row]}, "items": [item], "scoring_results": {}}
    original = run_process(state)
    for profile in ("photobench_open_v1", "atm_qs_v1"):
        assert run_process(profile_state(state, profile)) == original


def test_saved_keyframe_parent_identity_is_used_without_guessing():
    row, item = fixture()
    item["retrieved_candidate_media"][1]["source_keyframe_asset_id"] = "frame_b"
    item["runtime_turns"][0]["retrieval_observations"]["asset_ids"] = ["asset_a", "frame_b"]
    result = item_process(row, item)
    assert all(c["candidate_status"] == "confirmed" for c in result["chain"])
    assert result["chain"][0]["hits"] == ["image:a.jpg", "video:b"]
    del item["retrieved_candidate_media"][1]["source_keyframe_asset_id"]
    assert all(c["candidate_status"] == "record_missing" for c in item_process(row, item)["chain"])
