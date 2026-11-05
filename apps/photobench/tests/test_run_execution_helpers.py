"""``domain.runs.execution`` 的用例：主链的纯计算。

**这一份里每一条都要能区分「实现正确」与「实现错误」两种口径**——本仓库已经五次
踩过「夹具没有区分力」的坑（最近一次是 S9b-1 的路径穿越用例：断言「抛 KeyError」，
而不拦也一样抛）。所以每条都带一个反面情形，或断言一个只有正确实现才给得出的值。
"""

from __future__ import annotations

import pytest

from photobench.domain.runs.execution import (
    album_media_entries,
    import_directories,
    index_assets_by_name,
    modality_metrics,
    normalize_media_refs,
    qa_completion_errors,
    resolve_gt_media,
    resolve_predicted_media,
    safe_slug,
    selected_asset_ids,
    summarize_progress,
)


def test_safe_slug_keeps_only_url_safe_characters_and_falls_back():
    assert safe_slug("PhotoBench-20261004-120000 album/3") == "photobench-20261004-120000-album-3"
    assert safe_slug("///") == "x"
    assert safe_slug("", fallback="run") == "run"


def test_album_media_entries_puts_photos_before_videos_and_dedupes():
    """顺序是 manifest 的顺序：photo 在前、video 在后（1.0 ``:1587``）。

    顺序在这里是**载荷**，不是风格：``import_directories`` 与逐目录导入的先后都
    跟着它走。
    """
    manifest = {"photos": ["p/b.jpg", "p/a.jpg"], "videos": ["v/c.mp4", "p/a.jpg"]}
    assert album_media_entries(manifest) == ["p/b.jpg", "p/a.jpg", "v/c.mp4"]


def test_import_directories_returns_each_media_parent_once_in_manifest_order():
    """2.0 的整册导入一次只吃一个扁平目录，所以 photos 与 videos 分居两个目录时
    必须导两次。**顺序跟着 manifest**：反过来会让 ``per_directory`` 的读数与
    manifest 对不上。"""
    manifest = {"photos": ["photos/a.jpg", "photos/b.jpg"], "videos": ["videos/c.mp4"]}
    assert import_directories(manifest) == ["photos", "videos"]


def test_import_directories_handles_files_at_the_album_root():
    """manifest 直接写文件名（没有子目录）时，导入的目录就是 album 根（``""``）。"""
    assert import_directories({"photos": ["a.jpg"]}) == [""]
    assert import_directories({}) == []


def test_index_assets_by_name_groups_duplicates_instead_of_picking_one():
    """同名资产要全留着：``resolve_gt_media`` 靠候选个数区分 ``ok`` 与 ``ambiguous``，
    丢成单值之后「库里有两张同名」会被当成「正好找到一张」。"""
    assets = [{"id": "a", "file_name": "x/a.jpg"}, {"id": "b", "file_name": "y/a.jpg"}]
    assert index_assets_by_name(assets) == {"a.jpg": [assets[0], assets[1]]}


def test_normalize_media_refs_prefers_typed_refs_over_the_legacy_id_lists():
    """``{prefix}_media_refs`` 存在时**忽略** ``_image_ids`` / ``_video_ids``（1.0 ``:955``）。

    两种都给时以 typed 为准，因为只有它带类型——旧字段的视频暗示来自字段名本身。
    """
    row = {
        "retrieval_media_refs": [{"media_type": "video", "media_id": "clip"}],
        "retrieval_image_ids": ["a.jpg"],
    }
    assert normalize_media_refs(row) == [{"media_type": "video", "media_id": "clip"}]


def test_normalize_media_refs_upgrades_legacy_video_ids_with_the_video_type():
    """老题只有 ``_video_ids``，光看 id（常常就是普通文件名）猜不出类型。"""
    row = {"retrieval_image_ids": ["a.jpg"], "retrieval_video_ids": ["clip"]}
    assert normalize_media_refs(row) == [
        {"media_type": "image", "media_id": "a.jpg"},
        {"media_type": "video", "media_id": "clip"},
    ]


def test_normalize_media_refs_dedupes_by_media_key():
    """同一张图在 ``_image_ids`` 里出现两次只算一次——GT 集合是集合，不是列表。"""
    row = {"retrieval_image_ids": ["a.jpg", "a.jpg", "b.jpg"]}
    assert normalize_media_refs(row) == [
        {"media_type": "image", "media_id": "a.jpg"},
        {"media_type": "image", "media_id": "b.jpg"},
    ]


