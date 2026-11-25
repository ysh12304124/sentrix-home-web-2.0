import copy

import pytest

from photobench.domain.datasets.validation import require_valid_targets, validate_targets


def ref(name, kind="image"):
    return {"media_type": kind, "media_id": name}


def test_unknown_compound_and_ambiguous_ids_block_without_mutating_input():
    rows = [
        {
            "qa_id": "q",
            "retrieval_media_refs": [ref("a.jpg b.jpg"), ref("missing.jpg"), ref("a.jpg", "video")],
        }
    ]
    manifest = {"photos": ["photos/a.jpg", "photos/b.jpg"], "videos": []}
    original = copy.deepcopy(rows)
    result = validate_targets(rows, manifest)
    assert result["error_count"] == 3 and not result["can_start"]
    assert {i["code"] for i in result["issues"]} == {"multiple_ids", "unknown_media_id"}
    assert rows == original
    with pytest.raises(ValueError, match="QA q retrieval_media_refs"):
        require_valid_targets(rows, manifest)
    manifest["photos"].append("elsewhere/a.jpg")
    assert any(
        i["code"] == "ambiguous_media_id"
        for i in validate_targets([{"qa_id": "q", "retrieval_media_refs": [ref("a.jpg")]}], manifest)[
            "issues"
        ]
    )


def test_legal_distinct_sets_and_dedup_are_warnings_not_rewritten_gt():
    rows = [
        {
            "qa_id": "q",
            "retrieval_media_refs": [ref("a.jpg"), ref("a.jpg")],
            "evaluation": {"scorer": "atm_list", "target_media_refs": [ref("b.mp4", "video")]},
        }
    ]
    original = copy.deepcopy(rows)
    result = require_valid_targets(rows, {"photos": ["a.jpg"], "videos": ["b.mp4"]})
    assert result["warning_count"] == 2 and result["source_modified"] is False
    assert rows == original
    assert result["issues"][1]["value"] == {
        "answer_target": ["video:b"],
        "retrieval_evidence": ["image:a.jpg"],
    }


@pytest.mark.parametrize("target", [None, [], [ref("", "image")], [ref("a.jpg", "audio")]])
def test_missing_list_gt_is_blocked(target):
    result = validate_targets(
        [{"qa_id": "q", "evaluation": {"scorer": "atm_list", "target_media_refs": target}}], {}
    )
    assert not result["can_start"]


def test_legacy_and_intermediate_turn_refs_are_checked_and_spaces_in_real_names_are_valid():
    rows = [
        {
            "qa_id": "q",
            "retrieval_image_ids": ["a b.jpg"],
            "conversation": [{"answer_evidence_video_ids": ["missing"]}],
        }
    ]
    result = validate_targets(rows, {"photos": ["a b.jpg"], "videos": []})
    assert result["error_count"] == 1
    assert result["issues"][0]["field"].startswith("conversation[0]")


@pytest.mark.parametrize(
    "patch",
    [
        {"evaluation": "atm_list"},
        {"conversation": "turn"},
        {"conversation": [None]},
        {"answer_claims": {}},
        {"retrieval_image_ids": "a.jpg b.jpg"},
    ],
)
def test_malformed_nested_definitions_return_actionable_errors(patch):
    report = validate_targets([{"qa_id": "q", **patch}], {"photos": ["a.jpg", "b.jpg"]})
    assert not report["can_start"]
    assert report["issues"][0]["qa_id"] == "q"
