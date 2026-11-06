"""Scoring profiles, immutable batch inputs and QA-weighted summaries."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from photobench.domain.evaluation.prompts import ATM_OPEN_PROMPT, CHOICE_PROMPT, NUMBER_MATCH_PROMPT
from photobench.domain.runs.judge import JUDGE_PROMPT, JUDGE_PROMPT_KINDS

OPEN = "photobench_open_v1"
ATM = "atm_qs_v1"
RULE_VERSION = "scoring-2026.11.06-v4"
READABLE_METADATA_MODES = {"readable_v1", "image_metadata_v1"}
LABELS = {OPEN: "开放式回答（0/1/2，历史口径）", ATM: "ATM-Bench式QS"}
PROMPTS = {
    OPEN: {"answer_quality": JUDGE_PROMPT},
    ATM: {
        "choice": CHOICE_PROMPT,
        "number": NUMBER_MATCH_PROMPT.replace("规范化模型回答", "模型开放式回答")
        + "允许等值单位换算及等价日期写法；必须对照原问题和参考答案，不能补猜。",
        "open": ATM_OPEN_PROMPT,
    },
}


PROFILE_PROMPT_KINDS = {
    **JUDGE_PROMPT_KINDS,
    **{
        f"{profile}:{purpose}": prompt
        for profile, prompts in PROMPTS.items()
        for purpose, prompt in prompts.items()
    },
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def profile_configs(
    profiles: Any = None, configs: Any = None, custom: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    selected = [OPEN] if profiles is None else profiles
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(p, str) for p in selected)
        or len(set(selected)) != len(selected)
        or any(p not in LABELS for p in selected)
    ):
        raise ValueError("scoring_profiles must select one or both supported standards")
    if configs is not None and not isinstance(configs, dict):
        raise ValueError("scoring_configs must be an object")
    result = {}
    for profile in selected:
        raw = (configs or {}).get(profile, {})
        if not isinstance(raw, dict) or set(raw) - {
            "prompts",
            "use_gt_images",
            "use_gt_metadata",
            "gt_metadata_mode",
            "quality_input_version",
        }:
            raise ValueError("Invalid scoring profile configuration")
        prompts = dict(PROMPTS[profile])
        for purpose in prompts:
            key = f"{profile}:{purpose}"
            saved = (custom or {}).get(key)
            if profile == OPEN and not saved:
                saved = (custom or {}).get("answer_quality")
            prompts[purpose] = saved or prompts[purpose]
        overrides = raw.get("prompts", {})
        if not isinstance(overrides, dict) or set(overrides) - set(prompts):
            raise ValueError("Unknown scoring prompt purpose")
        prompts.update(overrides)
        if any(not isinstance(p, str) or not p.strip() or len(p) > 50000 for p in prompts.values()):
            raise ValueError("Scoring prompts must contain 1–50000 characters")
        flags = {k: raw.get(k, False) for k in ("use_gt_images", "use_gt_metadata")}
        if any(type(v) is not bool for v in flags.values()):
            raise ValueError("GT flags must be booleans")
        result[profile] = {"prompts": prompts, **flags}
        if profile == OPEN:
            version = raw.get("quality_input_version", "task_v3")
            if version not in {"legacy_v1", "task_v2", "task_v3"}:
                raise ValueError("Unknown answer quality input version")
            result[profile]["quality_input_version"] = version
        elif "quality_input_version" in raw:
            raise ValueError("Answer quality input version is only valid for the open profile")
        # Explicit old-client flags retain their protocol. Frozen batches never pass here.
        if "gt_metadata_mode" in raw or "use_gt_metadata" not in raw:
            mode = raw.get("gt_metadata_mode", "image_metadata_v1")
            if mode not in READABLE_METADATA_MODES:
                raise ValueError("Unknown GT metadata mode")
            result[profile].update(
                use_gt_metadata=flags["use_gt_images"] if mode == "image_metadata_v1" else True,
                gt_metadata_mode=mode,
            )
    return result


def uses_readable_metadata(config: Mapping[str, Any]) -> bool:
    return bool(config.get("use_gt_metadata") and config.get("gt_metadata_mode") in READABLE_METADATA_MODES)


def answer_fingerprint(record: Mapping[str, Any]) -> str:
    return digest(
        {
            k: record.get(k)
            for k in ("message", "answer", "predicted_media", "media_mapping_complete", "turn_id")
        }
    )


def evaluation_turn(row: Mapping[str, Any]) -> int:
    return int(
        (row.get("evaluation") or {}).get("applies_to_turn", max(0, len(row.get("conversation") or []) - 1))
    )


def result_key(batch_id: str, qa_id: str, turn: int) -> str:
    return f"{batch_id}:{qa_id}:{turn}"


def valid_result(result: Mapping[str, Any]) -> bool:
    score = result.get("score")
    maximum = 2 if result.get("profile_id") == OPEN else 1
    return (
        result.get("status") == "completed"
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        and result.get("max_score") == maximum
        and 0 <= score <= maximum
        and (score in {0, 1, 2} if maximum == 2 else result.get("purpose") == "list" or score in {0, 1})
    )


def diagnostic_key(batch: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    return digest(
        [
            batch["judge"],
            batch.get("diagnostics") or {},
            target["qa_id"],
            target["turn_index"],
            target["answer_fingerprint"],
        ]
    )


def item_scoring_error(state: Mapping[str, Any], item: Mapping[str, Any]) -> str | None:
    """Validate every original scoring batch/turn, independent of legacy Judge placeholders."""
    batches = {b["batch_id"]: b for b in state.get("scoring_batches") or []}
    results = state.get("scoring_results") or {}
    for batch_id in state.get("primary_scoring_batch_ids") or []:
        if batch_id not in batches:
            return f"Missing primary scoring batch: {batch_id}"
        batch = batches[batch_id]
        for index, record in enumerate(item.get("runtime_turns") or [item]):
            result = results.get(result_key(batch_id, str(item["qa_id"]), index)) or {}
            prefix = f"{batch['profile_id']} turn {index + 1}"
            if not valid_result(result) or result.get("answer_fingerprint") != answer_fingerprint(record):
                return f"{prefix}: {result.get('reason') or 'missing valid scoring result'}"
            diagnostic = (state.get("diagnostic_results") or {}).get(result.get("diagnostic_key")) or {}
            if diagnostic.get("status") != "completed":
                return f"{prefix}: {diagnostic.get('reason') or 'shared diagnostics incomplete'}"
    return None


def make_batch(
    profile: str,
    config: Mapping[str, Any],
    judge: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    scope: str,
    created_at: str,
    nonce: str = "",
    metadata_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    frozen = {
        "profile_id": profile,
        "diagnostics": copy.deepcopy(judge.get("diagnostics") or {}),
        "rule_version": RULE_VERSION,
        "config": copy.deepcopy(config),
        "judge": {k: judge.get(k) for k in ("url", "model", "provider_id")},
        "qa_definition_sha256": snapshot.get("definition_sha256"),
        "qa_snapshot_sha256": digest(snapshot.get("rows") or []),
    }
    if uses_readable_metadata(config):
        from photobench.domain.evaluation.gt_context import CAPTURE_INSTRUCTIONS, empty_context

        frozen["gt_metadata_snapshot"] = copy.deepcopy(metadata_snapshot or empty_context())
        frozen["gt_metadata_instructions"] = CAPTURE_INSTRUCTIONS
    return {
        **frozen,
        "batch_id": "score-" + digest([frozen, nonce])[:24],
        "created_at": created_at,
        "scope": scope,
        "targets": [],
        "materials": {},
        "status": "pending",
        "submissions": [],
    }


def scoring_targets(
    state: Mapping[str, Any],
    batch: Mapping[str, Any],
    scope: str,
    qa_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if scope not in {"missing", "failed", "unscored", "needs_review", "all"}:
        raise ValueError("Invalid scoring scope")
    rows = {str(r["qa_id"]): r for r in (state.get("qa_snapshot") or {}).get("rows") or []}
    selected = []
    results = state.get("scoring_results") or {}
    source_id = batch.get("selection_source_batch_id")
    allowed = qa_ids
    for item_index, item in enumerate(state.get("items") or []):
        if item.get("execution_status") != "completed":
            continue
        qa_id = str(item.get("qa_id"))
        if allowed is not None and qa_id not in allowed:
            continue
        if qa_id not in rows:
            raise ValueError("Frozen QA definition missing: " + qa_id)
        for turn_index, record in enumerate(item.get("runtime_turns") or [item]):
            if "answer" not in record:
                continue
            key = result_key(str(batch["batch_id"]), qa_id, turn_index)
            old = results.get(key) or {}
            if old and old.get("answer_fingerprint") != answer_fingerprint(record):
                raise ValueError("Saved answer changed; create a new scoring batch")
            target = {
                "key": key,
                "qa_id": qa_id,
                "turn_index": turn_index,
                "item_index": item_index,
                "answer_fingerprint": answer_fingerprint(record),
                "is_evaluation_turn": turn_index == evaluation_turn(rows[qa_id]),
            }
            diagnostic = (state.get("diagnostic_results") or {}).get(diagnostic_key(batch, target), {})
            if valid_result(old):
                if diagnostic.get("status") != "completed" and scope in {"missing", "all", "failed"}:
                    selected.append({**target, "diagnostics_only": True})
                continue
            source = old or results.get(result_key(str(source_id), qa_id, turn_index), {})
            status = source.get("status")
            if scope == "failed" and status not in {"failed", "running", "interrupted"}:
                continue
            if scope == "needs_review" and status != "needs_review":
                continue
            if scope == "unscored" and (valid_result(source) or status in {"failed", "needs_review"}):
                continue
            if scope == "missing" and (valid_result(source) or status == "needs_review"):
                continue
            selected.append(target)
    return selected


def request_history(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        request
        for attempt in [*(result.get("history") or []), result]
        for request in attempt.get("requests") or []
    ]


def summarize_batches(state: Mapping[str, Any]) -> dict[str, Any]:
    rows = (state.get("qa_snapshot") or {}).get("rows") or []
    definitions = {str(row["qa_id"]): row for row in rows}
    total = len(rows) or int(state.get("qa_count") or 0)
    results = state.get("scoring_results") or {}
    summaries: dict[str, Any] = {}
    for batch in state.get("scoring_batches") or []:
        batch_results = [r for r in results.values() if r.get("batch_id") == batch["batch_id"]]
        values = {
            str(r["qa_id"]): r
            for r in batch_results
            if str(r.get("qa_id")) in definitions
            and r.get("turn_index") == evaluation_turn(definitions[str(r["qa_id"])])
        }
        valid = [r for r in values.values() if valid_result(r)]
        scored = len(valid)
        maximum = 2 if batch["profile_id"] == OPEN else 1
        mean = sum(r["score"] for r in valid) / scored if scored else None
        normalized = mean / maximum if mean is not None else None
        requests = [q for r in batch_results for q in request_history(r) if q.get("dispatch_started")]
        summaries[batch["batch_id"]] = {
            "batch_id": batch["batch_id"],
            "profile_id": batch["profile_id"],
            "label": LABELS[batch["profile_id"]],
            "rule_version": batch["rule_version"],
            "judge": batch["judge"],
            "gt_config": {
                **{k: batch["config"][k] for k in ("use_gt_images", "use_gt_metadata")},
                "gt_metadata_mode": batch["config"].get("gt_metadata_mode"),
                "annotation_version": (batch.get("gt_metadata_snapshot") or {}).get("annotation_version"),
                "mapping_sha256": (batch.get("gt_metadata_snapshot") or {}).get("sha256"),
            },
            "qa_definition_sha256": batch["qa_definition_sha256"],
            "qa_set": state.get("qa_set"),
            "total": total,
            "scored": scored,
            "coverage": scored / total if total else 0,
            "max_score": maximum,
            "scored_mean": mean,
            "normalized_mean": normalized,
            "total_score": normalized if total and scored == total else None,
            "status": "completed" if total and scored == total else "incomplete",
            "failed": sum(r.get("status") == "failed" for r in values.values()),
            "needs_review": sum(r.get("status") == "needs_review" for r in values.values()),
            "judge_calls": len(requests),
            "judge_responses": sum(q.get("status") == "complete" for q in requests),
            "diagnostic_pending": sum(
                (state.get("diagnostic_results") or {}).get(r.get("diagnostic_key"), {}).get("status")
                != "completed"
                for r in batch_results
            ),
            "judge_duration_ms": sum(q.get("duration_ms", 0) for q in requests),
            "judge_tokens": sum(
                (q.get("raw_response", {}).get("usage") or {}).get("total_tokens", 0) for q in requests
            ),
        }
    return summaries


def scoring_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    batches = summarize_batches(state) if "scoring_results" in state else state.get("scoring_summaries") or {}
    latest = {p: {"profile_id": p, "label": LABELS[p], "status": "not_collected"} for p in LABELS}
    for batch in batches.values():
        latest[batch["profile_id"]] = batch
    if state.get("selected_scoring_profile") and state.get("selected_scoring_batch") in batches:
        latest[state["selected_scoring_profile"]] = batches[state["selected_scoring_batch"]]
    diagnostic_requests = [
        q
        for r in (state.get("diagnostic_results") or {}).values()
        for q in request_history(r)
        if q.get("dispatch_started")
    ]
    return {"profiles": latest, "batches": batches, "diagnostic_judge_calls": len(diagnostic_requests)}


def stable_media_key(ref: Mapping[str, Any]) -> str:
    kind = str(ref.get("media_type") or "")
    identifier = str(ref.get("media_id") or ref.get("file_name") or "").replace(chr(92), "/")
    if kind not in {"image", "video"} or not identifier:
        raise ValueError("Missing typed media identity")
    if kind == "video" and PurePosixPath(identifier).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
        identifier = str(PurePosixPath(identifier).with_suffix(""))
    return kind + ":" + identifier


class MediaIndex:
    """Resolve typed identities once per frozen media universe."""

    def __init__(self, universe: Sequence[Mapping[str, Any]]) -> None:
        self.keys = {stable_media_key(ref) for ref in universe}
        self.by_name: dict[tuple[str, str], set[str]] = {}
        for key in self.keys:
            kind, identifier = key.split(":", 1)
            self.by_name.setdefault((kind, PurePosixPath(identifier).name), set()).add(key)

    def resolve(self, ref: Mapping[str, Any]) -> str:
        key = stable_media_key(ref)
        if key in self.keys:
            return key
        kind, identifier = key.split(":", 1)
        matches = self.by_name.get((kind, PurePosixPath(identifier).name), set())
        if len(matches) > 1:
            raise ValueError("Ambiguous media mapping: " + key)
        return next(iter(matches)) if matches else key


def list_score(
    target: Sequence[Mapping[str, Any]], record: Mapping[str, Any], universe: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    prediction = record.get("predicted_media")
    if not isinstance(prediction, list) or record.get("media_mapping_complete") is not True:
        raise ValueError("Delivery record missing or media mapping incomplete")
    index = MediaIndex(universe)
    gt = {index.resolve(r) for r in target}
    pred = {index.resolve(r) for r in prediction}
    if not gt:
        raise ValueError("Frozen target set is empty")
    return {
        "score": len(gt & pred) / len(gt | pred),
        "target": sorted(gt),
        "predicted": sorted(pred),
        "intersection": sorted(gt & pred),
        "formula": "|P ∩ GT| / |P ∪ GT|",
    }


def media_ranking(
    item: Mapping[str, Any],
    universe: Sequence[Mapping[str, Any]] | None = None,
    *,
    index: MediaIndex | None = None,
) -> dict[str, Any]:
    if (item.get("evaluation") or {}).get("media_metrics_policy") == "exclude":
        return {"status": "not_applicable", "gt_count": 0, "reason": "excluded_by_evaluation_policy"}
    targets = item.get("retrieval_media_refs") or []
    candidates = item.get("retrieved_candidate_media")
    if not targets:
        return {"status": "not_applicable", "gt_count": 0}
    index = index or MediaIndex(targets if universe is None else universe)
    if not isinstance(candidates, list) or "retrieved_candidate_asset_ids" not in item:
        return {"status": "not_collected"}
    by_asset: dict[str, Mapping[str, Any]] = {}
    for ref in candidates:
        for field in ("asset_id", "source_keyframe_asset_id"):
            if ref.get(field):
                by_asset[str(ref[field])] = ref
    sequence = item["retrieved_candidate_asset_ids"]
    if any(str(identifier) not in by_asset for identifier in sequence):
        return {"status": "not_collected", "reason": "candidate_mapping_incomplete"}
    try:
        gt = {index.resolve(r) for r in targets}
        ordered = list(dict.fromkeys(index.resolve(by_asset[str(identifier)]) for identifier in sequence))
    except ValueError as exc:
        return {"status": "not_collected", "reason": str(exc)}
    return {
        "status": "available",
        "version": "media-r5-v1",
        "gt_count": len(gt),
        "ranked": ordered,
        "recall_at_5": len(gt & set(ordered[:5])) / len(gt),
    }


def media_ranking_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    universe = [r for item in items for r in item.get("retrieval_media_refs") or []]
    index = MediaIndex(universe)
    ranks = [
        media_ranking(i, index=index)
        for i in items
        if i.get("answerability") not in {"unanswerable", "insufficient_evidence"}
    ]
    included = [r for r in ranks if r.get("status") == "available" and 0 < r["gt_count"] <= 5]
    return {
        "version": "media-r5-v1",
        "recall_at_5": sum(r["recall_at_5"] for r in included) / len(included) if included else None,
        "included": len(included),
        "excluded_gt_over_5": sum(r.get("gt_count", 0) > 5 for r in ranks),
        "unavailable": sum(r.get("status") == "not_collected" for r in ranks),
        "scope": "GT数量≤5；图片和视频联合候选去重后前5份",
    }