def test_selected_asset_ids_keeps_delivery_order_and_dedupes():
    """交付顺序是模型的选择顺序（1.0 用 ``dict.fromkeys``），去重但保序。"""
    turn_result = {
        "attachments": [
            {"handle": "h1", "asset_id": "b"},
            {"handle": "h2", "asset_id": "a"},
            {"handle": "h3", "asset_id": "b"},
        ]
    }
    assert selected_asset_ids(turn_result) == ["b", "a"]
    assert selected_asset_ids({}) == []
    assert selected_asset_ids({"attachments": [{"handle": "h"}]}) == []


def test_resolve_predicted_media_falls_back_from_a_keyframe_to_its_parent_video():
    """模型交付的常常是抽出来的关键帧，而 GT 标的是那段视频（1.0 ``:1135``）。

    **没有这条回落，同一件事会被算成「没召回」**——而这正是本用例要区分的那两种口径：
    回落 → ``file_name`` 是 ``clip.mp4``；不回落 → 是 ``clip_0001.jpg``。
    """
    assets = [
        {"id": "v1", "file_name": "clip.mp4", "media_type": "video"},
        {
            "id": "k1",
            "file_name": "clip_0001.jpg",
            "media_type": "image",
            "metadata_json": {"derived_kind": "video_keyframe", "parent_asset_id": "v1"},
            "source_timestamp_sec": 12.5,
        },
    ]
    by_name = index_assets_by_name(assets)
    assert resolve_predicted_media(["k1"], by_name) == [
        {
            "asset_id": "v1",
            "file_name": "clip.mp4",
            "media_type": "video",
            "media_id": "clip",
            "source_timestamp_sec": 12.5,
            "source_keyframe_asset_id": "k1",
        }
    ]


def test_resolve_predicted_media_keeps_a_keyframe_whose_parent_is_not_a_video():
    """父资产不是视频时**不回落**：回落到一张图会把一个图片交付伪装成视频召回。"""
    assets = [
        {"id": "p1", "file_name": "photo.jpg", "media_type": "image"},
        {
            "id": "k1",
            "file_name": "k.jpg",
            "media_type": "image",
            "metadata_json": {"derived_kind": "video_keyframe", "parent_asset_id": "p1"},
        },
    ]
    resolved = resolve_predicted_media(["k1"], index_assets_by_name(assets))
    assert [item["file_name"] for item in resolved] == ["k.jpg"]


def test_resolve_predicted_media_drops_ids_that_are_not_in_the_scope():
    assert resolve_predicted_media(["nope"], index_assets_by_name([])) == []


def test_resolve_gt_media_reports_why_a_reference_could_not_be_mapped():
    """三态 ``ok`` / ``missing`` / ``ambiguous``（1.0 ``:1390``）。

    **三者必须可区分**：召回率的分母都算它，但「题目标错了」与「导入漏了这张」
    的处置完全不同。只断言 ``asset_id`` 的用例对这三态没有区分力
    ——``missing`` 与 ``ambiguous`` 的 ``asset_id`` 都是 ``None``。
    """
    assets = [{"id": "only", "file_name": "a.jpg", "media_type": "image"}]
    by_name = index_assets_by_name(assets)
    gt = [
        {"media_type": "image", "media_id": "a.jpg"},
        {"media_type": "image", "media_id": "gone.jpg"},
        {"media_type": "video", "media_id": "clip"},
    ]
    resolved = {item["media_id"]: item for item in resolve_gt_media(gt, by_name, [])}
    assert resolved["a.jpg"]["mapping_status"] == "ok"
    assert resolved["a.jpg"]["asset_id"] == "only"
    assert resolved["gone.jpg"]["mapping_status"] == "missing"
    assert resolved["gone.jpg"]["asset_id"] is None
    # 视频引用没有后缀 → 补 .mp4（1.0 ``:1378``），这决定了它能不能匹配上资产名
    assert resolved["clip"]["file_name"] == "clip.mp4"
    assert resolved["clip"]["mapping_status"] == "missing"


def test_resolve_gt_media_marks_a_duplicated_file_name_as_ambiguous():
    assets = [{"id": "a", "file_name": "x/a.jpg"}, {"id": "b", "file_name": "x/a.jpg"}]
    by_name = index_assets_by_name(assets)
    item = resolve_gt_media([{"media_type": "image", "media_id": "a.jpg"}], by_name, [])[0]
    assert item["mapping_status"] == "ambiguous"
    assert item["asset_id"] is None


def test_resolve_gt_media_marks_matched_only_for_what_was_actually_delivered():
    """``matched`` 读的是**交付集**，不是「库里有没有」——这两件事混起来会让
    ``gt_media`` 里的 matched 恒为真。"""
    assets = [{"id": "a", "file_name": "a.jpg"}, {"id": "b", "file_name": "b.jpg"}]
    by_name = index_assets_by_name(assets)
    gt = [{"media_type": "image", "media_id": "a.jpg"}, {"media_type": "image", "media_id": "b.jpg"}]
    delivered = resolve_predicted_media(["a"], by_name)
    resolved = {item["media_id"]: item for item in resolve_gt_media(gt, by_name, delivered)}
    assert resolved["a.jpg"]["matched"] is True
    assert resolved["b.jpg"]["matched"] is False


