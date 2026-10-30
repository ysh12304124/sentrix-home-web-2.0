"""``domain/runs/analysis.py`` 的用例：1.0 那批纯函数的等价性。

这一层没有 IO，所以每条断言都是**定点可杀**的：改掉一个分支就红在对应那一行。
"""

from __future__ import annotations

import json

import pytest

from photobench.domain.runs import analysis


def _item(**over: object) -> dict:
    item = {
        "qa_id": "q1",
        "answerability": "answerable",
        "judge": {"score": 2, "status": "completed"},
        "wall_clock_ms": 1000,
    }
    item.update(over)
    return item


@pytest.mark.parametrize(
    ("counts", "legacy_matches", "expected"),
    [(None, ["a.jpg"], 1), ({"matched": 1, "gt": 1}, [], 1), ({"matched": 0, "gt": 1}, ["a.jpg"], 0)],
)
def test_item_summary_uses_stored_media_counts(counts, legacy_matches, expected):
    item = _item(
        media_retrieval_counts=counts,
        matched_file_names=legacy_matches,
        retrieval_media_refs=[{"media_type": "image", "media_id": "a.jpg"}],
    )
    summary = analysis.item_summary(item, 0)
    assert summary["matched_count"] == expected
    assert summary["ground_truth_count"] == 1
    assert item["media_retrieval_counts"] == counts


# ---- Judge 一致性 ---------------------------------------------------------


def test_a_praising_zero_score_is_inconsistent():
    """1.0 ``:701``：打了 0 分却在理由里夸「回答正确」的 judge 输出不算数。"""
    assert analysis.judge_score_consistency(0, "模型回答正确，准确") is False
    assert analysis.judge_consistency_status({"score": 0, "reason": "模型回答正确"}) == "inconsistent"
    assert analysis.judge_score_for_summary({"score": 0, "reason": "模型回答正确"}) is None


def test_a_negated_praise_does_not_trip_the_inconsistency_rule():
    """否定式褒义（「回答不正确」）不该被当成夸奖——1.0 ``:705`` 那句正则的用意。"""
    assert analysis.judge_score_consistency(0, "回答不正确") is True
    assert analysis.judge_score_for_summary({"score": 0, "reason": "回答不正确"}) == 0


@pytest.mark.parametrize(
    "reason",
    [
        "该问题可回答，正确地点已由参考答案给出。模型未给出正确答案，因此得0分。",
        "问题可以回答。正确时间已知，但模型未回答核心问题。",
        "该问题可回答且正确地点已知，但模型没有回答。",
        "模型未回答，正确答案见参考信息。",
    ],
)
def test_zero_score_is_not_rejected_for_praise_of_the_question_or_reference(reason: str):
    assert analysis.judge_score_consistency(0, reason) is True
    current = {"score": 0, "reason": reason, "consistency_version": 2}
    assert analysis.judge_score_for_summary(current) == 0
    assert analysis.item_summary({"judge": current}, 0)["judge"]["consistency_version"] == 2


def test_unversioned_judge_records_keep_their_original_consistency_policy():
    reason = "该问题可回答，正确地点已知；模型没有回答。"
    assert analysis.judge_score_for_summary({"score": 0, "reason": reason}) is None
    assert (
        analysis.judge_score_for_summary({"score": None, "raw_text": '{"score":0}', "consistency_version": 2})
        is None
    )


@pytest.mark.parametrize(
    "reason", ["模型回答正确，与参考答案一致", "回答基本准确", "与标准答案完全一致", "核心信息完整，符合预期"]
)
def test_version_two_still_rejects_a_praising_zero(reason: str):
    assert analysis.judge_score_consistency(0, reason) is False
    assert analysis.judge_score_for_summary({"score": 0, "reason": reason, "consistency_version": 2}) is None


def test_a_non_zero_score_is_always_consistent():
    assert analysis.judge_score_consistency(1, "回答正确") is True
    assert analysis.judge_consistency_status({"score": 2}) is None


