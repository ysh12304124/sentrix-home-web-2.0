"""关键帧分析：这一轮评测里，视频抽出来的帧到底有没有用。

1.0 ``OrchestratorRepository.get_keyframe_analysis``（``benchmark_orchestrator.py:5600-5888``）
逐行移植。**它返回的是算好的指标，不是原始数据**——1.0 就是这么做的（前端只渲染），
所以 2.0 也把计算留在后端。

**唯一的改写是数据的来路，不是口径。** 1.0 一次 ``GET /api/assets`` 就拿全了：它的
``list_assets`` 是 ``SELECT *``（``backend/db.py:1505``），``derived_kind`` /
``parent_asset_id`` / ``source_timestamp_sec`` 三个血缘列与整包 ``metadata_json``
都在那张表里。2.0 的 ``/api/assets`` 是投影，血缘列在 S10a 恢复到了响应上，
但视频的时长 / 总帧数 / 处理耗时在 ``GET /videos/{id}`` 上，所以要另外取一次并按
``asset_id`` 传进来（``video_facts``）。

**这是一处形状偏离，代价是每次分析多 N 次 HTTP**（N = scope 里的视频数，
benchmark 相册实测最多 10 个）。换来的好处是 ``/api/assets`` 不必为一个视频专属的
面板背上视频专属字段。已登记在 ``s10-deviations.md``。

口径本身一处没动，包括这两个容易被当成 bug 的地方：

1. **关键帧命中不惩罚「返回了帧而不是原片」**：一张关键帧只要它的 ``parent_asset_id``
   等于本题 GT 的那段视频，就算命中。这是 1.0 在 docstring 里写明的设计。
2. **``answer_quality_delta`` 不是因果结论**。它只是「带关键帧候选题」与「不带」两组
   Judge 均分之差，1.0 在 ``definitions`` 里专门写了这句话，照搬。
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from photobench.domain.runs.analysis import judge_score_for_summary

#: 1.0 判关键帧的同一个字面量（``:5713``）。2.0 只有一条抽帧路径
#: （``s8-deviations.md`` §3），所以不存在 1.0 那个 ``video_keyframe_webp`` 的第二取值
#: ——1.0 的读侧本来就同时认两个，这里保留同一条判据的形状，实际只会命中一个。
KEYFRAME_KIND = "video_keyframe"

#: 判「这张资产是不是视频」的字面量，与 1.0 同一个。
VIDEO_MEDIA_TYPE = "video"

#: 1.0 ``:5847`` 原样。它描述的是**数据来源**，2.0 的来源没变（同一批持久化的
#: ffprobe 元数据与处理耗时），只是取的路径从资产清单换成了 ``GET /videos/{id}``。
TIMING_SOURCE = "Sentrix video_processing_seconds + persisted ffprobe metadata"


def _metadata(asset: Mapping[str, Any]) -> Mapping[str, Any]:
    """1.0 ``:5670`` 的 ``metadata(asset)``：``metadata_json`` 不是 mapping 时当空。"""
    value = asset.get("metadata_json")
    return value if isinstance(value, Mapping) else {}


def _derived_kind(asset: Mapping[str, Any]) -> str:
    """血缘列的**两级回退**。

    1.0 是 ``item.get("derived_kind") or metadata(item).get("derived_kind")``
    （``:5713`` 的判据与 ``:5670`` 的取值合起来就是这个形状）。2.0 只写列
    （迁移 0010），第二级留着是为了读得懂指向 1.0 ``results/`` 的旧数据。
    """
    return str(asset.get("derived_kind") or _metadata(asset).get("derived_kind") or "")


def _parent_asset_id(asset: Mapping[str, Any]) -> str:
    return str(asset.get("parent_asset_id") or _metadata(asset).get("parent_asset_id") or "")


def _mean(values: Sequence[float]) -> float | None:
    """1.0 ``:5816``。**空序列给 ``None`` 而不是 0**——0 是一个会被人当真的数。"""
    return round(sum(values) / len(values), 4) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    """1.0 ``:5819``。分母为 0（或假值）给 ``None``，同上。"""
    return round(numerator / denominator, 4) if denominator else None


def _recover_from_traces(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """1.0 ``:5622-5650``：资产清单为空时的回退。

    复用的 run 可能指向一个本地 Sentrix 已经不再暴露的记忆空间。这时**从已持久化的
    工具预览里把关键帧血缘捞回来**，而不是回一张全零的表——全零的表与「这个相册真的
    没有关键帧」长得一模一样，而它们是两件事。
    """
    recovered: dict[str, dict[str, Any]] = {}
    for item in state.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        for trace in item.get("tool_trace") or []:
            if not isinstance(trace, Mapping):
                continue
            observation = trace.get("observation")
            if not isinstance(observation, Mapping):
                continue
            for preview in observation.get("preview") or []:
                if not isinstance(preview, Mapping) or preview.get("media_kind") != KEYFRAME_KIND:
                    continue
                keyframe_id = str(preview.get("asset_id") or "").strip()
                parent_id = str(preview.get("source_video_asset_id") or "").strip()
                if not keyframe_id:
                    continue
                recovered[keyframe_id] = {
                    "id": keyframe_id,
                    "file_name": preview.get("file_name") or preview.get("asset_id"),
                    "media_type": "image",
                    "derived_kind": KEYFRAME_KIND,
                    "parent_asset_id": parent_id,
                    "metadata_json": {
                        "derived_kind": KEYFRAME_KIND,
                        "parent_asset_id": parent_id,
                        "source_timestamp_sec": preview.get("source_timestamp_sec"),
                        "keyframe_selection_reason": preview.get("selection_reason") or "来自已保存工具预览",
                    },
                }
                if parent_id and parent_id not in recovered:
                    recovered[parent_id] = {
                        "id": parent_id,
                        "file_name": preview.get("source_video_file_name") or parent_id,
                        "media_type": VIDEO_MEDIA_TYPE,
                        "metadata_json": {},
                    }
    return list(recovered.values())


def keyframe_analysis(
    state: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]] | None,
    *,
    error: str = "",
    video_facts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """一个 run 的关键帧分析。

    ``assets`` 为 ``None`` 表示**去 Sentrix 取资产清单这一步失败了**（``error`` 带原因），
    与「取到了但是空的」是两种结果：前者回 ``unavailable``，后者走工具轨迹回退。

    ``video_facts`` 是 ``{asset_id: {"duration_sec", "frame_count", "processing_seconds"}}``，
    只对 ``source_videos`` 里的 id 有意义；缺哪个数就当哪个数没有（1.0 遇到缺失的键
    也是这个行为——它有一整套 ``isinstance(...)`` 判断）。
    """
    run_id = str(state.get("run_id") or "")
    scope_id = str(state.get("scope_id") or "").strip()
    if not scope_id:
        return {"status": "pending", "reason": "scope_id_missing", "run_id": run_id}
    if assets is None:
        return {"status": "unavailable", "run_id": run_id, "scope_id": scope_id, "error": error}

    # 标注成 ``Sequence`` 而不是 ``list``：``list`` 不变型，而下面那条回退分支给的是
    # ``list[dict]``——按 ``list[Mapping]`` 声明会在赋值处判错。``Sequence`` 是协变的。
    rows: Sequence[Mapping[str, Any]] = [item for item in assets if isinstance(item, Mapping)]
    note = "Sentrix 资产清单"
    if not rows:
        rows = _recover_from_traces(state)
        note = (
            "当前 run 工具轨迹回退：使用已持久化的关键帧 parent_asset_id 与时间戳；未重新处理视频"
            if rows
            else "Sentrix 资产清单为空，且 run 未保存关键帧预览"
        )
    else:
        # The asset list can lag a run's persisted tool trace. Merge preview-only
        # keyframes so an already-read frame remains measurable even if its asset
        # record is missing from the current scope response.
        known_ids = {str(item.get("id")) for item in rows if item.get("id")}
        recovered = _recover_from_traces(state)
        missing = [item for item in recovered if str(item.get("id") or "") not in known_ids]
        if missing:
            rows = [*rows, *missing]
            note = f"Sentrix 资产清单 + {len(missing)} 条工具轨迹补充"

    by_id = {str(item.get("id")): item for item in rows if item.get("id")}
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for item in rows:
        name = str(item.get("file_name") or "").strip().lower()
        if name:
            by_name.setdefault(name, []).append(item)

    def resolve_asset(value: Any) -> Mapping[str, Any] | None:
        """1.0 ``:5674``：把一个媒体引用解析到资产行。

        依次试 ``asset_id`` / ``id`` / ``media_id`` / ``file_name`` / ``image_id`` /
        ``video_id``，每个候选先查 id 表、再查文件名表、最后按**去后缀的 stem** 查。
        那条 stem 兜底是 1.0 的真实用途：GT 里写的是 ``IMG_1234`` 而库里存的是
        ``IMG_1234.jpg``。
        """
        if isinstance(value, Mapping):
            candidates = [
                # Video candidate payloads keep ``asset_id`` on the parent video
                # and carry the actual retrieved frame in this separate field.
                # Resolve the frame first so keyframe metrics do not silently
                # treat a valid video-frame candidate as a non-keyframe video.
                value.get("source_keyframe_asset_id"),
                value.get("asset_id"),
                value.get("id"),
                value.get("media_id"),
                value.get("file_name"),
                value.get("image_id"),
                value.get("video_id"),
            ]
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text:
                continue
            if text in by_id:
                return by_id[text]
            matches = by_name.get(text.lower()) or []
            if matches:
                return matches[0]
            stem = Path(text).stem.lower()
            matches = [
                item for name, group in by_name.items() if Path(name).stem.lower() == stem for item in group
            ]
            if matches:
                return matches[0]
        return None

    keyframe_assets = [item for item in rows if _derived_kind(item) == KEYFRAME_KIND]
    keyframe_ids = {str(item.get("id")) for item in keyframe_assets if item.get("id")}
    parent_by_keyframe = {
        str(item.get("id")): _parent_asset_id(item) for item in keyframe_assets if item.get("id")
    }
    source_videos = [
        item
        for item in rows
        if item.get("media_type") == VIDEO_MEDIA_TYPE and str(item.get("id")) not in keyframe_ids
    ]
    source_video_ids = {str(item.get("id")) for item in source_videos if item.get("id")}

    # 处理阶段的三个数（1.0 ``:5707-5730``）。它们的口径与检索指标同一个 scope——
    # 只看这个相册的视频，不重跑任何媒体。
    facts = video_facts or {}
    raw_frame_count = 0
    source_duration_sec = 0.0
    processing_seconds: list[float] = []
    stage_seconds: dict[str, list[float]] = {
        name: []
        for name in (
            "decode_seconds",
            "svd_dedup_seconds",
            "katna_seconds",
            "scene_construction_seconds",
        )
    }
    for video in source_videos:
        fact = facts.get(str(video.get("id"))) or {}
        duration = fact.get("duration_sec")
        if isinstance(duration, (int, float)):
            source_duration_sec += float(duration)
        # 1.0 是 ``int(frames or 0)`` 外面套 try/except——ffprobe 在很多容器上根本
        # 不给 ``nb_frames``，缺了就是 0，不该让整条分析炸掉。
        with contextlib.suppress(TypeError, ValueError):
            raw_frame_count += int(fact.get("frame_count") or 0)
        elapsed = fact.get("processing_seconds")
        if isinstance(elapsed, (int, float)):
            processing_seconds.append(float(elapsed))
        timings = fact.get("processing_timings")
        if isinstance(timings, Mapping):
            for name, values in stage_seconds.items():
                stage_value = timings.get(name)
                if isinstance(stage_value, (int, float)):
                    values.append(float(stage_value))

    def refs(item: Mapping[str, Any], field: str) -> list[Any]:
        value = item.get(field) or []
        return list(value) if isinstance(value, list) else []

    def asset_ids(values: Sequence[Any]) -> set[str]:
        result = set()
        for value in values:
            asset = resolve_asset(value)
            if asset and asset.get("id"):
                result.add(str(asset["id"]))
        return result

    def keyframes_in(values: Sequence[Any]) -> set[str]:
        return asset_ids(values) & keyframe_ids

    def parents_in(
        values: Sequence[Any], trace_parent_hints: Mapping[str, str] | None = None
    ) -> set[str]:
        result = set()
        for asset_id in keyframes_in(values):
            parent = parent_by_keyframe.get(asset_id) or (trace_parent_hints or {}).get(asset_id)
            if parent:
                result.add(parent)
        return result

    def observed_keyframe_refs(item: Mapping[str, Any]) -> list[dict[str, str]]:
        """Collect distinct video frames actually inspected during each QA item.

        Video QA records the visual inputs under ``tool_trace[].observation.preview``.
        Those references are persisted even when ``retrieved_candidate_media`` is empty;
        the caller unions them with explicit candidates and deduplicates by asset id.
        """
        refs_seen: dict[str, dict[str, str]] = {}
        for trace in item.get("tool_trace") or []:
            if not isinstance(trace, Mapping):
                continue
            # Persisted run traces record search_memories hits as result.asset_ids.
            # These are the actual retrieved candidates even when no observation
            # preview was saved. Non-keyframe assets are filtered later by
            # keyframes_in(), which resolves ids against the current scope.
            result = trace.get("result")
            if isinstance(result, Mapping):
                for value in result.get("asset_ids") or []:
                    asset_id = str(value or "").strip()
                    if asset_id:
                        refs_seen[asset_id] = {
                            "asset_id": asset_id,
                            "parent_asset_id": "",
                        }
            observation = trace.get("observation")
            if not isinstance(observation, Mapping):
                continue
            for preview in observation.get("preview") or []:
                if not isinstance(preview, Mapping) or preview.get("media_kind") != KEYFRAME_KIND:
                    continue
                asset_id = str(preview.get("asset_id") or preview.get("id") or "").strip()
                if asset_id:
                    refs_seen[asset_id] = {
                        "asset_id": asset_id,
                        "parent_asset_id": str(preview.get("source_video_asset_id") or ""),
                    }
        return list(refs_seen.values())

    items = state.get("items") or []
    candidate_total = candidate_relevant = predicted_total = predicted_relevant = 0
    gt_video_targets = gt_video_hits = 0
    candidate_items = candidate_hit_items = predicted_items = 0
    quality_with: list[float] = []
    quality_without: list[float] = []
    observed_event_ids: set[str] = set()
    observed_scene_ids: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        gt_ids = asset_ids(refs(item, "gt_media"))
        gt_video_ids = {asset_id for asset_id in gt_ids if asset_id in source_video_ids}
        candidate_values = refs(item, "retrieved_candidate_media") or refs(
            item, "retrieved_candidate_images"
        )
        # A video QA can expose the frames actually read only in tool previews. Union
        # these with the explicit candidate list and let keyframes_in() dedupe by id.
        trace_keyframes = observed_keyframe_refs(item)
        trace_parent_hints = {
            str(preview["asset_id"]): str(preview["parent_asset_id"])
            for preview in trace_keyframes
            if preview.get("parent_asset_id")
        }
        candidate_values = [*candidate_values, *trace_keyframes]
        predicted_values = refs(item, "predicted_media") or refs(item, "predicted_images")
        candidate_kf = keyframes_in(candidate_values)
        predicted_kf = keyframes_in(predicted_values)
        candidate_parents = parents_in(candidate_values, trace_parent_hints)
        candidate_total += len(candidate_kf)
        candidate_relevant += sum(
            1 for asset_id in candidate_kf if parent_by_keyframe.get(asset_id) in gt_video_ids
        )
        predicted_total += len(predicted_kf)
        predicted_relevant += sum(
            1 for asset_id in predicted_kf if parent_by_keyframe.get(asset_id) in gt_video_ids
        )
        if gt_video_ids:
            gt_video_targets += len(gt_video_ids)
            gt_video_hits += len(gt_video_ids & candidate_parents)
        if candidate_kf:
            candidate_items += 1
            if gt_video_ids & candidate_parents:
                candidate_hit_items += 1
        if predicted_kf:
            predicted_items += 1
        score = judge_score_for_summary(item.get("judge"))
        if score in {0, 1, 2}:
            (quality_with if candidate_kf else quality_without).append(float(score))
        for trace in item.get("tool_trace") or []:
            if not isinstance(trace, Mapping):
                continue
            observation = trace.get("observation")
            if not isinstance(observation, Mapping):
                continue
            for preview in observation.get("preview") or []:
                if not isinstance(preview, Mapping):
                    continue
                event = preview.get("event_context")
                event_id = (event.get("id") if isinstance(event, Mapping) else None) or preview.get(
                    "event_id"
                )
                if event_id:
                    observed_event_ids.add(str(event_id))
                scene_id = preview.get("source_scene_index")
                if scene_id is not None:
                    observed_scene_ids.add(str(scene_id))

    timestamps_by_video: dict[str, list[float]] = {}
    selection_reasons: dict[str, int] = {}
    worldmm_scores: list[float] = []
    for asset in keyframe_assets:
        parent = parent_by_keyframe.get(str(asset.get("id")))
        if parent:
            stamp = _metadata(asset).get("source_timestamp_sec", asset.get("source_timestamp_sec"))
            if isinstance(stamp, (int, float)):
                timestamps_by_video.setdefault(parent, []).append(float(stamp))
        reason = (
            _metadata(asset).get("worldmm_selection_reason")
            or _metadata(asset).get("keyframe_selection_reason")
            or "未记录"
        )
        selection_reasons[str(reason)] = selection_reasons.get(str(reason), 0) + 1
        score = _metadata(asset).get("worldmm_score")
        if isinstance(score, (int, float)):
            worldmm_scores.append(float(score))

    gaps: list[float] = []
    spans: list[float] = []
    for values in timestamps_by_video.values():
        values.sort()
        if len(values) > 1:
            gaps.extend(right - left for left, right in zip(values, values[1:], strict=False))
            spans.append(values[-1] - values[0])

    with_keyframe = _mean(quality_with)
    without_keyframe = _mean(quality_without)
    return {
        "status": "ready",
        "run_id": run_id,
        "scope_id": scope_id,
        "asset_source_note": note,
        "metric_scope": "full_run_items_and_sentrix_scope",
        "assets": {
            "total": len(rows),
            "source_videos": len(source_videos),
            "keyframes": len(keyframe_assets),
            "videos_with_keyframes": len({parent for parent in parent_by_keyframe.values() if parent}),
            "keyframes_per_video_mean": _rate(len(keyframe_assets), len(source_videos)),
        },
        "processing": {
            "source_duration_sec": round(source_duration_sec, 4),
            "raw_frame_count": raw_frame_count,
            "processing_seconds_total": round(sum(processing_seconds), 4),
            "processing_seconds_mean": _mean(processing_seconds),
            "realtime_factor": (
                round(source_duration_sec / sum(processing_seconds), 4)
                if processing_seconds and source_duration_sec
                else None
            ),
            "retained_frame_ratio": _rate(len(keyframe_assets), raw_frame_count),
            "compression_ratio": (
                round(1 - len(keyframe_assets) / raw_frame_count, 4) if raw_frame_count else None
            ),
            "timing_source": TIMING_SOURCE,
            "stage_timings": {
                name: {
                    "total_seconds": round(sum(values), 3),
                    "mean_seconds": _mean(values),
                    "videos_measured": len(values),
                }
                for name, values in stage_seconds.items()
            },
        },
        "retrieval": {
            "candidate_keyframes": candidate_total,
            "candidate_relevant_keyframes": candidate_relevant,
            "candidate_precision": _rate(candidate_relevant, candidate_total),
            "video_parent_recall": _rate(gt_video_hits, gt_video_targets),
            "video_gt_targets": gt_video_targets,
            "video_gt_targets_hit_by_keyframe": gt_video_hits,
            "candidate_items": candidate_items,
            "candidate_hit_items": candidate_hit_items,
            "candidate_item_hit_rate": _rate(candidate_hit_items, candidate_items),
            "predicted_keyframes": predicted_total,
            "predicted_relevant_keyframes": predicted_relevant,
            "predicted_precision": _rate(predicted_relevant, predicted_total),
            "predicted_items": predicted_items,
        },
        "temporal": {
            "videos_sampled": len(timestamps_by_video),
            "scene_count": len(observed_scene_ids),
            "mean_gap_sec": _mean(gaps),
            "mean_span_sec": _mean(spans),
            "worldmm_score_mean": _mean(worldmm_scores),
            "selection_reasons": selection_reasons,
        },
        "usefulness": {
            "qa_with_keyframe": len(quality_with),
            "qa_without_keyframe": len(quality_without),
            "answer_quality_with_keyframe": with_keyframe,
            "answer_quality_without_keyframe": without_keyframe,
            "answer_quality_delta": (
                round(with_keyframe - without_keyframe, 4)
                if with_keyframe is not None and without_keyframe is not None
                else None
            ),
            "event_context_count": len(observed_event_ids),
        },
        "definitions": {
            "candidate_precision": "检索候选与工具轨迹实际读取的去重关键帧中，其 parent video 与本题 GT 视频一致的比例",
            "video_parent_recall": "GT 源视频中，被检索候选或工具轨迹实际读取关键帧指向的比例",
            "answer_quality_delta": "带关键帧候选题与不带关键帧候选题的 Judge 均分差；不是因果结论",
            "temporal": "根据关键帧 source_timestamp_sec 统计采样间隔和覆盖跨度",
        },
    }
