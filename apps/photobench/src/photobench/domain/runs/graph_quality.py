"""图记忆评测的纯计算。

这份模块只消费 run.json、PhotoBench 的 GT 和主 API 的只读投影，不把评测标注带进
Sentrix 运行时。旧版 ``benchmark_orchestrator`` 的三块图指标原来混在编排器里，2.0
把它们拆成纯函数，方便独立测试，也方便后续替换图构建模块。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

FACE_CLUSTERING_METRIC_VERSION = 5

QUESTION_TYPE_LABELS = {
    "event_memory_qa": "事件记忆",
    "single_evidence_memory_qa": "单证据记忆",
    "multi_turn_clarify": "多轮澄清",
    "time_sequence": "时间/时序",
    "location_scene": "地点/场景",
    "visual_scene_understanding": "视觉/场景理解",
    "video_media": "视频/媒体",
    "evidence_insufficient": "证据不足",
    "prompt_injection": "提示注入",
    "multi_hop": "多跳/因果",
}


def _walk(value: object) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _graph_trace(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """找到 search_memories 的图遥测；兼容旧版 graph_rerank 轨迹。"""

    for node in _walk(item.get("tool_trace") or []):
        if "graph_primary_asset_ids" in node or "graph_rerank" in node:
            return node
        result = node.get("result")
        if isinstance(result, Mapping) and ("graph_primary_asset_ids" in result or "graph_rerank" in result):
            return {str(key): value for key, value in result.items()}
    return None


def _gt_asset_ids(item: Mapping[str, Any]) -> set[str]:
    values: list[object] = []
    for key in ("gt_media", "gt_images"):
        values.extend(item.get(key) or [])
    return {
        str(entry.get("asset_id")) for entry in values if isinstance(entry, Mapping) and entry.get("asset_id")
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _judge_score(value: object) -> float | None:
    if not isinstance(value, Mapping):
        return None
    score = value.get("score")
    return float(score) if isinstance(score, (int, float)) else None


def _has_final_media_metric(item: Mapping[str, Any]) -> bool:
    counts = item.get("media_retrieval_counts")
    return bool(
        str(item.get("answerability") or "").strip().lower() != "unanswerable"
        and "retrieval_media_refs" in item
        and isinstance(counts, Mapping)
        and int(counts.get("gt") or 0) > 0
    )


def _graph_heads(trace: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the primary (without graph promotion) and fused graph heads."""

    baseline = trace.get("graph_primary_asset_ids") or []
    final = trace.get("graph_fused_asset_ids") or []
    if not baseline and isinstance(trace.get("graph_rerank"), Mapping):
        rerank = trace["graph_rerank"]
        baseline = (
            rerank.get("baseline_head_asset_ids") or (rerank.get("baseline_ranked_asset_ids") or [])[:18]
        )
        final = rerank.get("returned_head_asset_ids") or (rerank.get("reranked_asset_ids") or [])[:18]
    # Keep the conversion here so malformed historical traces cannot break the report.
    return [str(asset_id) for asset_id in baseline], [str(asset_id) for asset_id in final]


def _retrieval_metrics(asset_ids: list[str], gt: set[str]) -> tuple[float, float]:
    """Return question-level recall and precision for one candidate head."""

    predicted = set(asset_ids)
    hits = len(predicted & gt)
    recall = hits / len(gt) if gt else 0.0
    precision = hits / len(predicted) if predicted else 0.0
    return recall, precision


def _graph_comparison(item: Mapping[str, Any], trace: Mapping[str, Any]) -> dict[str, Any] | None:
    gt = _gt_asset_ids(item)
    if not gt:
        return None
    baseline, final = _graph_heads(trace)
    baseline_recall, baseline_precision = _retrieval_metrics(baseline, gt)
    final_recall, final_precision = _retrieval_metrics(final, gt)
    return {
        "baseline_recall": baseline_recall,
        "baseline_precision": baseline_precision,
        "graph_recall": final_recall,
        "graph_precision": final_precision,
        "recall_delta": final_recall - baseline_recall,
        "precision_delta": final_precision - baseline_precision,
    }