@pytest.mark.parametrize(
    "reason",
    [
        "模型回答给出的“三垟耀址基石”与正确内容完全不符，属于核心答案错误，因此得0分。",
        "模型回答提到正确答案，但最终写的是另一地点。",
        "助手的回答没有提供正确内容。",
        "答案引用正确时间却否定了问题的前提。",
    ],
)
def test_v4_reference_narration_is_not_answer_level_praise(reason):
    assert analysis.judge_score_consistency(0, reason) is True
    assert analysis.judge_score_for_summary({"score": 0, "reason": reason, "consistency_version": 4}) == 0


@pytest.mark.parametrize(
    "reason",
    [
        "模型回答正确",
        "助手的回答内容完全准确",
        "该回答基本正确",
        "模型答案总体上是准确的",
        "回答中的核心信息正确",
        "模型回答与参考答案完全一致",
    ],
)
def test_v4_still_rejects_real_answer_level_praise_at_zero(reason):
    assert analysis.judge_score_consistency(0, reason) is False
    assert analysis.judge_score_for_summary({"score": 0, "reason": reason, "consistency_version": 4}) is None


def test_v3_history_keeps_the_recorded_consistency_policy():
    reason = "模型回答给出的“三垟耀址基石”与正确内容完全不符，属于核心答案错误。"
    original = {"score": 0, "reason": reason, "consistency_version": 3}
    assert analysis.judge_score_consistency(0, reason, version=3) is False
    assert analysis.judge_score_for_summary(original) is None
    assert original == {"score": 0, "reason": reason, "consistency_version": 3}


def test_judge_score_for_summary_rejects_out_of_range_scores():
    assert analysis.judge_score_for_summary({"score": 3}) is None
    assert analysis.judge_score_for_summary({}) is None


# ---- 媒体键 ---------------------------------------------------------------


def test_media_key_canonicalises_video_extensions():
    """视频键要去掉扩展名且大小写归一（1.0 ``:947``）——「A.MP4」与「a.mov」是同一个。"""
    assert analysis.media_key("video", "Album/A.MP4") == ("video", "a")
    assert analysis.media_key("video", "a.mov") == ("video", "a")
    assert analysis.media_key("image", "Album/B.JPG") == ("image", "b.jpg")
    assert analysis.media_key(None, "video-12") == ("video", "video-12")


def test_infer_media_type_prefers_the_explicit_value():
    assert analysis.infer_media_type("x.mp4", "image") == "image"
    assert analysis.infer_media_type("x.mp4") == "video"


# ---- 排名指标 -------------------------------------------------------------


def test_ranking_metrics_rewards_the_first_position():
    gt = {("image", "a")}
    first = analysis.ranking_metrics([("image", "a")], gt)
    second = analysis.ranking_metrics([("image", "x"), ("image", "a")], gt)
    assert first["r_at_1"] == 1.0 and first["mrr"] == 1.0
    assert second["r_at_1"] == 0.0 and second["mrr"] == 0.5
    # 没有 GT 的题**不产出**任何排名指标（1.0 ``:1270`` 的第一行）；有 GT 但一条候选
    # 都没排出来则是一组 0，不是空——两件事不能混成一个。
    assert analysis.ranking_metrics([("image", "a")], set()) == {}
    assert analysis.ranking_metrics([], gt)["r_at_1"] == 0.0


def test_mean_ranking_metrics_keeps_average_precision_over_every_question():
    """R@K 只平均「正例数 ≤ K」的题（1.0 ``:1294`` 的注释），mAP 用全部题。"""
    rows = [
        {
            "image_ranking": {
                "gt_count": 2,
                "ranked_count": 2,
                "r_at_5": 1.0,
                "hit_at_5": 1.0,
                "r_at_10": 1.0,
                "hit_at_10": 1.0,
                "r_at_1": 0.5,
                "hit_at_1": 1.0,
                "mrr": 1.0,
                "average_precision": 1.0,
                "p_at_5": 1.0,
            }
        },
        {
            "image_ranking": {
                "gt_count": 25,
                "ranked_count": 5,
                "r_at_5": 0.2,
                "hit_at_5": 1.0,
                "r_at_10": 0.2,
                "hit_at_10": 1.0,
                "r_at_1": 0.04,
                "hit_at_1": 1.0,
                "mrr": 0.5,
                "average_precision": 0.2,
                "p_at_5": 0.2,
            }
        },
    ]
    result = analysis.mean_ranking_metrics(rows)
    assert result["r_at_5_question_count"] == 1
    assert result["r_at_5"] == 1.0
    assert result["question_count"] == 2
    assert result["mrr"] == 0.75