def test_modality_metrics_splits_total_image_and_video():
    """1.0 ``:1206``：三组 triplet。视频与图片分别算，总量那组包含两者。"""
    gt = [
        {"media_type": "image", "media_id": "a.jpg"},
        {"media_type": "video", "media_id": "clip"},
    ]
    predicted = [
        {"media_type": "image", "media_id": "a.jpg"},
        {"media_type": "image", "media_id": "b.jpg"},
    ]
    metrics = modality_metrics(gt, predicted)
    assert metrics["media"] == {
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "matched": 1,
        "predicted": 2,
        "gt": 2,
    }
    # 图片那一组：GT 只有 a.jpg，交付了两张 → 召回 1.0 但精确率 0.5。
    # **这一组是这条用例的重点**：只交付视频那张（``predicted`` 里出现两次 image）
    # 才是「总量 0.5 而图片 0.6667」的来源，一个把总量指标直接复制给模态分组的
    # 实现会在这里红。
    assert metrics["image"]["matched"] == 1
    assert metrics["image"]["gt"] == 1
    assert metrics["image"]["predicted"] == 2
    assert metrics["image"]["precision"] == 0.5
    assert metrics["image"]["recall"] == 1.0
    assert metrics["image"]["f1"] == pytest.approx(2 / 3)
    assert metrics["video"] == {
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "matched": 0,
        "predicted": 0,
        "gt": 1,
    }


def test_modality_metrics_uses_none_not_zero_when_there_is_nothing_to_score():
    """空 GT 的召回是 ``None`` 而不是 0——**0 会拉低平均，``None`` 不参与**。

    这是 1.0 的口径（``_metric_triplet`` ``:1189``），也是本仓库的既定纪律：
    缺失不是一个测量结果。
    """
    metrics = modality_metrics([], [{"media_type": "image", "media_id": "a.jpg"}])
    assert metrics["image"]["recall"] is None
    assert metrics["image"]["precision"] == 0.0
    assert metrics["image"]["f1"] is None


def test_summarize_progress_counts_tools_by_their_result_not_their_request():
    """``tools`` 数的是「发起了几次」，``executed_tools`` 数的是「回来了几次」。

    两者分开是因为 2.0 会为同一次调用发两条（``model_action`` 与 ``tool_result``），
    而**被 guard 拦下的调用只有第一条**——把它们混成一个数就再也看不出这件事。
    """
    progress = [
        {"kind": "plan", "stage": "planning", "step_index": 1, "payload": {"requirements": [1]}},
        {"kind": "model_action", "stage": "action", "step_index": 2, "payload": {"tool": "search"}},
        {"kind": "tool_result", "stage": "tool_result", "step_index": 3, "payload": {"tool": "search"}},
        {"kind": "model_action", "stage": "action", "step_index": 4, "payload": {"tool": "facts"}},
        {
            "kind": "tool_error",
            "stage": "tool_result",
            "step_index": 5,
            "payload": {"tool": "facts", "code": "denied"},
        },
        # ``inspect`` 被**发起了两次、只回来一次**（第二次被 guard 拦在工具里）。
        # 这一条是夹具的区分力所在：只有它能让「数发出」与「数回执」给出不同的答案。
        # 每个工具恰好一次发出一次回来时，两种实现的输出完全一样，用例是空的。
        {"kind": "model_action", "stage": "action", "step_index": 6, "payload": {"tool": "inspect"}},
        {"kind": "model_action", "stage": "action", "step_index": 7, "payload": {"tool": "inspect"}},
        {"kind": "tool_result", "stage": "tool_result", "step_index": 8, "payload": {"tool": "inspect"}},
        {"kind": "terminal", "stage": "terminal", "step_index": 9, "payload": {"status": "partial"}},
    ]
    summary = summarize_progress(progress)
    assert summary["steps"] == 9
    # ``tools`` 数的是**每一次提到这个名字的事件**，所以 inspect 是 3（两次发起 + 一次回来）
    assert summary["tools"] == {"search": 2, "facts": 2, "inspect": 3}
    assert summary["executed_tools"] == {"search": 1, "facts": 1, "inspect": 1}
    assert summary["error_codes"] == {"denied": 1}
    assert summary["stages"]["terminal"] == 1