def _final_media_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """按 run 列表页的口径计算最终交付媒体宏平均指标。

    这里故意读取 ``media_retrieval_counts``，而不是图的候选头。前者的 predicted
    是模型最终交付的媒体，正是列表里的 ``media_retrieval_recall_macro`` 来源。
    """
    rows = [
        item.get("media_retrieval_counts")
        for item in items
        if _has_final_media_metric(item)
    ]
    if not rows:
        return {"available": False, "sample_count": 0}
    values = {"precision": [], "recall": [], "f1": []}
    for row in rows:
        gt = int(row.get("gt") or 0)
        predicted = int(row.get("predicted") or 0)
        matched = int(row.get("matched") or 0)
        precision = matched / predicted if predicted else 0.0
        recall = matched / gt
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        values["precision"].append(precision)
        values["recall"].append(recall)
        values["f1"].append(f1)
    scores = [score for item in items if (score := _judge_score(item.get("judge"))) is not None]
    return {
        "available": True,
        "sample_count": len(rows),
        "media_retrieval_precision": sum(values["precision"]) / len(rows),
        "media_retrieval_recall": sum(values["recall"]) / len(rows),
        "media_retrieval_f1": sum(values["f1"]) / len(rows),
        "answer_quality_mean": sum(scores) / len(scores) if scores else None,
        "judge_valid_count": len(scores),
    }


def _paired_final_media_effect(
    graph_items: list[dict[str, Any]],
    no_graph_items: list[dict[str, Any]] | None,
    *,
    baseline_run_id: str | None = None,
    graph_run_id: str | None = None,
    graph_run_valid: bool = True,
    baseline_run_valid: bool = True,
    pairing_error: str | None = None,
) -> dict[str, Any]:
    if no_graph_items is None:
        return {
            "available": False,
            "status": "requires_paired_no_graph_run",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": "需要同相册、同题集、同模型的 graph_enabled=false 完整测评作对照",
        }
    if pairing_error or not graph_run_valid or not baseline_run_valid:
        note = pairing_error or "有图和无图 run 都必须是 completed 且 run_valid=true，才能比较最终媒体结果"
        return {
            "available": False,
            "status": "requires_complete_paired_runs",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": note,
        }
    no_graph_by_id = {str(item.get("qa_id") or ""): item for item in no_graph_items}
    paired_graph: list[dict[str, Any]] = []
    paired_no_graph: list[dict[str, Any]] = []
    for item in graph_items:
        qa_id = str(item.get("qa_id") or "")
        baseline = no_graph_by_id.get(qa_id)
        if qa_id and baseline is not None:
            paired_graph.append(item)
            paired_no_graph.append(baseline)
    graph_metrics = _final_media_metrics(paired_graph)
    no_graph_metrics = _final_media_metrics(paired_no_graph)
    if not graph_metrics.get("available") or not no_graph_metrics.get("available"):
        return {
            "available": False,
            "status": "paired_media_metrics_unavailable",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": "配对题目缺少最终媒体交付指标，不能用候选头代替",
        }
    without = {
        "media_retrieval_recall": no_graph_metrics["media_retrieval_recall"],
        "media_retrieval_precision": no_graph_metrics["media_retrieval_precision"],
        "media_retrieval_f1": no_graph_metrics["media_retrieval_f1"],
        "answer_quality_mean": no_graph_metrics["answer_quality_mean"],
    }
    with_graph = {
        "media_retrieval_recall": graph_metrics["media_retrieval_recall"],
        "media_retrieval_precision": graph_metrics["media_retrieval_precision"],
        "media_retrieval_f1": graph_metrics["media_retrieval_f1"],
        "answer_quality_mean": graph_metrics["answer_quality_mean"],
    }
    delta = {
        key: (
            with_graph[key] - without[key]
            if isinstance(with_graph[key], (int, float)) and isinstance(without[key], (int, float))
            else None
        )
        for key in (
            "media_retrieval_recall",
            "media_retrieval_precision",
            "media_retrieval_f1",
            "answer_quality_mean",
        )
    }
    return {
        "available": True,
        "status": "paired",
        "baseline_run_id": baseline_run_id,
        "with_graph_run_id": graph_run_id,
        "sample_count": sum(
            1
            for graph_item, baseline_item in zip(paired_graph, paired_no_graph, strict=True)
            if _has_final_media_metric(graph_item) and _has_final_media_metric(baseline_item)
        ),
        "without_graph": without,
        "with_graph": with_graph,
        "delta": delta,
        "note": "最终媒体召回率来自完整 QA 的交付媒体，不是 graph primary/fused 候选头",
    }