# ---- 汇总 -----------------------------------------------------------------


def test_capability_summary_uses_the_legacy_image_only_path():
    """没有 ``retrieval_media_refs`` 的历史 run 走图片专用那条分支（1.0 ``:4666``）。"""
    items = [
        _item(
            retrieval_image_ids=["a.jpg"],
            predicted_file_names=["a.jpg"],
            matched_file_names=["a.jpg"],
            retrieval_recall=1.0,
        ),
    ]
    summary = analysis.capability_summary(items)
    assert summary["retrieval_metric_scope"] == "legacy_image_only"
    assert summary["retrieval_recall_micro"] == 1.0
    assert summary["answer_quality_mean"] == 2.0
    assert summary["judge_distribution"] == {"0": 0, "1": 0, "2": 1}
    assert summary["media_retrieval_metric_count"] is None


def test_capability_summary_uses_the_typed_media_path():
    """带类型的引用走 micro/macro 那条（``:4648``）。"""
    items = [
        _item(
            retrieval_media_refs=[{"media_type": "image", "media_id": "a.jpg"}],
            media_retrieval_counts={"gt": 1, "predicted": 1, "matched": 1},
            image_retrieval_counts={"gt": 1, "predicted": 1, "matched": 1},
            video_retrieval_counts={"gt": 0, "predicted": 0, "matched": 0},
        ),
    ]
    summary = analysis.capability_summary(items)
    assert summary["retrieval_metric_scope"] == "all_media"
    assert summary["media_retrieval_recall_micro"] == 1.0
    assert summary["media_retrieval_metric_count"] == 1


def test_the_candidate_metrics_are_separate_from_the_delivery_metrics():
    """候选级与交付级是**两组并列的数**，不是一组。

    这一条钉的是「零交付 ≠ 零召回」：同一个 item 上，候选集抓到了 GT（candidate recall
    = 1.0）而交付集是空的（delivery recall = 0.0）。如果两者合用一格，这道题会被读成
    「检索全军覆没」——而它其实是「检索找到了，模型没交图」，两件事的修法相反。
    """
    items = [
        _item(
            retrieval_media_refs=[
                {"media_type": "image", "media_id": "a.jpg"},
                {"media_type": "image", "media_id": "b.jpg"},
            ],
            media_retrieval_counts={"gt": 2, "predicted": 0, "matched": 0},
            image_retrieval_counts={"gt": 2, "predicted": 0, "matched": 0},
            video_retrieval_counts={"gt": 0, "predicted": 0, "matched": 0},
            candidate_retrieval_counts={"gt": 2, "predicted": 2, "matched": 2},
            retrieval_recall=0.0,
        )
    ]

    summary = analysis.capability_summary(items)

    assert summary["media_retrieval_recall_micro"] == 0.0
    assert summary["candidate_retrieval_recall_micro"] == 1.0
    assert summary["candidate_retrieval_precision_micro"] == 1.0
    assert summary["candidate_metric_count"] == 1


