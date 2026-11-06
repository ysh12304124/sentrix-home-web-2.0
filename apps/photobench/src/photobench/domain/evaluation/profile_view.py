"""Read adapters for independent profile scores and untouched historical snapshots."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, cast

from photobench.domain.evaluation.media import saved_media_metrics
from photobench.domain.evaluation.profiles import (
    ATM,
    LABELS,
    OPEN,
    evaluation_turn,
    result_key,
    scoring_summary,
    valid_result,
)


def select_batch(state: Mapping[str, Any], profile: str, batch_id: str = "") -> Mapping[str, Any] | None:
    if profile not in LABELS:
        raise ValueError("Unknown scoring profile")
    batches = [b for b in state.get("scoring_batches") or [] if b["profile_id"] == profile]
    if batch_id:
        match = next((b for b in batches if b["batch_id"] == batch_id), None)
        if match is None:
            raise ValueError("Scoring batch does not belong to this standard")
        return cast(Mapping[str, Any], match)
    return batches[-1] if batches else None


def item_scores(state: Mapping[str, Any], qa_id: str) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(r) for r in (state.get("scoring_results") or {}).values() if str(r["qa_id"]) == qa_id
    ]


def item_profile_results(state: Mapping[str, Any], qa_id: str) -> dict[str, Any]:
    """Both latest standards for the evaluation turn, independent of score filters."""
    row: Mapping[str, Any] = next(
        (r for r in (state.get("qa_snapshot") or {}).get("rows") or [] if str(r["qa_id"]) == qa_id), {}
    )
    turn = evaluation_turn(row)
    results = {}
    for profile in LABELS:
        batch = select_batch(state, profile)
        batch_id = str(batch["batch_id"]) if batch else ""
        value = (state.get("scoring_results") or {}).get(result_key(batch_id, qa_id, turn)) or {}
        valid = valid_result(value)
        results[profile] = {
            **{k: copy.deepcopy(v) for k, v in value.items() if k not in {"requests", "history"}},
            "profile_id": profile,
            "batch_id": batch_id,
            "turn_index": turn,
            "status": value.get("status", "not_collected"),
            "judge_status": value.get("status", "not_collected"),
            "score": value.get("score") if valid else None,
            "max_score": 2 if profile == OPEN else 1,
            "normalized_score": value["score"] / value["max_score"] if valid else None,
            "scorer": value.get("purpose") or ("answer_quality" if profile == OPEN else profile),
            "judge_model": value.get("judge_model") or ((batch or {}).get("judge") or {}).get("model"),
        }
    return results


def choice_matching_result(state: Mapping[str, Any], qa_id: str) -> dict[str, Any] | None:
    """Expose this standard's selected batch independently of the page's main score."""
    row: Mapping[str, Any] = next(
        (r for r in (state.get("qa_snapshot") or {}).get("rows") or [] if str(r["qa_id"]) == qa_id), {}
    )
    if (row.get("evaluation") or {}).get("answer_format") != "choice":
        return None
    selected = state.get("selected_scoring_batch", "") if state.get("selected_scoring_profile") == ATM else ""
    batch = select_batch(state, ATM, selected)
    if not batch:
        return None
    turn = evaluation_turn(row)
    value = (state.get("scoring_results") or {}).get(result_key(batch["batch_id"], qa_id, turn)) or {}
    fields = ("version", "rule_version", "matched_option", "comparisons", "reason", "semantic_aux")
    return {
        **{k: copy.deepcopy(value[k]) for k in fields if k in value},
        "profile_id": ATM,
        "batch_id": batch["batch_id"],
        "turn_index": turn,
        "scorer": "choice",
        "answer_format": "choice",
        "status": value.get("status", "not_collected"),
        "score": value.get("score") if valid_result(value) else None,
        "max_score": 1,
        "judge_model": value.get("judge_model") or (batch.get("judge") or {}).get("model"),
    }


def primary_profile_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("selected_scoring_profile") or not state.get("scoring_profiles"):
        return state
    ids = state.get("primary_scoring_batch_ids") or []
    batch = next((b for b in state.get("scoring_batches") or [] if b["batch_id"] in ids), None)
    return profile_state(state, batch["profile_id"], batch["batch_id"]) if batch else state


def profile_state(state: dict[str, Any], profile: str = "", batch_id: str = "") -> dict[str, Any]:
    if not profile:
        if batch_id:
            raise ValueError("Select a scoring standard for this batch")
        return state
    batch = select_batch(state, profile, batch_id)
    batch_id = str(batch["batch_id"]) if batch else ""
    rows = {str(r["qa_id"]): r for r in (state.get("qa_snapshot") or {}).get("rows") or []}
    projected: dict[str, Any] = {
        **state,
        "items": [],
        "selected_scoring_profile": profile,
        "selected_scoring_batch": batch_id,
    }
    results = state.get("scoring_results") or {}
    for original in state.get("items") or []:
        item = dict(original)
        item["runtime_turns"] = [dict(t) for t in original.get("runtime_turns") or []]
        qa_id = str(item.get("qa_id"))
        turn_index = evaluation_turn(rows.get(qa_id, {}))

        def score(index: int, qa_id: str = qa_id, original: Mapping[str, Any] = original) -> dict[str, Any]:
            value = results.get(result_key(batch_id, qa_id, index)) or {}
            return {
                **{
                    k: value[k]
                    for k in (
                        "batch_id",
                        "rule_version",
                        "profile_id",
                        "status",
                        "reason",
                        "finished_at",
                        "purpose",
                        "choice_match",
                        "version",
                        "answer_format",
                        "source_qtype",
                        "matched_option",
                        "comparisons",
                        "semantic_aux",
                        "formula",
                        "target",
                        "predicted",
                        "intersection",
                        "number_aux",
                        "number_aux_disagrees",
                        "choice_semantic_aux",
                    )
                    if k in value
                },
                "judge_model": value.get("judge_model") or ((batch or {}).get("judge") or {}).get("model"),
                "scoring_rule": {
                    "rule_version": (batch or {}).get("rule_version"),
                    "frozen_prompt_template": ((batch or {}).get("config") or {})
                    .get("prompts", {})
                    .get(value.get("purpose")),
                    "use_gt_images": ((batch or {}).get("config") or {}).get("use_gt_images"),
                    "use_gt_metadata": ((batch or {}).get("config") or {}).get("use_gt_metadata"),
                    "gt_metadata_mode": ((batch or {}).get("config") or {}).get("gt_metadata_mode"),
                    "annotation_version": ((batch or {}).get("gt_metadata_snapshot") or {}).get(
                        "annotation_version"
                    ),
                    "mapping_sha256": ((batch or {}).get("gt_metadata_snapshot") or {}).get("sha256"),
                },
                "score": value.get("score") if valid_result(value) else None,
                "max_score": 2 if profile == OPEN else 1,
                "normalized_score": value["score"] / value["max_score"] if valid_result(value) else None,
                "scorer": value.get("purpose") or profile,
                "judge_status": value.get("status", "not_collected"),
                "media_metrics": copy.deepcopy(value["media_metrics"])
                if "media_metrics" in value
                else saved_media_metrics(rows.get(qa_id, {}), original, index),
            }

        def apply(record: dict[str, Any], index: int, qa_id: str = qa_id) -> None:
            for field in (
                "judge_history",
                "scoring_history",
                "task_judge",
                "task_judges",
                "evidence_judge",
                "evidence_judges",
            ):
                record.pop(field, None)
            record["judge"] = record["evaluation_result"] = score(index)
            record["judge_status"] = record["judge"]["judge_status"]
            value = results.get(result_key(batch_id, qa_id, index)) or {}
            diagnostic = (state.get("diagnostic_results") or {}).get(value.get("diagnostic_key")) or {}
            for field in ("task_judge", "evidence_judge"):
                if field in diagnostic:
                    record[field] = copy.deepcopy(diagnostic[field])

        apply(item, turn_index)
        item["scoring_profile"] = profile
        item["scoring_batch_id"] = batch_id
        for i, turn in enumerate(item.get("runtime_turns") or []):
            apply(turn, i)
        projected["items"].append(item)
    scoring = scoring_summary(state)
    selected_summary = scoring["batches"].get(batch_id, scoring["profiles"][profile])
    scoring["profiles"][profile] = selected_summary
    projected["summary"] = {
        "scoring": scoring,
        "selected_scoring": selected_summary,
        "profile_media_version": 1,
    }
    return projected
