"""记忆有效性：这一轮评测里，记忆层造出来的东西有多少真的被用上了。

1.0 ``OrchestratorRepository.get_memory_effectiveness``（``benchmark_orchestrator.py:5889-6445``）
逐行移植。**1.0 返回的是算好的表格，不是原始数据**，2.0 同样把计算留在后端。

比较的基准是**已持久化的工具轨迹与 Agent2 证据账本**，不去猜模型没保存的思维链——
这是 1.0 docstring 里写明的前提，2.0 一字不改。

**数据的来路有一处形状偏离**，与 ``keyframe_analysis`` 同一条：血缘（关键帧 →
父视频）来自 ``/api/assets``，1.0 是从 ``SELECT *`` 的整行里翻的。已登记在
``s10-deviations.md``。

几处**刻意的口径**，移植时容易当成 bug 改掉，所以在这里说明白：

1. **命中分两个维度**：``tool``（GT 媒体进了检索/工具返回集合）与 ``usage``
   （模型显式用于回答）。这是 1.0 的明确决策，注释写在 ``:5985-5990``。
2. **视频 GT 按「关键帧父级覆盖」判命中**：返回了该视频的任一关键帧就算召回了这段
   视频，与关键帧分析同一口径。
3. **``levels`` 不是正确率表**。它比的是「造出来的量」与「被用上的量」，所以 0 分题
   另有一张互斥的 ``error_chain_diagnosis`` 主表（``primary_rows`` 合计全部 0 分题），
   而 ``signals`` 与 ``attribution_primary`` 是**交叉诊断，不能与主表相加**——
   这两句在 1.0 的 ``note`` 里写着，照搬。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.contract import evaluation_summary
from photobench.domain.runs.analysis import judge_bucket_for_summary

#: 判关键帧的字面量，与 ``keyframe_analysis`` 同一个（1.0 ``:6014``）。
KEYFRAME_KIND = "video_keyframe"

#: 1.0 ``:5966`` 判「工具调用成功」的那一组状态词，一个不多一个不少。
_TOOL_OK_STATUS = {"ok", "success", "completed"}


def _value_id(value: Any) -> str | None:
    """1.0 ``:5911``：把一个媒体引用折成一个可比对的字符串 id。"""
    if isinstance(value, Mapping):
        value = (
            value.get("asset_id")
            or value.get("id")
            or value.get("media_id")
            or value.get("file_name")
            or value.get("image_id")
            or value.get("video_id")
        )
    text = str(value or "").strip()
    return text or None


def _collect(values: Any, target: set[str]) -> None:
    """1.0 ``:5919``：dict 与 list 两种形状都收。"""
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return
    for value in values:
        item_id = _value_id(value)
        if item_id:
            target.add(item_id)


def _tokens(values: Sequence[Any] | None) -> tuple[list[str], set[str]]:
    """1.0 ``:6021`` 的 ``_tokens``：拆一条媒体引用为 ``(asset_ids, name/raw tokens)``。"""
    ids: list[str] = []
    tokens: set[str] = set()
    for value in values or []:
        if not isinstance(value, Mapping):
            tokens.add(str(value).strip().lower())
            continue
        aid = str(value.get("asset_id") or value.get("id") or "").strip()
        if aid:
            ids.append(aid)
        for key in ("media_id", "file_name", "image_id", "video_id", "asset_id", "id"):
            raw = value.get(key)
            if raw:
                tokens.add(str(raw).strip().lower())
    return ids, tokens


def _row(
    key: str,
    name: str,
    total: int,
    effective: int,
    created_detail: str,
    effective_detail: str,
    invalid_detail: str,
) -> dict[str, Any]:
    """1.0 ``:6153`` 的 ``row()``：记忆层级表的一行。"""
    total = int(total or 0)
    effective = int(effective or 0)
    return {
        "key": key,
        "name": name,
        "created": total,
        "effective": effective,
        "ineffective": max(0, total - effective),
        "rate": round(effective / total, 4) if total else None,
        "created_detail": created_detail,
        "effective_detail": effective_detail,
        "ineffective_detail": invalid_detail,
    }


def _diagnosis_row(key: str, name: str, count: int, state: str, improvement: str) -> dict[str, Any]:
    """1.0 ``:6352`` 的 ``diagnosis_row()``。``wrong_total`` 由调用方补 ``rate``。"""
    return {"key": key, "name": name, "count": int(count), "state": state, "improvement": improvement}


def memory_effectiveness(
    state: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """一个 run 的记忆有效性。

    ``assets`` 与 ``keyframe_analysis`` 同义：``None`` 表示取资产清单失败。
    但**这里取不到不是错误**——1.0 在 ``:6005`` 用 ``except Exception: assets = []``
    把失败吞掉，让命中匹配回落到 ``media_id`` / ``file_name`` 直接比对。
    所以这个函数对 ``None`` 与 ``[]`` 一视同仁，只是 ``data_quality.hit_match_source``
    会说明用的是哪一级。
    """
    items = [item for item in (state.get("items") or []) if isinstance(item, Mapping)]
    integrity = state.get("input_integrity") or {}
    summary = state.get("summary") or {}
    agent2 = (summary.get("agent2_trace") or {}) if isinstance(summary, Mapping) else {}
    requirement_counts = agent2.get("requirement_status_counts") or {}

    tool_counts: dict[str, int] = {}
    tool_ok: dict[str, int] = {}
    preview_rows: list[Mapping[str, Any]] = []
    preview_asset_ids: set[str] = set()
    final_evidence_asset_ids: set[str] = set()

    for item in items:
        _collect(item.get("evidence_asset_ids"), final_evidence_asset_ids)
        _collect(item.get("evidence_source_media"), final_evidence_asset_ids)
        _collect(item.get("answer_evidence_media_refs"), final_evidence_asset_ids)
        for trace in item.get("tool_trace") or []:
            if not isinstance(trace, Mapping):
                continue
            name = str(trace.get("tool") or trace.get("tool_name") or trace.get("name") or "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1
            if str(trace.get("status") or "").lower() in _TOOL_OK_STATUS:
                tool_ok[name] = tool_ok.get(name, 0) + 1
            observation = trace.get("observation")
            if not isinstance(observation, Mapping):
                continue
            for preview in observation.get("preview") or []:
                if not isinstance(preview, Mapping):
                    continue
                preview_rows.append(preview)
                item_id = _value_id(preview)
                if item_id:
                    preview_asset_ids.add(item_id)

    event_ids: set[str] = set()
    for preview in preview_rows:
        event = preview.get("event_context")
        event_id = (event.get("id") if isinstance(event, Mapping) else None) or preview.get("event_id")
        if event_id:
            event_ids.add(str(event_id))

    visual_total = int(tool_counts.get("inspect_photo", 0))
    visual_effective = int(tool_ok.get("inspect_photo", 0))
    ocr_total = int(tool_counts.get("read_photo_text", 0))
    ocr_effective = int(tool_ok.get("read_photo_text", 0))
    scene_total = len(
        {
            str(preview.get("source_scene_index"))
            for preview in preview_rows
            if preview.get("source_scene_index") is not None
        }
    )
    event_total = len(event_ids)
    requirement_total = sum(int(value or 0) for value in requirement_counts.values())
    requirement_effective = int(requirement_counts.get("satisfied", 0) or 0)
    answer_total = len(items)
    answer_effective = sum(1 for item in items if str(item.get("agent_status") or "").lower() == "complete")
    judge_counts = {"2": 0, "1": 0, "0": 0, "unjudged": 0}
    for item in items:
        score = judge_bucket_for_summary(item.get("judge"))
        if score in {0, 1, 2}:
            judge_counts[str(score)] += 1
        else:
            judge_counts["unjudged"] += 1

    scope_id = str(state.get("scope_id") or "").strip()
    rows = [item for item in (assets or []) if isinstance(item, Mapping)]
    parent_of_kf: dict[str, str] = {}
    children_of_video: dict[str, set[str]] = {}
    asset_source_note = ""
    if rows:
        for asset in rows:
            aid = str(asset.get("id") or "")
            if not aid:
                continue
            meta = asset.get("metadata_json")
            meta = meta if isinstance(meta, Mapping) else {}
            kind = str(asset.get("derived_kind") or meta.get("derived_kind") or "")
            parent = str(asset.get("parent_asset_id") or meta.get("parent_asset_id") or "")
            if kind == KEYFRAME_KIND and parent:
                parent_of_kf[aid] = parent
                children_of_video.setdefault(parent, set()).add(aid)
        asset_source_note = f"使用 scope 资产清单 {len(rows)} 条（含关键帧父级）做命中匹配"

    hit_cache: dict[int, tuple[bool, bool, bool, list[str], set[str]]] = {}

    def coverage(ids: Sequence[str]) -> set[str]:
        """1.0 ``:6038``：asset 及其可覆盖集合——关键帧→父视频；视频→其全部关键帧。"""
        out: set[str] = set()
        for aid in ids:
            if not aid:
                continue
            out.add(aid)
            parent = parent_of_kf.get(aid)
            if parent:
                out.add(parent)
            out.update(children_of_video.get(aid, ()))
        return out

    def matched(
        gt_ids: Sequence[str], gt_tokens: set[str], cand_ids: Sequence[str], cand_tokens: set[str]
    ) -> bool:
        if coverage(gt_ids) & coverage(cand_ids):
            return True
        return bool(gt_tokens & cand_tokens)

    def preview_refs(item: Mapping[str, Any]) -> tuple[list[str], set[str]]:
        """1.0 ``:6056``：工具轨迹的**回退**引用来源。

        关键帧预览同时携带源视频，所以「视频 GT 被返回了它的任一帧」在这里才成立。
        """
        ids: list[str] = []
        tokens: set[str] = set()
        for trace in item.get("tool_trace") or []:
            if not isinstance(trace, Mapping):
                continue
            observation = trace.get("observation")
            if not isinstance(observation, Mapping):
                continue
            for preview in observation.get("preview") or []:
                if not isinstance(preview, Mapping):
                    continue
                aid = str(preview.get("asset_id") or preview.get("id") or "").strip()
                if aid:
                    ids.append(aid)
                source = str(preview.get("source_video_asset_id") or "").strip()
                if source:
                    ids.append(source)
                for key in ("media_id", "file_name", "asset_id", "id", "source_video_file_name"):
                    raw = preview.get(key)
                    if raw:
                        tokens.add(str(raw).strip().lower())
        return ids, tokens

    def item_hits(item: Mapping[str, Any]) -> tuple[bool, bool, bool, list[str], set[str]]:
        """1.0 ``:6074``：``(有 GT, 工具召回命中, 模型使用命中, gt_ids, gt_tokens)``。"""
        cached = hit_cache.get(id(item))
        if cached is not None:
            return cached
        gt_ids, gt_tokens = _tokens(item.get("gt_media"))
        has_gt = bool(gt_ids or gt_tokens)
        r_ids, r_tokens = _tokens(
            item.get("retrieved_candidate_media") or item.get("retrieved_candidate_images") or []
        )
        p_ids, p_tokens = _tokens(item.get("predicted_media") or item.get("predicted_images") or [])
        if not r_ids and not r_tokens:
            r_ids, r_tokens = preview_refs(item)
        tool_hit = has_gt and matched(gt_ids, gt_tokens, r_ids, r_tokens)
        usage_hit = has_gt and matched(gt_ids, gt_tokens, p_ids, p_tokens)
        result = (has_gt, tool_hit, usage_hit, gt_ids, gt_tokens)
        hit_cache[id(item)] = result
        return result

    def causal_scope(dim: str, include_unanswerable: bool) -> dict[str, Any]:
        """1.0 ``:6087``：命中 × Judge 分的 2×3 矩阵。"""
        matrix = {
            "hit_correct": 0,
            "hit_partial": 0,
            "hit_incorrect": 0,
            "miss_correct": 0,
            "miss_partial": 0,
            "miss_incorrect": 0,
            "unjudged": 0,
        }
        eligible = 0
        hit_items = 0
        for item in items:
            if (
                not include_unanswerable
                and str(item.get("answerability") or "").strip().lower() == "unanswerable"
            ):
                continue
            has_gt, tool_hit, usage_hit, _, _ = item_hits(item)
            if not has_gt:
                continue
            hit = tool_hit if dim == "tool" else usage_hit
            eligible += 1
            if hit:
                hit_items += 1
            score = judge_bucket_for_summary(item.get("judge"))
            if score is None:
                matrix["unjudged"] += 1
                continue
            prefix = "hit" if hit else "miss"
            suffix = "correct" if score == 2 else "incorrect" if score == 0 else "partial"
            matrix[f"{prefix}_{suffix}"] += 1
        return {
            "eligible": eligible,
            "hit_items": hit_items,
            "miss_items": max(0, eligible - hit_items),
            "matrix": matrix,
        }

    raw_total = int(integrity.get("files_checked") or 0)
    if not raw_total:
        raw_total = len(
            {
                value_id
                for item in items
                for ref in (item.get("gt_media") or [])
                if (value_id := _value_id(ref))
            }
        )

    levels = [
        _row(
            "L0",
            "原始媒体",
            raw_total,
            min(len(preview_asset_ids), raw_total) if raw_total else len(preview_asset_ids),
            f"输入完整性校验 {raw_total} 个文件",
            f"成功工具结果实际返回 {len(preview_asset_ids)} 个不同资产；"
            f"最终证据链 {len(final_evidence_asset_ids)} 个资产",
            f"未在工具结果中出现 {max(0, raw_total - len(preview_asset_ids))} 个；"
            f"仅返回但未进入最终证据 "
            f"{max(0, len(preview_asset_ids) - len(final_evidence_asset_ids))} 个",
        ),
        _row(
            "L1",
            "场景图 / 解析",
            visual_total + ocr_total + scene_total,
            visual_effective + ocr_effective + scene_total,
            f"视觉观察 {visual_total} + OCR {ocr_total} + 场景节点 {scene_total}",
            f"成功视觉 {visual_effective} + 成功 OCR {ocr_effective} + 已返回场景节点 {scene_total}",
            f"工具拒绝/失败：视觉 {visual_total - visual_effective} + OCR {ocr_total - ocr_effective}",
        ),
        _row(
            "L2",
            "事件 / 记忆",
            event_total,
            event_total,
            f"工具图谱中发现 {event_total} 个事件上下文",
            f"{event_total} 个事件均在评测工具预览中出现",
            "当前轨迹未发现未生效事件；若图谱接口未加载则本项不应判定",
        ),
        _row(
            "L3",
            "目标 / 证据规则",
            requirement_total,
            requirement_effective,
            f"证据账本共 {requirement_total} 项需求",
            f"满足 {requirement_effective} 项（satisfied）",
            f"未闭合 {max(0, requirement_total - requirement_effective)} 项（open / running / failed）",
        ),
        _row(
            "L4+",
            "总结 / 回答",
            answer_total,
            answer_effective,
            f"评测生成 {answer_total} 条回答",
            f"Agent 状态 complete {answer_effective} 条",
            f"未完整闭环 {max(0, answer_total - answer_effective)} 条（partial / error / blocked）",
        ),
    ]
    levels[0]["ineffective_reasons"] = [
        {
            "label": "未被成功工具返回",
            "count": max(0, raw_total - len(preview_asset_ids)),
            "meaning": "创建输入中没有在检索预览出现",
        },
        {
            "label": "返回但未进入最终证据",
            "count": max(0, len(preview_asset_ids) - len(final_evidence_asset_ids)),
            "meaning": "工具找到，但没有形成回答证据",
        },
    ]
    levels[1]["ineffective_reasons"] = [
        {
            "label": "视觉工具拒绝/失败",
            "count": max(0, visual_total - visual_effective),
            "meaning": "inspect_photo 没有成功返回",
        },
        {
            "label": "OCR 工具拒绝/失败",
            "count": max(0, ocr_total - ocr_effective),
            "meaning": "read_photo_text 没有成功返回",
        },
    ]
    levels[2]["ineffective_reasons"] = [
        {
            "label": "未发现未生效事件",
            "count": 0,
            "meaning": "当前轨迹中的事件上下文均至少出现过一次",
        },
    ]
    levels[3]["ineffective_reasons"] = [
        {
            "label": "open",
            "count": int(requirement_counts.get("open", 0) or 0),
            "meaning": "需求没有完成证据闭合",
        },
        {
            "label": "running",
            "count": int(requirement_counts.get("running", 0) or 0),
            "meaning": "需求仍停留在处理中",
        },
        {
            "label": "failed",
            "count": int(requirement_counts.get("failed", 0) or 0),
            "meaning": "需求证据处理失败",
        },
    ]
    levels[4]["ineffective_reasons"] = [
        {
            "label": "partial",
            "count": sum(1 for item in items if str(item.get("agent_status") or "").lower() == "partial"),
            "meaning": "只完成部分回答链路",
        },
        {
            "label": "error",
            "count": sum(1 for item in items if str(item.get("agent_status") or "").lower() == "error"),
            "meaning": "执行或接口错误",
        },
        {
            "label": "blocked",
            "count": sum(
                1 for item in items if str(item.get("agent_status") or "").lower() == "blocked_by_guard"
            ),
            "meaning": "被安全/状态守卫中断",
        },
    ]

    wrong_ids = {id(item) for item in items if judge_bucket_for_summary(item.get("judge")) == 0}
    wrong_total = len(wrong_ids)
    memory_miss_wrong = 0
    memory_hit_wrong = 0
    tool_failure_items = 0
    evidence_not_closed_items = 0
    execution_error_items = 0
    attribution_counts: dict[str, int] = {}
    # 0 分错误链诊断固定用「工具召回命中」维度：只有检索未到达时才谈得上「先修检索」。
    tool_scope = causal_scope("tool", False)
    tool_matrix = tool_scope.get("matrix") or {}
    hit_total = int(tool_scope["hit_items"])
    hit_correct = int(tool_matrix.get("hit_correct") or 0)
    hit_partial = int(tool_matrix.get("hit_partial") or 0)
    hit_incorrect = int(tool_matrix.get("hit_incorrect") or 0)

    def has_failed_tool(item: Mapping[str, Any]) -> bool:
        return any(
            isinstance(trace, Mapping)
            and str(trace.get("status") or "").lower() not in {"ok", "complete", "completed", "success"}
            for trace in (item.get("tool_trace") or [])
        )

    def has_open_requirement(item: Mapping[str, Any]) -> bool:
        trace = item.get("agent2_trace") or {}
        requirements = (
            ((trace.get("task_state") or {}).get("requirements") or []) if isinstance(trace, Mapping) else []
        )
        return any(
            isinstance(req, Mapping) and str(req.get("status") or "").lower() != "satisfied"
            for req in requirements
        )

    for item in items:
        if id(item) not in wrong_ids:
            continue
        has_gt, tool_hit, _, _, _ = item_hits(item)
        if has_gt and tool_hit:
            memory_hit_wrong += 1
        else:
            memory_miss_wrong += 1
        if has_failed_tool(item):
            tool_failure_items += 1
        if has_open_requirement(item):
            evidence_not_closed_items += 1
        if str(item.get("agent_status") or "").lower() in {"error", "blocked", "blocked_by_guard"}:
            execution_error_items += 1
        attribution = item.get("attribution")
        primary = str(
            (attribution.get("primary") if isinstance(attribution, Mapping) else None) or "unknown"
        ).upper()
        attribution_counts[primary] = attribution_counts.get(primary, 0) + 1

    def with_rate(row: dict[str, Any]) -> dict[str, Any]:
        row["rate"] = round(row["count"] / wrong_total, 4) if wrong_total else None
        return row

    error_chain_diagnosis = {
        "wrong_total": wrong_total,
        "primary_rows": [
            with_rate(
                _diagnosis_row(
                    "memory_miss_wrong",
                    "工具召回未命中 → 回答错误",
                    memory_miss_wrong,
                    "GT 媒体未进入本题检索候选（工具召回未命中），属于记忆到达之前的损失",
                    "优先修复检索候选、时间/地点/人物字段、事件图谱关联与重排",
                )
            ),
            with_rate(
                _diagnosis_row(
                    "memory_hit_wrong",
                    "工具已召回 → 回答仍错误",
                    memory_hit_wrong,
                    "GT 媒体已被工具召回，但 Judge 仍为 0 分，不能再归因于‘没找到记忆’",
                    "优先检查证据选择、上下文组织、Agent 推理和最终回答生成",
                )
            ),
        ],
        "signals": [
            with_rate(
                _diagnosis_row(
                    "tool_failure",
                    "工具拒绝 / 执行异常（交叉信号）",
                    tool_failure_items,
                    "本题至少有一次工具状态不是成功；与主诊断可能重叠，不参与相加",
                    "保留失败原因、重试结果和输入输出，区分接口失败与检索质量问题",
                )
            ),
            with_rate(
                _diagnosis_row(
                    "evidence_not_closed",
                    "证据需求未闭合（交叉信号）",
                    evidence_not_closed_items,
                    "Agent2 仍有 open / running / failed 需求；不等价于回答错误原因",
                    "补齐需求到 evidence_ref 的闭合链路，再单独观察回答正确率变化",
                )
            ),
            with_rate(
                _diagnosis_row(
                    "execution_error",
                    "Agent 执行错误 / 守卫中断（交叉信号）",
                    execution_error_items,
                    "agent_status 为 error 或 blocked；与检索/回答错误可能同时出现",
                    "先隔离执行异常，避免把基础设施失败误判为记忆或模型能力问题",
                )
            ),
        ],
        "attribution_primary": [
            {
                "key": key,
                "count": count,
                "rate": round(count / wrong_total, 4) if wrong_total else None,
            }
            for key, count in sorted(attribution_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "opportunity": {
            "retrieval_error_share": round(memory_miss_wrong / wrong_total, 4) if wrong_total else None,
            "post_retrieval_wrong_rate": round(hit_incorrect / hit_total, 4) if hit_total else None,
            "hit_correct_rate": round(hit_correct / hit_total, 4) if hit_total else None,
            "hit_total": hit_total,
            "hit_correct": hit_correct,
            "hit_partial": hit_partial,
            "hit_incorrect": hit_incorrect,
            "meaning": "retrieval_error_share=0 分中工具召回未命中的比例，应先修检索；"
            "post_retrieval_wrong_rate / hit_correct_rate 表示工具已召回后回答链路仍有多大提升空间",
        },
        "note": "primary_rows 互斥合计全部 0 分题；命中依据=工具召回命中"
        "（GT 进入检索候选，视频按关键帧父级覆盖）。"
        "signals 与 attribution_primary 是交叉诊断，不能与主表相加。",
    }

    def evidence_effectiveness_row(dim: str, key: str, name: str, media_type: str) -> dict[str, Any]:
        """1.0 ``:6306``：某类 GT 命中后，按 Judge 判这份证据有没有用。"""
        candidates = []
        for item in items:
            has_gt, tool_hit, usage_hit, _, _ = item_hits(item)
            if not has_gt:
                continue
            hit = tool_hit if dim == "tool" else usage_hit
            if not hit:
                continue
            has_type = any(
                isinstance(ref, Mapping)
                and (
                    ref.get("media_type") == media_type
                    or (media_type == "video_keyframe" and ref.get("media_type") == "video")
                )
                for ref in (item.get("gt_media") or [])
            )
            if has_type:
                candidates.append(item)
        returned = len(candidates)
        effective = 0
        ineffective = 0
        unjudged = 0
        for item in candidates:
            score = judge_bucket_for_summary(item.get("judge"))
            if score in {1, 2}:
                effective += 1
            elif score == 0:
                ineffective += 1
            else:
                unjudged += 1
        return {
            "key": key,
            "name": name,
            "returned_items": returned,
            "effective_items": effective,
            "ineffective_items": ineffective,
            "unjudged_items": unjudged,
            "effective_rate_on_returned": round(effective / returned, 4) if returned else None,
            "ineffective_rate_on_returned": round(ineffective / returned, 4) if returned else None,
            "definition": "该类型 GT 命中后：Judge=2 或 1 计有效，Judge=0 计无效；未命中不进入本表",
        }

    def event_effectiveness_row() -> dict[str, Any]:
        candidates = []
        for item in items:
            linked = any(
                ((preview.get("event_context") or {}).get("id") or preview.get("event_id"))
                for trace in (item.get("tool_trace") or [])
                if isinstance(trace, Mapping)
                for preview in (
                    (trace.get("observation") or {}).get("preview") or []
                    if isinstance(trace.get("observation"), Mapping)
                    else []
                )
                if isinstance(preview, Mapping)
            )
            if linked:
                candidates.append(item)
        effective = 0
        ineffective = 0
        unjudged = 0
        for item in candidates:
            score = judge_bucket_for_summary(item.get("judge"))
            if score in {1, 2}:
                effective += 1
            elif score == 0:
                ineffective += 1
            else:
                unjudged += 1
        return {
            "key": "event",
            "name": "事件上下文证据",
            "returned_items": len(candidates),
            "effective_items": effective,
            "ineffective_items": ineffective,
            "unjudged_items": unjudged,
            "effective_rate_on_returned": round(effective / len(candidates), 4) if candidates else None,
            "ineffective_rate_on_returned": round(ineffective / len(candidates), 4) if candidates else None,
            "definition": "工具预览带回事件上下文的题；Judge=2 或 1 计有效，Judge=0 计无效",
        }

    evidence_effectiveness = {
        "tool": {
            "image": evidence_effectiveness_row("tool", "image", "原图片证据", "image"),
            "video_keyframe": evidence_effectiveness_row(
                "tool", "video_keyframe", "视频 / 关键帧证据", "video"
            ),
        },
        "usage": {
            "image": evidence_effectiveness_row("usage", "image", "原图片证据", "image"),
            "video_keyframe": evidence_effectiveness_row(
                "usage", "video_keyframe", "视频 / 关键帧证据", "video"
            ),
        },
        "event": event_effectiveness_row(),
        "note": "tool=工具召回命中（视频按关键帧父级覆盖）后按 Judge 判有效/无效；"
        "usage=模型显式用于回答后判有效/无效。Judge=1 计有效。"
        "该指标是证据结果有效性，不是记忆库覆盖率。",
    }

    total_trace = sum(tool_counts.values())
    ok_trace = sum(tool_ok.values())
    graph_ready = bool(total_trace and preview_rows and event_ids)
    return {
        "status": "ready",
        "run_id": str(state.get("run_id") or ""),
        "scope_id": state.get("scope_id"),
        "metric_scope": "full_run_persisted_tool_traces_and_agent2_ledger",
        "data_quality": {
            "graph_ready": graph_ready,
            "tool_trace_count": total_trace,
            "tool_success_count": ok_trace,
            "preview_count": len(preview_rows),
            "event_context_count": len(event_ids),
            "note": "仅在工具轨迹、媒体预览和事件上下文均存在时计算有效/无效对比；"
            "不使用未保存的模型隐藏思维链",
            "hit_match_source": asset_source_note
            or (
                f"scope {scope_id!r} 无资产清单：命中匹配回退到 media_id/file_name 直接比对与工具预览"
                if scope_id
                else "run 未记录 scope：命中匹配仅用字段直接比对"
            ),
        },
        "levels": levels,
        "details": {
            "raw_media_returned_assets": len(preview_asset_ids),
            "raw_media_final_evidence_assets": len(final_evidence_asset_ids),
            "visual_calls": {"total": visual_total, "effective": visual_effective},
            "ocr_calls": {"total": ocr_total, "effective": ocr_effective},
            "requirement_status_counts": requirement_counts,
            "answer_status_complete": answer_effective,
            "answer_correctness": {
                **(
                    {"evaluation_summary": evaluation_summary(items)}
                    if any(item.get("evaluation") for item in items)
                    else {}
                ),
                "correct": judge_counts["2"],
                "partial": judge_counts["1"],
                "incorrect": judge_counts["0"],
                "unjudged": judge_counts["unjudged"],
                "exact_accuracy": round(judge_counts["2"] / answer_total, 4) if answer_total else None,
            },
            "memory_answer_attribution": {
                "tool": causal_scope("tool", False),
                "usage": causal_scope("usage", False),
                "tool_all_with_gt": causal_scope("tool", True),
                "usage_all_with_gt": causal_scope("usage", True),
                "definitions": {
                    "tool": "工具召回命中：该题 GT 媒体进入检索 / 工具返回集合（视频按关键帧父级覆盖）",
                    "usage": "模型使用命中：该题 GT 媒体被模型显式用于回答"
                    "（严格交付口径，与 psh 逐题指标一致）",
                    "hit_correct": "命中且 Judge 2 分",
                    "hit_partial": "命中且 Judge 1 分",
                    "hit_incorrect": "命中但 Judge 0 分：媒体已到达，问题更可能在使用 / 推理 / 回答",
                    "miss_correct": "未命中但 Judge 2 分：不能归因于媒体召回 / 使用",
                    "miss_incorrect": "未命中且 Judge 0 分：优先排查媒体检索 / 使用链路",
                    "scope": "tool / usage 各维度都排除 answerability=unanswerable 的题；"
                    "*_all_with_gt 仅用于完整对照",
                },
            },
            "error_chain_diagnosis": error_chain_diagnosis,
            "evidence_effectiveness": evidence_effectiveness,
        },
    }