def test_a_run_without_candidates_reports_none_not_zero():
    """**「没有候选数据」与「召回率为零」必须分得开。**

    没有候选集的旧 run 若被读成 0.0，会被当成「检索全军覆没」，而它只是那次没记这件事。
    分母为 0 时给 ``None``。
    """
    items = [
        _item(
            retrieval_media_refs=[
                {"media_type": "image", "media_id": "a.jpg"},
                {"media_type": "image", "media_id": "b.jpg"},
            ],
            media_retrieval_counts={"gt": 2, "predicted": 0, "matched": 0},
            image_retrieval_counts={"gt": 2, "predicted": 0, "matched": 0},
            video_retrieval_counts={"gt": 0, "predicted": 0, "matched": 0},
            retrieval_recall=0.0,
        )
    ]

    summary = analysis.capability_summary(items)

    assert summary["candidate_metric_count"] == 0
    assert summary["candidate_retrieval_recall_micro"] is None


def test_the_recall_layer_reads_the_candidate_set_when_there_is_one():
    """归因表的 R 层问的是**检索**，不是交付。

    这一格此前用的是 ``retrieval_recall``——分母 GT、分子**交付集**，于是零交付的题
    R 层必然亮红（实测 424 道），而那些题里有相当一部分检索其实抓到了。``source`` /
    ``recall_source`` 标出用的是哪一档口径，免得两批 run 的 R 层被当成同一个数比。
    """
    with_candidates = analysis.derive_attribution(_item(candidate_retrieval_recall=1.0, retrieval_recall=0.0))
    without = analysis.derive_attribution(_item(retrieval_recall=1.0))

    assert with_candidates["layers"]["R"] == "pass"
    assert with_candidates["recall_source"] == "candidate"
    assert without["recall_source"] == "delivery"
    assert "交付口径" in without["detail"]["R"]


@pytest.mark.parametrize("answerability", ["unanswerable", "insufficient_evidence"])
def test_capability_summary_excludes_unanswerable_questions_from_retrieval_metrics(answerability):
    items = [
        _item(answerability=answerability, retrieval_image_ids=["a.jpg"]),
        _item(
            qa_id="q2",
            retrieval_image_ids=["a.jpg"],
            matched_file_names=["a.jpg"],
            predicted_file_names=["a.jpg"],
            retrieval_recall=1.0,
        ),
    ]
    summary = analysis.capability_summary(items)
    assert summary["retrieval_excluded_unanswerable_count"] == 1
    assert summary["retrieval_metric_count"] == 1


def test_effective_summary_derives_image_ranking_from_the_candidate_order():
    """1.0 ``:6664``：老 run 没存 ``image_ranking`` 时，从候选顺序重算而不是留空。"""
    item = _item(
        gt_images=[{"media_type": "image", "media_id": "a.jpg"}],
        retrieved_candidate_media=[{"media_type": "image", "media_id": "a.jpg"}],
    )
    summary = analysis.effective_summary({"items": [item], "qa_count": 1, "phases": {}})
    assert summary["image_ranking"]["r_at_1"] == 1.0
    assert summary["total"] == 1


def test_effective_summary_marks_agent_stability_on_items_with_a_trace():
    item = _item(
        execution_trace=[
            {"stage": "model", "call_type": "agent", "parse_status": "success", "raw": '{"action": "final"}'}
        ]
    )
    analysis.effective_summary({"items": [item], "phases": {}})
    assert item["agent_stability"]["json_parse_total"] == 1
    assert item["agent_stability"]["json_parse_success"] == 1


# ---- 列表条目与 item 投影 --------------------------------------------------


def test_run_list_entry_drops_none_fields_and_keeps_the_summary_subset():
    state = {
        "run_id": "r1",
        "status": "completed",
        "album_id": "album3",
        "qa_count": 2,
        "items": [_item(), _item(qa_id="q2")],
        "phases": {"qa_eval": {"status": "done", "progress": 1.0, "ignored": "x"}},
        "rejudge": {"status": "running", "completed": 1, "total": 2, "junk": 1},
    }
    entry = analysis.run_list_entry(state)
    assert entry["run_id"] == "r1"
    assert entry["item_count"] == 2
    assert "model_name" not in entry  # 原状态里没有 -> 不进条目
    assert entry["phases"] == {"qa_eval": {"status": "done", "progress": 1.0}}
    assert entry["rejudge"] == {"status": "running", "completed": 1, "total": 2}
    assert entry["summary"]["answer_quality_mean"] == 2.0
    assert "items" not in entry