def test_summarize_progress_tolerates_the_shapes_2_0_actually_sends():
    """2.0 的 ``status`` 事件的 payload 是 ``{}``，而 ``step_index`` 只在部分事件上。

    没有这条，一个假设 payload 里一定有 tool 的实现会把 ``None`` 当工具名记进计数
    （``{"None": 12}``），而那在断言 ``tools`` 的用例里看不出来——它只在那里出现。
    """
    summary = summarize_progress(
        [
            {"kind": "status", "stage": "thinking", "step_index": 1, "payload": {}},
            {"kind": "status", "stage": "thinking"},
            "坏行",
            None,
        ]
    )
    assert summary["steps"] == 1
    assert summary["tools"] == {}
    assert summary["kinds"] == {"status": 2}


def test_endpoint_identity_preserves_model_versions_and_refuses_ambiguous_selection():
    from photobench.domain.runs.execution import model_identity

    assert model_identity({"data": [{"id": "/models/Qwen3.5.gguf"}]})["display_name"] == "Qwen3.5"
    assert model_identity({"data": [{"id": "Qwen3.5"}]})["display_name"] == "Qwen3.5"
    payload = {"data": [{"id": "a"}, {"id": "b"}]}
    assert model_identity(payload)["status"] == "unavailable"
    assert model_identity(payload, "b")["raw_id"] == "b"
    assert model_identity(payload, "missing")["status"] == "unavailable"


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_completion_counts_task_failure_once_without_inventing_a_judge_score(status):
    details = qa_completion_errors(
        [{"qa_id": "a"}],
        [{"qa_id": "a", "execution_status": status, "error": "task failed", "judge_status": "skipped"}],
        judge_required=True,
    )
    assert len(details) == 1
    assert details[0]["error_type"] == "agent_error"


def test_completion_respects_disabled_judge_and_records_earlier_turn_failure():
    item = {"qa_id": "a", "execution_status": "completed"}
    assert qa_completion_errors([{"qa_id": "a"}], [item], judge_required=False) == []
    item.update(judge_status="completed", judge={"score": "0"})
    assert qa_completion_errors([{"qa_id": "a"}], [item], judge_required=True) == []
    item["runtime_turns"] = [{"judge": {"judge_status": "failed", "reason": "earlier turn failed"}}]
    assert (
        qa_completion_errors([{"qa_id": "a"}], [item], judge_required=True)[0]["error_type"] == "judge_error"
    )


@pytest.mark.parametrize("reason", ["step_budget_exhausted", "parse_failure", "timeout", "model_error"])
@pytest.mark.parametrize("earlier_turn", [False, True])
def test_partial_product_failure_is_not_hidden_by_completed_execution(reason, earlier_turn):
    item = {
        "qa_id": "a",
        "execution_status": "completed",
        "judge_status": "completed",
        "judge": {"score": 0},
        "turn_status": "complete",
        "termination_reason": "answered",
        "runtime_turns": [{"agent_status": "partial", "termination_reason": reason}],
    }
    if not earlier_turn:
        item.update(turn_status="partial", termination_reason=reason)
    details = qa_completion_errors([{"qa_id": "a"}], [item], judge_required=True)
    assert len(details) == 1
    assert details[0]["error_type"] == "agent_error"
    assert details[0]["reason"] == reason
    assert item["judge"]["score"] == 0


def test_partial_answer_with_gap_and_recovered_calls_remain_successful():
    item = {
        "qa_id": "a",
        "execution_status": "completed",
        "turn_status": "partial",
        "termination_reason": "answered_with_gap",
        "judge_status": "completed",
        "judge": {"score": 0},
        "runtime_turns": [
            {
                "status": "partial",
                "termination_reason": "answered_with_gap",
                "execution_trace": [{"status": "error"}, {"status": "complete"}],
            }
        ],
    }
    assert qa_completion_errors([{"qa_id": "a"}], [item], judge_required=True) == []
    item["runtime_turns"][0]["status"] = "running"
    with pytest.raises(ValueError, match="nonterminal product turn"):
        qa_completion_errors([{"qa_id": "a"}], [item], judge_required=True)


@pytest.mark.parametrize(
    "rows,items",
    [
        ([{"qa_id": "a"}], []),
        ([{"qa_id": "a"}], [{"qa_id": "b", "execution_status": "completed"}]),
        ([{"qa_id": "a"}], [{"qa_id": "a", "execution_status": "running"}]),
        ([{"qa_id": "a"}], [{"qa_id": "a", "execution_status": "scheduled"}]),
        ([{"qa_id": "a"}], [{"qa_id": "a", "execution_status": "completed"}] * 2),
        ([{"qa_id": "a"}] * 2, [{"qa_id": "a", "execution_status": "completed"}]),
        ([{}], [{"qa_id": "a", "execution_status": "completed"}]),
    ],
)
def test_completion_rejects_missing_duplicate_or_nonterminal_results(rows, items):
    with pytest.raises(ValueError, match="QA result integrity"):
        qa_completion_errors(rows, items, judge_required=False)