def final_media_effect_from_run_summaries(
    graph_summary: Mapping[str, Any] | None,
    no_graph_summary: Mapping[str, Any] | None,
    *,
    baseline_run_id: str | None = None,
    graph_run_id: str | None = None,
    graph_run_valid: bool = True,
    baseline_run_valid: bool = True,
    pairing_error: str | None = None,
) -> dict[str, Any]:
    """Compare the final-media summary values shown on the run list.

    The overall card must use the exact run-level macro metrics (for example 39.3%), not
    recompute a possibly smaller intersection of item IDs.  Item-level ``qa_id`` pairing is
    still used by the question-type table, where it is needed to explain which questions
    improved or failed.
    """

    if no_graph_summary is None:
        return {
            "available": False,
            "status": "requires_paired_no_graph_run",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": "需要同相册、同题集、同模型的 graph_enabled=false 完整测评作对照",
        }
    if graph_summary is None:
        return {
            "available": False,
            "status": "run_summary_unavailable",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": "当前 run 缺少最终媒体汇总结果",
        }
    if pairing_error or not graph_run_valid or not baseline_run_valid:
        return {
            "available": False,
            "status": "requires_complete_paired_runs",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": pairing_error
            or "有图和无图 run 都必须是 completed 且 run_valid=true，才能比较最终媒体结果",
        }

    def metric(summary: Mapping[str, Any], key: str) -> float | None:
        value = summary.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    keys = {
        "media_retrieval_recall": "media_retrieval_recall_macro",
        "media_retrieval_precision": "media_retrieval_precision_macro",
        "media_retrieval_f1": "media_retrieval_f1_macro",
        "answer_quality_mean": "answer_quality_mean",
    }
    without = {name: metric(no_graph_summary, key) for name, key in keys.items()}
    with_graph = {name: metric(graph_summary, key) for name, key in keys.items()}
    required = ("media_retrieval_recall", "media_retrieval_precision", "media_retrieval_f1")
    if any(without[name] is None or with_graph[name] is None for name in required):
        return {
            "available": False,
            "status": "run_summary_media_metrics_unavailable",
            "baseline_run_id": baseline_run_id,
            "with_graph_run_id": graph_run_id,
            "sample_count": 0,
            "note": "两轮 run 的 summary 缺少最终媒体 P/R/F1，不能用候选头代替",
        }
    delta = {
        name: (
            with_graph[name] - without[name]
            if with_graph[name] is not None and without[name] is not None
            else None
        )
        for name in keys
    }
    baseline_count = no_graph_summary.get("media_retrieval_metric_count")
    graph_count = graph_summary.get("media_retrieval_metric_count")
    counts = [int(value) for value in (baseline_count, graph_count) if isinstance(value, (int, float))]
    return {
        "available": True,
        "status": "run_summary",
        "baseline_run_id": baseline_run_id,
        "with_graph_run_id": graph_run_id,
        "sample_count": (
            int(graph_count)
            if isinstance(graph_count, (int, float))
            else (counts[0] if counts else 0)
        ),
        "baseline_sample_count": int(baseline_count) if isinstance(baseline_count, (int, float)) else None,
        "with_graph_sample_count": int(graph_count) if isinstance(graph_count, (int, float)) else None,
        "without_graph": without,
        "with_graph": with_graph,
        "delta": delta,
        "note": "总体指标直接读取两轮 run 列表的最终媒体汇总值；题型明细另按 qa_id 配对",
    }