def test_item_summary_counts_ground_truth_media():
    summary = analysis.item_summary(
        _item(
            retrieval_image_ids=["a.jpg", "b.jpg"],
            retrieval_video_ids=["c.mp4"],
            retrieval_media_refs=[{"media_type": "image", "media_id": "a.jpg"}],
        ),
        index=3,
        review={"verdict": "correct"},
    )
    assert summary["index"] == 3
    assert summary["ground_truth_image_count"] == 2
    assert summary["ground_truth_video_count"] == 1
    assert summary["ground_truth_count"] == 1  # media_refs 优先
    assert summary["review"] == {"verdict": "correct"}


def test_hydrate_qa_metadata_merges_dataset_fields_and_builds_tags():
    hydrated = analysis.hydrate_qa_metadata(
        {"qa_id": "q1", "judge": {"score": 1}},
        {"q1": {"task_type": "T1", "difficulty": "hard", "tags": ["x"]}},
    )
    assert hydrated["task_type"] == "T1"
    assert hydrated["tags"] == ["x", "task:T1", "difficulty:hard"]
    # 1 分的 judge 让 J 层判失败（1.0 ``:4232``）——归因不是「没问题」而是「回答质量问题」。
    assert hydrated["attribution"]["primary"] == "J"


def test_hydrate_qa_metadata_recomputes_legacy_precision_recall():
    hydrated = analysis.hydrate_qa_metadata(
        {"qa_id": "q1", "retrieval_image_ids": ["a.jpg", "b.jpg"], "predicted_file_names": ["a.jpg"]},
        {},
    )
    assert hydrated["retrieval_precision"] == 1.0
    assert hydrated["retrieval_recall"] == 0.5


# ---- 遥测采样 -------------------------------------------------------------


def test_attach_gpu_sample_history_skips_broken_lines_and_reports_peaks():
    live: dict = {}
    lines = [
        json.dumps({"_t": 1.0, "phase": "qa_eval", "temperature_c": 40.0, "power_draw_w": 100}),
        "{ not json",
        json.dumps({"_t": 2.0, "phase": "qa_eval", "temperature_c": 60.0}),
    ]
    analysis.attach_gpu_sample_history(live, lines)
    assert live["samples_count"] == 2
    assert live["peak"]["temperature_c"] == 60.0
    assert live["latest"]["temperature_c"] == 60.0


def test_attach_gpu_sample_history_without_lines_leaves_an_empty_history():
    live: dict = {}
    analysis.attach_gpu_sample_history(live, [])
    assert live["history"] == []


def test_downsample_history_keeps_phase_boundaries():
    history = [{"phase": "a", "temperature_c": i} for i in range(300)] + [
        {"phase": "b", "temperature_c": i} for i in range(300)
    ]
    downsampled = analysis.downsample_history(history, limit=240)
    assert len(downsampled) <= 241
    assert any(point["phase"] == "b" for point in downsampled)
    assert downsampled[0]["phase"] == "a"
    assert downsampled[-1]["temperature_c"] == 299


# ---- 分页 -----------------------------------------------------------------


def test_paginate_rows_clamps_page_size_and_page():
    rows = list(range(50))
    page = analysis.paginate_rows(rows, 2, 20)
    assert page["runs"] == list(range(20, 40))
    assert page["pages"] == 3 and page["has_next"] is True and page["has_previous"] is True
    assert analysis.paginate_rows(rows, 99, 20)["page"] == 3
    assert analysis.paginate_rows(rows, 1, 999)["page_size"] == 100


# ---- 完成口径与 run_valid（2026-10-15）--------------------------------------


def _exec_item(*, execution: str, judge: str, **over: object) -> dict:
    return _item(execution_status=execution, judge_status=judge, **over)


