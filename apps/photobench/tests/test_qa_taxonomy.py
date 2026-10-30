"""Version boundaries, orthogonal annotations and immutable QA inputs."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fakes_execution import FakeAlbums
from test_error_analysis import item, report
from test_run_execution import ALBUM, _execution, _manifest, _request
from test_run_read import _read_runs
from test_suites import FakeRunStore, _cancelled_state, _suites

from photobench.application.run_execution.state import RunState
from photobench.domain.datasets.qa_taxonomy import qa_fingerprint, validate_qa_annotations
from photobench.domain.error_analysis.exports import export_report
from photobench.domain.error_analysis.rules import case_from_item, matches
from photobench.domain.errors import InvalidRequestError
from photobench.infrastructure.datasets.qa_metadata import JsonQaMetadataSource
from photobench.infrastructure.results.store import FileRunStore, write_committed_snapshot


def annotated(**over):
    return dict(
        task_type="T2_fact_qa",
        question_type="fact_place",
        expected_action="answer",
        answerability="answerable",
        injection=False,
        difficulty="easy",
        **over,
    )


def test_parent_subtype_and_final_turn_must_agree():
    valid = annotated()
    validate_qa_annotations(valid)
    with pytest.raises(ValueError, match="task/subtype"):
        validate_qa_annotations({**valid, "task_type": "T1_retrieve_media"})
    with pytest.raises(ValueError, match="final turn"):
        validate_qa_annotations({**valid, "conversation": [{**valid, "question_type": "fact_time"}]})
    for special in ("not_applicable", "pending_review"):
        validate_qa_annotations({**valid, "task_type": special, "question_type": special})


def test_execution_uses_frozen_rows_even_if_source_changes(monkeypatch):
    rows = [{"qa_id": "same", "question": "original", "answer": "old reference"}]
    albums = FakeAlbums(manifest=_manifest(), qa_rows=rows)
    execution, *_ = _execution(albums=albums)
    state = RunState(execution._initial_state("r", _request(), _manifest(), "s", ALBUM))
    albums.rows[0]["question"] = "new question with same ID"
    seen = []

    def evaluate(row, *_):
        seen.append(copy.deepcopy(row))
        return {"qa_id": row["qa_id"], "answer": "result", "execution_status": "completed"}

    monkeypatch.setattr(execution, "_evaluate_one", evaluate)
    execution._phase_qa_eval(state, _request())
    assert seen == [{"qa_id": "same", "question": "original", "answer": "old reference"}]
    snapshot = state.get("qa_snapshot")
    assert snapshot["definition_sha256"] == qa_fingerprint(snapshot["rows"])
    request = replace(_request(), frozen_qa_snapshot=snapshot)
    albums.calls.clear()
    resumed = execution._initial_state("next", request, _manifest(), "s", ALBUM)
    assert resumed["qa_snapshot"] == snapshot
    assert not any(name == "qa_rows" for name, _ in albums.calls)
    bad = {**snapshot, "definition_sha256": "changed"}
    with pytest.raises(ValueError, match="fingerprint"):
        execution._initial_state("next", replace(request, frozen_qa_snapshot=bad), _manifest(), "s", ALBUM)


def test_overview_omits_bank_but_durable_snapshot_retains_it(tmp_path: Path):
    rows = [{"qa_id": "q", "question": "private original", "answer": "reference"}]
    state = {
        "run_id": "r",
        "status": "cancelled",
        "items": [],
        "qa_snapshot": {
            "album_id": "a",
            "qa_set": "v2",
            "rows": rows,
            "definition_sha256": qa_fingerprint(rows),
        },
    }
    write_committed_snapshot(tmp_path / "r", state)
    assert FileRunStore(tmp_path).load_state("r")["qa_snapshot"]["rows"] == rows
    overview = _read_runs(tmp_path).get_run("r")
    assert "rows" not in overview["qa_snapshot"]
    assert state["qa_snapshot"]["rows"] == rows


def test_unversioned_legacy_resume_does_not_guess_current_bank(tmp_path: Path):
    state = _cancelled_state()
    state.pop("qa_snapshot")
    with pytest.raises(InvalidRequestError, match="无法确认题库版本"):
        _suites(tmp_path, store=FakeRunStore({"old": state})).resume_run("old")


def test_same_id_in_different_sets_keeps_separate_metadata(tmp_path: Path):
    album = tmp_path / "album"
    album.mkdir()
    (album / "manifest.json").write_text(
        json.dumps({"album_id": "album", "qa_sets": {"one": "one.jsonl", "two": "two.jsonl"}})
    )
    for name, task in (("one", "T1_retrieve_media"), ("two", "T2_fact_qa")):
        (album / f"{name}.jsonl").write_text(json.dumps({"qa_id": "same", "task_type": task}))
    metadata = JsonQaMetadataSource(tmp_path, tmp_path / "no-policy").metadata_by_qa_id()
    assert len(metadata) == 2
    assert {v["task_type"] for v in metadata.values()} == {"T1_retrieve_media", "T2_fact_qa"}
    assert "same" not in metadata
    reader = _read_runs(tmp_path / "results")
    reader._qa_source = JsonQaMetadataSource(tmp_path, tmp_path / "no-policy")
    assert reader._qa_index({"album_id": "album", "qa_set": "one"}) == {}
    reader._qa_metadata = metadata
    one = next(k for k in metadata if "\0one\0" in k)
    digest = one.split("\0")[2]
    assert (
        reader._qa_index({"album_id": "album", "qa_set": "one", "input_integrity": {"qa_sha256": digest}})[
            "same"
        ]["task_type"]
        == "T1_retrieve_media"
    )


def test_turn_task_switch_and_score_filter_do_not_cross_turns():
    turns = [
        {**annotated(), "judge": {"score": 2}, "task_judge": {"actual_action": "answer", "correct": True}},
        {**annotated(), "question_type": "fact_time", "judge": {"score": 0}},
    ]
    case = case_from_item(item(**annotated(), runtime_turns=turns), 0)
    r = report([case])
    assert len(r["turn_groups"]["content_subtype"]) == 2
    assert matches(case, {"content_unit": "turn", "content_subtype": "fact_place", "score": "2"})
    assert not matches(case, {"content_unit": "turn", "content_subtype": "fact_place", "score": "0"})
    assert not matches(case, {"content_subtype": "fact_time"})


def test_mixed_behavior_requires_new_judge_rule_and_is_independent_of_quality():
    data = item(
        **{**annotated(), "expected_action": "answer_and_refuse", "injection": True},
        judge={"score": 0},
        task_judge={"actual_action": "answer_and_refuse", "correct": True},
    )
    old = case_from_item(data, 0)
    assert old["turns"][0]["behavior_correct"] is None
    data["task_judge"]["behavior_rule_version"] = 2
    case = case_from_item(data, 0)
    assert case["score"] == 0 and case["turns"][0]["behavior_correct"] is True
    assert report([case])["behavior_matrix"] == [
        {"expected": "answer_and_refuse", "actual": "answer_and_refuse", "count": 1}
    ]


def test_pure_safety_and_pending_are_not_content_denominators():
    cases = [case_from_item(item(**annotated()), 0)]
    for index, special in enumerate(("not_applicable", "pending_review"), 1):
        cases.append(case_from_item(item(task_type=special, question_type=special), index))
    r = report(cases)
    assert r["denominators"]["content_valid"] == 1
    assert r["denominators"]["content_not_applicable"] == r["denominators"]["content_pending"] == 1
    assert "retrieval_gt" not in cases[1]["missing"]
    html = export_report(r, cases, "html", {})
    assert "事实查询" in html and "细分题型" in html
    assert "不适用、待核或未记录" in html


def test_annotation_change_does_not_claim_evidence_changed():
    original = item(
        **annotated(),
        retrieval_media_refs=[{"media_type": "image", "media_id": "a"}],
        retrieval_image_ids=["a"],
    )
    revised = {**original, "question_type": "fact_time"}
    revised.pop("retrieval_image_ids")
    a, b = case_from_item(original, 0), case_from_item(revised, 0)
    assert a["gt_fingerprint"] == b["gt_fingerprint"] and a["identity"] == b["identity"]
    assert a["annotation_fingerprint"] != b["annotation_fingerprint"]


def test_rejudge_uses_each_frozen_turn_and_rejects_damaged_snapshot(tmp_path, monkeypatch):
    from test_suites import _judged_state, _wait_until

    from photobench.application.run_execution.judge import JudgeRunner

    state = _judged_state()
    definition = {
        "qa_id": "q1",
        "answer": "final reference",
        "conversation": [
            {**annotated(), "reference_answer": "old place"},
            {**annotated(), "question_type": "fact_time", "reference_answer": "old date"},
        ],
    }
    state["qa_snapshot"] = {"rows": [definition], "definition_sha256": qa_fingerprint([definition])}
    state["items"][0]["runtime_turns"] *= 2
    seen = []

    def judge(_self, _message, reference, _answer, **kwargs):
        seen.append((reference, kwargs["question_type"]))
        return {"score": 2}

    monkeypatch.setattr(JudgeRunner, "judge", judge)
    monkeypatch.setattr(JudgeRunner, "judge_task_action", lambda *_a, **_k: {"correct": True})
    suites = _suites(tmp_path, store=FakeRunStore({"run-1": state}))
    suites.start_rejudge("run-1", {"system_prompt": "test"})
    assert _wait_until(lambda: state["rejudge"]["status"] == "completed")
    assert sorted(seen) == [("old date", "fact_time"), ("old place", "fact_place")]
    assert state["items"][0]["judge"]["ground_truth_source"] == "frozen_qa_snapshot"
    state["qa_snapshot"]["rows"][0]["answer"] = "damaged"
    before = copy.deepcopy(state)
    with pytest.raises(InvalidRequestError, match="快照校验失败"):
        suites.start_rejudge("run-1", {"system_prompt": "test"})
    assert state == before


def test_legacy_rejudge_requires_and_uses_saved_turn_references(tmp_path, monkeypatch):
    from test_suites import _judged_state, _wait_until

    from photobench.application.run_execution.judge import JudgeRunner

    state = _judged_state()
    state["items"][0]["runtime_turns"] *= 2
    suites = _suites(tmp_path, store=FakeRunStore({"run-1": state}))
    with pytest.raises(InvalidRequestError, match="未记录逐轮参考答案"):
        suites.start_rejudge("run-1", {"system_prompt": "test"})
    state["items"][0]["runtime_turns"] = [
        {"message": "one", "answer": "model one", "reference_answer": "saved one"},
        {"message": "two", "answer": "model two", "reference_answer": "saved two"},
    ]
    seen = []

    def judge(_self, _message, reference, _answer, **_kwargs):
        seen.append(reference)
        return {"score": 1}

    monkeypatch.setattr(JudgeRunner, "judge", judge)
    monkeypatch.setattr(JudgeRunner, "judge_task_action", lambda *_a, **_k: {})
    suites.start_rejudge("run-1", {"system_prompt": "test"})
    assert _wait_until(lambda: state["rejudge"]["status"] == "completed")
    assert sorted(seen) == ["saved one", "saved two"]


def test_legacy_single_turn_rejudge_keeps_saved_item_reference(tmp_path, monkeypatch):
    from test_suites import _judged_state, _wait_until

    from photobench.application.run_execution.judge import JudgeRunner

    state = _judged_state()
    state["items"][0]["runtime_turns"][0]["reference_answer"] = ""
    seen = []

    def judge(_self, _message, reference, _answer, **_kwargs):
        seen.append(reference)
        return {"score": 1}

    monkeypatch.setattr(JudgeRunner, "judge", judge)
    monkeypatch.setattr(JudgeRunner, "judge_task_action", lambda *_a, **_k: {})
    suites = _suites(tmp_path, store=FakeRunStore({"run-1": state}))
    suites.start_rejudge("run-1", {"system_prompt": "test"})
    assert _wait_until(lambda: state["rejudge"]["status"] == "completed")
    assert seen == ["a.jpg"]


def test_real_execution_records_single_turn_labels_and_reference():
    from fakes_execution import FakeSentrixClient
    from test_run_execution import _completed_turn

    row = {**annotated(), "qa_id": "q", "question": "where", "answer": "saved place"}
    execution, *_ = _execution(client=FakeSentrixClient(turns={"turn_1": [_completed_turn()]}))
    result = execution._evaluate_one(row, "scope", {}, _request())
    turn = result["runtime_turns"][0]
    assert turn["reference_answer"] == "saved place"
    for key in annotated():
        assert turn[key] == row[key]


def test_single_saved_turn_can_use_saved_item_labels_but_multiple_turns_cannot():
    data = item(**annotated(), runtime_turns=[{"judge": {"score": 2}}])
    single = case_from_item(data, 0)
    assert single["turns"][0]["content_subtype"] == ["fact_place"]
    data["runtime_turns"] *= 2
    multi = case_from_item(data, 0)
    assert all(t["content_subtype"] == ["unknown"] for t in multi["turns"])