def aggregate_graph_qa_overall(
    items: list[dict[str, Any]],
    baseline_items: list[dict[str, Any]] | None = None,
    *,
    baseline_run_id: str | None = None,
    graph_run_id: str | None = None,
    graph_run_valid: bool = True,
    baseline_run_valid: bool = True,
    pairing_error: str | None = None,
) -> dict[str, Any]:
    """Aggregate the same-question primary→fused comparison for the whole run.

    A single graph-on run has no answer generated from the primary-only candidates, so
    answer quality cannot honestly be differenced here.  The response exposes the graph-on
    quality and an explicit comparison status; a paired graph-off run can fill the missing
    baseline later without changing the retrieval metric contract.
    """

    comparisons: list[dict[str, Any]] = []
    graph_judge_scores: list[float] = []
    routed_count = 0
    enabled_count = 0
    for item in items:
        trace = _graph_trace(item)
        if trace is None:
            continue
        routed_count += 1
        channels = trace.get("retrieval_channels") or []
        graph_channel = next(
            (
                channel
                for channel in channels
                if isinstance(channel, Mapping) and channel.get("channel") == "graph"
            ),
            None,
        )
        if isinstance(graph_channel, Mapping) and graph_channel.get("status") == "ok":
            enabled_count += 1
        comparison = _graph_comparison(item, trace)
        if comparison is not None:
            comparisons.append(comparison)
            score = _judge_score(item.get("judge"))
            if score is not None:
                graph_judge_scores.append(score)

    candidate_effect: dict[str, Any] = {
        "without_graph": {
            "retrieval_recall": (
                _mean([row["baseline_recall"] for row in comparisons]) if comparisons else None
            ),
            "retrieval_precision": (
                _mean([row["baseline_precision"] for row in comparisons]) if comparisons else None
            ),
        },
        "with_graph": {
            "retrieval_recall": (
                _mean([row["graph_recall"] for row in comparisons]) if comparisons else None
            ),
            "retrieval_precision": (
                _mean([row["graph_precision"] for row in comparisons]) if comparisons else None
            ),
        },
    }
    candidate_effect["delta"] = {
        "retrieval_recall": (
            candidate_effect["with_graph"]["retrieval_recall"]
            - candidate_effect["without_graph"]["retrieval_recall"]
            if comparisons else None
        ),
        "retrieval_precision": (
            candidate_effect["with_graph"]["retrieval_precision"]
            - candidate_effect["without_graph"]["retrieval_precision"]
            if comparisons else None
        ),
    }
    final_effect = _paired_final_media_effect(
        items,
        baseline_items,
        baseline_run_id=baseline_run_id,
        graph_run_id=graph_run_id,
        graph_run_valid=graph_run_valid,
        baseline_run_valid=baseline_run_valid,
        pairing_error=pairing_error,
    )
    if not comparisons:
        return {
            "available": False,
            "telemetry_status": "not_recorded" if not routed_count else "no_gt",
            "sample_count": 0,
            "graph_routed_count": routed_count,
            "graph_enabled_count": enabled_count,
            "quality_comparison_status": "requires_paired_no_graph_run",
            "comparison_basis": (
                "final_media_delivery" if final_effect.get("available") else "candidate_heads"
            ),
            "candidate_effect": candidate_effect,
            "final_media_effect": final_effect,
        }

    baseline_recall = _mean([row["baseline_recall"] for row in comparisons])
    baseline_precision = _mean([row["baseline_precision"] for row in comparisons])
    graph_recall = _mean([row["graph_recall"] for row in comparisons])
    graph_precision = _mean([row["graph_precision"] for row in comparisons])
    quality_mean = round(sum(graph_judge_scores) / len(graph_judge_scores), 3) if graph_judge_scores else None
    report = {
        "available": True,
        "telemetry_status": "recorded",
        "sample_count": len(comparisons),
        "graph_routed_count": routed_count,
        "graph_enabled_count": enabled_count,
        "without_graph": {
            "retrieval_recall": baseline_recall,
            "retrieval_precision": baseline_precision,
            "answer_quality_mean": None,
        },
        "with_graph": {
            "retrieval_recall": graph_recall,
            "retrieval_precision": graph_precision,
            "answer_quality_mean": quality_mean,
            "judge_valid_count": len(graph_judge_scores),
        },
        "delta": {
            "retrieval_recall": round(graph_recall - baseline_recall, 4)
            if graph_recall is not None and baseline_recall is not None
            else None,
            "retrieval_precision": round(graph_precision - baseline_precision, 4)
            if graph_precision is not None and baseline_precision is not None
            else None,
            "answer_quality_mean": None,
        },
        "quality_comparison_status": "requires_paired_no_graph_run",
        "quality_comparison_note": "质量分需要同题、同模型、关闭图检索的对照 run；本轮只记录了有图回答。",
        "candidate_effect": candidate_effect,
        "final_media_effect": final_effect,
    }
    # 一旦用户提供了同题 graph-off run，顶层的“无图/有图/变化”必须采用列表页
    # 的最终媒体交付口径。primary → fused 只能保留在 candidate_effect 里做诊断，
    # 不能再伪装成最终媒体召回率。
    if final_effect.get("available"):
        report["comparison_basis"] = "final_media_delivery"
        report["without_graph"] = {
            "retrieval_recall": final_effect["without_graph"]["media_retrieval_recall"],
            "retrieval_precision": final_effect["without_graph"]["media_retrieval_precision"],
            "answer_quality_mean": final_effect["without_graph"]["answer_quality_mean"],
        }
        report["with_graph"] = {
            "retrieval_recall": final_effect["with_graph"]["media_retrieval_recall"],
            "retrieval_precision": final_effect["with_graph"]["media_retrieval_precision"],
            "answer_quality_mean": final_effect["with_graph"]["answer_quality_mean"],
        }
        report["delta"] = {
            "retrieval_recall": final_effect["delta"]["media_retrieval_recall"],
            "retrieval_precision": final_effect["delta"]["media_retrieval_precision"],
            "answer_quality_mean": final_effect["delta"]["answer_quality_mean"],
        }
    else:
        report["comparison_basis"] = "candidate_heads"
    return report