def test_completed_counts_executed_items_not_judge_states():
    """``completed`` 数的是执行成功的题，不是「有 judge 状态」的题。

    1.0 ``:7286`` 用的是 ``judge_status in {completed, failed, skipped}``，而取消掉的题
    在 2.0 里正是被标成 ``skipped`` 的那一批——于是取消的 run 在列表页显示
    「487/487 完成」，实际只有 332 题有答案。1.0 极少遇到取消，2.0 已经遇到两次
    （2026-10-08、10-10）。
    """
    state = {
        "qa_count": 3,
        "items": [
            _exec_item(execution="completed", judge="completed"),
            _exec_item(execution="completed", judge="completed"),
            _exec_item(execution="failed", judge="skipped"),
        ],
    }

    summary = analysis.effective_summary(state)

    assert summary["total"] == 3
    assert summary["completed"] == 2


def test_run_valid_is_recomputed_on_the_read_side():
    """取消的 run 不该被当成一份可信的结果。

    2.0 原先只在 ``_phase_qa_eval`` 收尾时写一次 ``run_valid``，取消时那段根本跑不到，
    于是它永远停在 ``_initial_state`` 的初值 ``False``——一个被取消的 run 与一个真失败
    的 run 在界面上长得一样，而两者的处置相反：前者续跑，后者查原因。
    """
    state = {
        "qa_count": 2,
        "phases": {"qa_eval": {"status": "done"}},
        "items": [
            _exec_item(execution="completed", judge="completed"),
            _exec_item(execution="completed", judge="completed"),
        ],
    }
    assert analysis.effective_summary(state)["run_valid"] is True

    state["items"][1] = _exec_item(execution="failed", judge="skipped")
    assert analysis.effective_summary(state)["run_valid"] is False


def test_run_valid_is_false_when_a_phase_did_not_finish_cleanly():
    """1.0 ``:3819-3822``：有 partial/failed 相位就不算一份干净的结果。"""
    state = {
        "qa_count": 1,
        "phases": {"qa_eval": {"status": "partial"}},
        "items": [_exec_item(execution="completed", judge="completed")],
    }

    assert analysis.effective_summary(state)["run_valid"] is False


def test_judge_summary_rejects_boolean_and_derives_explicit_string_without_changing_history():
    from copy import deepcopy

    from photobench.domain.runs.analysis import judge_projection, judge_score_for_summary

    for score in (True, False, "", [], {}, "得2分"):
        assert judge_score_for_summary({"score": score}) is None
    old = {"score": None, "raw_text": '{"score":"1.0","reason":"部分满足"}', "judge_status": "completed"}
    snapshot = deepcopy(old)
    projected = judge_projection(old)
    assert old == snapshot
    assert projected["score"] == 1
    assert projected["score_derivation"]["source"] == "raw_text"
    assert projected["score_derivation"]["original_score"] is None
    assert judge_score_for_summary(old) == 1
    old["reason"] = "judge_inconsistent_score_reason"
    assert judge_score_for_summary(old) is None
    old["reason"] = "judge_error: failed"
    old["judge_status"] = "failed"
    assert judge_score_for_summary(old) is None


