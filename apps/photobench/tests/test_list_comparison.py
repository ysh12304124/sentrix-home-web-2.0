"""Answer-text diagnostics never change the delivered list or frozen quality scores."""

import copy
import json

import pytest

from photobench.domain.evaluation.list_comparison import list_answer_comparison
from photobench.domain.evaluation.profile_view import item_profile_results, profile_state
from photobench.domain.evaluation.profiles import ATM, result_key
from photobench.domain.runs.exports import export_sft, export_traces


def sample(answer="photo_1"):
    refs = [
        {"media_type": "image", "media_id": "20220101_120000.jpg", "asset_id": "asset_a"},
        {"media_type": "image", "media_id": "20220101_120001.jpg", "asset_id": "asset_b"},
        {"media_type": "video", "media_id": "20220101_120002", "asset_id": "asset_v"},
    ]
    definition = {"scorer": "atm_list", "target_media_refs": refs[:2], "applies_to_turn": 0}
    turn = {
        "answer": answer,
        "predicted_media": [],
        "media_mapping_complete": True,
        "trace_bundle": {
            "debug_calls": [
                {
                    "messages": [
                        {
                            "role": "tool",
                            "content": json.dumps(
                                {
                                    "result_set_id": "rs1",
                                    "preview": [
                                        {"handle": "photo_1", "file_name": refs[0]["media_id"]},
                                        {"handle": "photo_2", "file_name": refs[1]["media_id"]},
                                    ],
                                }
                            ),
                        }
                    ]
                }
            ]
        },
    }
    item = {
        "qa_id": "q",
        "evaluation": definition,
        "retrieved_candidate_media": refs,
        "runtime_turns": [turn],
        "answer": answer,
    }
    target = ["image:" + r["media_id"] for r in refs[:2]]
    value = {
        "purpose": "list",
        "status": "completed",
        "score": 0,
        "max_score": 1,
        "target": target,
        "predicted": [],
        "media_metrics": {
            "tools": {
                "status": "available",
                "predicted": target,
            }
        },
    }
    return definition, item, value


def test_text_mentions_do_not_promote_tool_candidates_to_delivery():
    definition, item, value = sample("照片photo_1和photo_1，asset_a")
    before = copy.deepcopy((definition, item, value))
    result = list_answer_comparison(definition, item, 0, value)
    assert result["model_delivery_jaccard"] == 0
    assert result["tool_candidate_jaccard"] == 1
    assert result["answer_text"]["score"] == 0.5
    assert result["answer_text"]["predicted"] == ["image:20220101_120000.jpg"]
    assert (definition, item, value) == before


@pytest.mark.parametrize(
    "answer,reason",
    [
        ("photo_1 photo_99", "unresolved_ids"),
        ("photo_1 到 photo_2", "range_not_expanded"),
        ("photo_1 to photo_2", "range_not_expanded"),
        ("找到两张照片", "no_explicit_media_ids"),
        ("", "no_explicit_media_ids"),
    ],
)
def test_incomplete_text_is_unavailable_instead_of_a_false_zero(answer, reason):
    definition, item, value = sample(answer)
    result = list_answer_comparison(definition, item, 0, value)["answer_text"]
    assert result["status"] == "unavailable" and result["score"] is None
    assert result["reason"] == reason


def test_explicit_unknown_filename_counts_as_extra_and_video_keeps_type():
    definition, item, value = sample("20220101_120000.jpg 20220101_120002.mp4 20220101_120099.jpg")
    result = list_answer_comparison(definition, item, 0, value)["answer_text"]
    assert result["score"] == 0.25
    assert "video:20220101_120002" in result["predicted"]
    assert "image:20220101_120099.jpg" in result["predicted"]


def test_gt_is_never_used_as_a_mapping_dictionary():
    definition, item, value = sample("20220101_120001")
    item["retrieved_candidate_media"] = []
    item["runtime_turns"][0].pop("trace_bundle")
    result = list_answer_comparison(definition, item, 0, value)["answer_text"]
    assert result["score"] is None and result["unresolved_tokens"] == ["20220101_120001"]
    definition["scorer"] = "atm_open"
    assert list_answer_comparison(definition, item, 0, value) is None


def test_conflicting_tool_handle_is_not_rescued_by_delivery_mapping():
    definition, item, value = sample()
    turn = item["runtime_turns"][0]
    call = copy.deepcopy(turn["trace_bundle"]["debug_calls"][0])
    payload = json.loads(call["messages"][0]["content"])
    payload["result_set_id"] = "rs2"
    payload["preview"][0]["file_name"] = "20220101_120001.jpg"
    call["messages"][0]["content"] = json.dumps(payload)
    turn["trace_bundle"]["debug_calls"].append(call)
    turn["predicted_media"] = item["retrieved_candidate_media"][:1]
    turn["attachments"] = [{"handle": "photo_1", "asset_id": "asset_a"}]
    text = list_answer_comparison(definition, item, 0, value)["answer_text"]
    assert text["score"] is None and text["unresolved_tokens"] == ["photo_1"]


def test_item_api_projection_and_both_exports_share_diagnostic_without_mutation():
    definition, item, value = sample()
    batch = {
        "batch_id": "b",
        "profile_id": ATM,
        "judge": {"model": "mix"},
        "rule_version": "v1",
        "config": {"use_gt_images": False, "use_gt_metadata": False},
        "qa_definition_sha256": "frozen",
    }
    value.update(batch_id="b", profile_id=ATM, qa_id="q", turn_index=0)
    state = {
        "qa_snapshot": {"rows": [{"qa_id": "q", "evaluation": definition}]},
        "items": [item],
        "scoring_batches": [batch],
        "scoring_profiles": [ATM],
        "scoring_results": {result_key("b", "q", 0): value},
    }
    before = copy.deepcopy(state)
    expected = item_profile_results(state, "q")[ATM]["list_comparison"]
    projected = profile_state(state, ATM, "b")
    assert projected["items"][0]["evaluation_result"]["list_comparison"] == expected
    for exported in [export_traces(projected)[0], export_sft(projected)["items"][0]]:
        assert exported["profile_scores"][ATM]["list_comparison"] == expected
    assert projected["summary"]["selected_scoring"]["total_score"] == 0
    assert state == before