def aggregate_graph_qa_by_type(
    items: list[dict[str, Any]],
    baseline_items: list[dict[str, Any]] | None = None,
    *,
    graph_run_valid: bool = True,
    baseline_run_valid: bool = True,
    pairing_error: str | None = None,
) -> list[dict[str, Any]]:
    """按题型汇总检索、回答质量和图拓展带来的 GT 召回变化。

    没有 ``graph_*`` 遥测的历史 run 仍然会出表，但 ``graph_effect_count`` 为 0，
    由 ``telemetry_status`` 明确标成 ``not_recorded``，而不是把普通检索结果冒充成
    图检索结果。
    """

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = str(item.get("question_type") or item.get("task_type") or "other")
        buckets.setdefault(key, []).append(item)

    rows: list[dict[str, Any]] = []
    baseline_by_id = {
        str(item.get("qa_id") or ""): item for item in (baseline_items or []) if item.get("qa_id")
    }
    for key, bucket in buckets.items():
        recalls = [
            float(value)
            for item in bucket
            if isinstance(
                (value := item.get("media_retrieval_recall", item.get("retrieval_recall"))),
                (int, float),
            )
        ]
        precisions = [
            float(value)
            for item in bucket
            if isinstance(
                (value := item.get("media_retrieval_precision", item.get("retrieval_precision"))),
                (int, float),
            )
        ]
        f1s = [
            float(value)
            for item in bucket
            if isinstance((value := item.get("media_retrieval_f1", item.get("retrieval_f1"))), (int, float))
        ]
        judge_scores = [score for item in bucket if (score := _judge_score(item.get("judge"))) is not None]
        evidence_scores = [
            float(score)
            for item in bucket
            if isinstance((score := (item.get("evidence_judge") or {}).get("score")), (int, float))
        ]
        traces = [trace for item in bucket if (trace := _graph_trace(item)) is not None]
        graph_enabled = 0
        intents: dict[str, int] = {}
        deltas: list[float] = []
        comparisons: list[dict[str, Any]] = []
        improved = worsened = rank_changed = set_changed = 0

        for item, trace in ((item, _graph_trace(item)) for item in bucket):
            if trace is None:
                continue
            channels = trace.get("retrieval_channels") or []
            graph_channel = next(
                (
                    channel
                    for channel in channels
                    if isinstance(channel, Mapping) and channel.get("channel") == "graph"
                ),
                None,
            )
            if isinstance(graph_channel, Mapping) and graph_channel.get("status") == "ok":
                graph_enabled += 1
            policy = trace.get("graph_policy") or {}
            if isinstance(policy, Mapping):
                intent = str(policy.get("intent") or "ordinary")
                intents[intent] = intents.get(intent, 0) + 1

            baseline, final = _graph_heads(trace)
            gt = _gt_asset_ids(item)
            # 空的 baseline/final 也是有效观测：例如 primary 没召回、或图扩展把
            # 条件结果全部过滤掉时，必须把它计为 0 召回，而不能静默丢掉这道题。
            if not gt:
                continue
            if baseline != final:
                rank_changed += 1
            if set(baseline) != set(final):
                set_changed += 1
            comparison = _graph_comparison(item, trace)
            if comparison is None:
                continue
            comparisons.append(comparison)
            delta = comparison["recall_delta"]
            deltas.append(delta)
            if delta > 1e-9:
                improved += 1
            elif delta < -1e-9:
                worsened += 1

        rows.append(
            {
                "type": key,
                "label": QUESTION_TYPE_LABELS.get(key, key),
                "sample_count": len(bucket),
                "retrieval_metric_count": len(recalls),
                "graph_routed_count": len(traces),
                "graph_enabled_count": graph_enabled,
                "graph_intents": intents,
                "graph_effect_count": len(deltas),
                "graph_recall_delta": _mean(deltas),
                "graph_gt_improved_count": improved,
                "graph_gt_worsened_count": worsened,
                "graph_rank_changed_count": rank_changed,
                "graph_candidate_set_changed_count": set_changed,
                "telemetry_status": "recorded" if traces else "not_recorded",
                "retrieval_recall": _mean(recalls),
                "retrieval_precision": _mean(precisions),
                "retrieval_f1": _mean(f1s),
                "without_graph_recall": _mean([comparison["baseline_recall"] for comparison in comparisons]),
                "without_graph_precision": _mean(
                    [comparison["baseline_precision"] for comparison in comparisons]
                ),
                "with_graph_recall": _mean([comparison["graph_recall"] for comparison in comparisons]),
                "with_graph_precision": _mean([comparison["graph_precision"] for comparison in comparisons]),
                "graph_precision_delta": _mean([comparison["precision_delta"] for comparison in comparisons]),
                "answer_quality_mean": (
                    round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None
                ),
                "judge_valid_count": len(judge_scores),
                "evidence_valid_count": len(evidence_scores),
                "evidence_supported_rate": round(
                    sum(1 for score in evidence_scores if score >= 2) / len(evidence_scores), 4
                )
                if evidence_scores
                else None,
                "quality_comparison_status": "requires_paired_no_graph_run",
            }
        )
        if baseline_items is not None:
            paired = [
                item
                for item in bucket
                if str(item.get("qa_id") or "") in baseline_by_id
            ]
            paired_baseline = [baseline_by_id[str(item.get("qa_id") or "")] for item in paired]
            effect = _paired_final_media_effect(
                paired,
                paired_baseline,
                graph_run_valid=graph_run_valid,
                baseline_run_valid=baseline_run_valid,
                pairing_error=pairing_error,
            )
            row = rows[-1]
            if effect.get("available"):
                row.update(
                    final_media_without_graph_recall=effect["without_graph"]["media_retrieval_recall"],
                    final_media_with_graph_recall=effect["with_graph"]["media_retrieval_recall"],
                    final_media_recall_delta=effect["delta"]["media_retrieval_recall"],
                    final_media_without_graph_precision=effect["without_graph"]["media_retrieval_precision"],
                    final_media_with_graph_precision=effect["with_graph"]["media_retrieval_precision"],
                    final_media_precision_delta=effect["delta"]["media_retrieval_precision"],
                    final_media_without_graph_quality=effect["without_graph"]["answer_quality_mean"],
                    final_media_with_graph_quality=effect["with_graph"]["answer_quality_mean"],
                    final_media_quality_delta=effect["delta"]["answer_quality_mean"],
                )
    return sorted(rows, key=lambda row: (-row["sample_count"], row["type"]))