@pytest.mark.parametrize("answerability", ["unanswerable", " INSUFFICIENT_EVIDENCE "])
def test_refusal_exclusion_covers_typed_candidate_ranking_and_read_projections(answerability):
    import copy

    refusal = _item(
        answerability=answerability,
        retrieval_media_refs=[{"media_type": "image", "media_id": "a.jpg"}],
        retrieval_image_ids=["a.jpg"],
        predicted_file_names=[],
        retrieval_recall=0.0,
        candidate_retrieval_recall=0.0,
        media_retrieval_counts={"gt": 100, "predicted": 100, "matched": 0},
        image_retrieval_counts={"gt": 100, "predicted": 100, "matched": 0},
        video_retrieval_counts={"gt": 100, "predicted": 100, "matched": 0},
        candidate_retrieval_counts={"gt": 100, "predicted": 100, "matched": 0},
        image_ranking=analysis.ranking_metrics([], {("image", "a.jpg")}),
    )
    ordinary = _item(
        qa_id="partial",
        answerability="partially_answerable",
        retrieval_media_refs=[{"media_type": "image", "media_id": "b.jpg"}],
        **{
            f"{prefix}_retrieval_counts": {"gt": 1, "predicted": 1, "matched": 1}
            for prefix in ("media", "image", "video", "candidate")
        },
        image_ranking=analysis.ranking_metrics([("image", "b.jpg")], {("image", "b.jpg")}),
    )
    before = copy.deepcopy(refusal)
    summary = analysis.capability_summary([refusal, ordinary])
    assert summary["retrieval_excluded_unanswerable_count"] == 1
    for prefix in ("media", "image", "video", "candidate"):
        assert summary[f"{prefix}_retrieval_recall_micro"] == 1
    assert summary["image_ranking"]["mrr"] == 1
    assert summary["judge_valid_count"] == 2
    assert analysis.capability_summary([refusal])["retrieval_recall_micro"] is None
    assert analysis.capability_summary([refusal])["candidate_retrieval_recall_micro"] is None
    hydrated = analysis.hydrate_qa_metadata(refusal, {})
    assert hydrated["retrieval_metrics_applicable"] is False
    assert hydrated["retrieval_recall"] is None
    assert hydrated["candidate_retrieval_recall"] is None
    assert hydrated["image_ranking"] == {}
    assert hydrated["attribution"]["layers"]["R"] == "na"
    assert analysis.item_summary(refusal, 0)["media_retrieval_recall"] is None
    assert analysis.item_summary(refusal, 0)["retrieval_metrics_applicable"] is False
    assert refusal == before


@pytest.mark.parametrize("answerability", ["answerable", "partially_answerable", "not_applicable", ""])
def test_retrieval_eligibility_preserves_other_questions(answerability):
    assert analysis.retrieval_metric_eligible({"answerability": answerability})


def test_recomputed_summary_drops_saved_refusal_candidate_and_ranking_values():
    refusal = _item(
        answerability="insufficient_evidence",
        candidate_retrieval_counts={"gt": 1, "predicted": 1, "matched": 1},
        image_ranking=analysis.ranking_metrics([("image", "a.jpg")], {("image", "a.jpg")}),
    )
    state = {
        "items": [refusal],
        "summary": {"candidate_retrieval_recall_micro": 1.0, "image_ranking": {"mrr": 1.0}},
    }
    assert analysis.effective_summary(state)["candidate_retrieval_recall_micro"] is None
    assert analysis.effective_summary(state)["image_ranking"] is None


def test_multiturn_summary_uses_first_message_without_overwriting_question():
    item = {
        "question": "是国庆在河北那次。",
        "turn_status": "complete",
        "runtime_turns": [
            {"index": 0, "message": "找婚礼舞台留影"},
            {"index": 1, "message": "是国庆在河北那次。"},
        ],
        "conversation_context_mode": "shared_conversation_id",
    }
    summary = analysis.item_summary(item, 8)
    assert summary["display_question"] == "找婚礼舞台留影"
    assert summary["conversation_turn_count"] == 2
    assert summary["agent_status"] == "complete"
    assert summary["question"] == item["question"]
    assert analysis.hydrate_qa_metadata(item, {})["display_question"] == summary["display_question"]
    assert "display_question" not in item


def test_tool_binding_uses_successful_attempt_in_same_turn_only():
    calls = [
        {"conversation_turn": 0, "step_id": "step_1", "status": "complete"},
        {"conversation_turn": 1, "step_id": "step_1", "status": "error"},
        {"conversation_turn": 1, "step_id": "step_1", "status": "complete"},
    ]
    tools = [
        {"conversation_turn": 1, "parent_step_id": "step_1", "model_call_index": 1},
        {"conversation_turn": 2, "parent_step_id": "step_1", "model_call_index": 0},
    ]
    result = analysis.bind_tool_calls_to_model_rounds(tools, [], calls)
    assert result[0]["model_call_index"] == 2
    assert "model_call_index" not in result[1]
    assert result[1]["round_binding_source"] == "unresolved"
    assert tools[0]["model_call_index"] == 1