def _metrics(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "available": bool(tp + fp + fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
    }


def _image_link_metrics(
    image_to_face_ids: Mapping[str, object],
    predicted_clusters_by_file: Mapping[str, set[str]],
    asset_names: set[str],
) -> dict[str, Any]:
    samples: list[tuple[set[str], set[str]]] = []
    gt_asset_count = detected_image_count = clustered_image_count = 0
    for name, labels in image_to_face_ids.items():
        file_name = Path(str(name)).name.lower()
        if file_name not in asset_names or not isinstance(labels, list) or not labels:
            continue
        gt_asset_count += 1
        clusters = {str(value) for value in predicted_clusters_by_file.get(file_name, set()) if value}
        if file_name in predicted_clusters_by_file:
            detected_image_count += 1
            clustered_image_count += bool(clusters)
        # 未检测到的人脸也必须进入 pairwise 分母；否则漏检只会减少样本数，
        # 不会产生 false negative，最终 link recall 会被系统性高估。
        samples.append(({str(value) for value in labels}, clusters))
    tp = fp = fn = tn = 0
    for left, right in itertools.combinations(samples, 2):
        gt_same = bool(left[0] & right[0])
        predicted_same = bool(left[1] & right[1])
        if gt_same and predicted_same:
            tp += 1
        elif predicted_same:
            fp += 1
        elif gt_same:
            fn += 1
        else:
            tn += 1
    result = _metrics(tp, fp, fn)
    result.update(
        {
            "true_negative": tn,
            "evaluable_pair_count": len(samples) * (len(samples) - 1) // 2,
            "evaluable_image_count": len(samples),
            "gt_asset_count": gt_asset_count,
            "detected_image_count": detected_image_count,
            "clustered_image_count": clustered_image_count,
        }
    )
    return result


def _identity_gallery_metrics(
    image_to_face_ids: Mapping[str, object],
    predicted_identities_by_file: Mapping[str, set[str]],
    asset_names: set[str],
) -> dict[str, Any]:
    samples: list[tuple[set[str], set[str]]] = []
    gt_asset_count = identified_image_count = 0
    for name, labels in image_to_face_ids.items():
        file_name = Path(str(name)).name.lower()
        if file_name not in asset_names or not isinstance(labels, list) or not labels:
            continue
        gt_asset_count += 1
        predicted = {str(value) for value in predicted_identities_by_file.get(file_name, set()) if value}
        identified_image_count += bool(predicted)
        # 没有预测身份时保留空集合，所有 GT identity 都应计为 false negative。
        samples.append(({str(value) for value in labels}, predicted))
    tp = sum(len(gt & predicted) for gt, predicted in samples)
    fp = sum(len(predicted - gt) for gt, predicted in samples)
    fn = sum(len(gt - predicted) for gt, predicted in samples)
    result = _metrics(tp, fp, fn)
    result.update(
        {
            "evaluable_image_count": len(samples),
            "gt_asset_count": gt_asset_count,
            "identified_image_count": identified_image_count,
        }
    )
    return result


def face_clustering_quality(
    clusters: list[dict[str, Any]],
    image_to_face_ids: Mapping[str, object] | None,
    asset_names: set[str] | None = None,
) -> dict[str, Any]:
    """按旧版 v4 口径计算身份 gallery 与图像级 identity-link，并修正漏检分母。

    ``asset_names`` 是当前 scope 实际导入的文件名集合。没有它时为兼容独立纯函数
    调用，回退到预测簇里的文件名；正式报告必须传入资产清单，否则“完全没检测到脸”
    的 GT 图片会从分母中消失。
    """

    if not image_to_face_ids:
        return {
            "available": False,
            "reason": "benchmark identity GT not found",
            "metric_definition_version": FACE_CLUSTERING_METRIC_VERSION,
        }
    if asset_names is None:
        asset_names = {
            Path(str(sample.get("file_name") or "")).name.lower()
            for cluster in clusters
            for sample in (cluster.get("samples") or [])
            if sample.get("file_name")
        }
    else:
        asset_names = {Path(str(name)).name.lower() for name in asset_names if str(name).strip()}
    raw_by_file: dict[str, list[tuple[str, str | None]]] = {}
    verified_by_file: dict[str, list[tuple[str, str | None]]] = {}
    for cluster in clusters:
        cluster_id = str(cluster.get("id") or "")
        for sample in cluster.get("samples") or []:
            file_name = Path(str(sample.get("file_name") or "")).name.lower()
            face_id = str(sample.get("face_instance_id") or "")
            if not file_name or not face_id:
                continue
            pair = (face_id, cluster_id or None)
            raw_by_file.setdefault(file_name, []).append(pair)
            if str(sample.get("validity") or "").lower() == "verified":
                verified_by_file.setdefault(file_name, []).append(pair)

    seed_cluster_by_identity: dict[str, str] = {}
    for file_name, detected in raw_by_file.items():
        stem = Path(file_name).stem
        if not stem.lower().startswith("faceid_"):
            continue
        cluster_ids = {cluster_id for _, cluster_id in detected if cluster_id}
        if len(cluster_ids) == 1:
            seed_cluster_by_identity[stem[7:]] = next(iter(cluster_ids))
    identity_by_seed_cluster = {cluster: identity for identity, cluster in seed_cluster_by_identity.items()}
    predicted_identities = {
        file_name: {
            identity_by_seed_cluster[cluster_id]
            for _, cluster_id in detected
            if cluster_id in identity_by_seed_cluster
        }
        for file_name, detected in verified_by_file.items()
    }
    predicted_clusters = {
        file_name: {cluster_id for _, cluster_id in detected if cluster_id}
        for file_name, detected in verified_by_file.items()
    }
    gallery = _identity_gallery_metrics(image_to_face_ids, predicted_identities, asset_names)
    links = _image_link_metrics(image_to_face_ids, predicted_clusters, asset_names)
    gt_single = {
        Path(str(name)).name.lower(): str(labels[0])
        for name, labels in image_to_face_ids.items()
        if isinstance(labels, list) and len(labels) == 1
    }
    evaluable_gt_single = {name for name in gt_single if name in asset_names}
    detected_single = sum(1 for name in evaluable_gt_single if len(verified_by_file.get(name, [])) == 1)
    quality = {
        "available": bool(gallery.get("available") or links.get("available")),
        "metric_definition_version": FACE_CLUSTERING_METRIC_VERSION,
        "identity_gallery": gallery,
        "image_identity_links": links,
        "gt_single_image_count": len(gt_single),
        "single_face_detection_rate": (
            round(detected_single / len(evaluable_gt_single), 4) if evaluable_gt_single else None
        ),
        "asset_name_coverage_count": sum(1 for name in gt_single if name in asset_names),
        "evaluable_gt_single_image_count": len(evaluable_gt_single),
        "identity_gallery_seed_count": len(seed_cluster_by_identity),
        "verified_detection_image_count": len(verified_by_file),
        "gt_multi_identity_image_count": sum(
            1
            for name, labels in image_to_face_ids.items()
            if Path(str(name)).name.lower() in asset_names and isinstance(labels, list) and len(labels) != 1
        ),
    }
    return quality
