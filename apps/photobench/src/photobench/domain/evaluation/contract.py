"""Versioned scoring definitions, independent of ability/content annotations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

VERSION = "evaluation-2026.11.03-v1"
SCORERS = {"choice_judge", "media_set", "open_judge", "atm_number", "atm_open", "atm_list"}


def content_fingerprint(row: Mapping[str, Any]) -> str:
    fields = (
        "question",
        "answer",
        "retrieval_media_refs",
        "answer_evidence_media_refs",
        "expected_action",
        "answerability",
    )
    payload = {key: row.get(key) for key in fields}
    payload["conversation"] = [
        {
            key: turn.get(key)
            for key in (
                "message",
                "reference_answer",
                "expected_action",
                "answerability",
                "retrieval_media_refs",
                "answer_evidence_media_refs",
            )
        }
        for turn in row.get("conversation") or []
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_evaluation(row: Mapping[str, Any]) -> None:
    definition = row.get("evaluation")
    if definition is None:
        return
    if not isinstance(definition, dict) or definition.get("version") != VERSION:
        raise ValueError("Unsupported evaluation definition")
    scorer = definition.get("scorer")
    expected_format = {
        "choice_judge": "choice",
        "media_set": "media_list",
        "atm_list": "media_list",
        "open_judge": "open",
        "atm_number": "open",
        "atm_open": "open",
    }
    if scorer not in SCORERS or definition.get("answer_format") != expected_format[scorer]:
        raise ValueError("Evaluation format/scorer mismatch")
    if definition.get("content_sha256") != content_fingerprint(row):
        raise ValueError("Evaluation content fingerprint mismatch")
    if "answer_quality_rubric" in definition:
        rubric = definition["answer_quality_rubric"]
        if scorer != "open_judge" or not isinstance(rubric, str) or not rubric.strip():
            raise ValueError("Answer quality rubric requires nonempty text on open_judge")
    policy = definition.get("media_metrics_policy", "include")
    if policy not in ("include", "exclude") or (policy == "exclude" and scorer in {"media_set", "atm_list"}):
        raise ValueError("Invalid media metrics policy")
    index = definition.get("applies_to_turn")
    if type(index) is not int or not 0 <= index < max(1, len(row.get("conversation") or [])):
        raise ValueError("Invalid evaluation turn")
    if definition.get("max_score") not in {1, 2} or (
        scorer != "open_judge" and definition.get("max_score") != 1
    ):
        raise ValueError("Invalid evaluation scale")
    if scorer == "choice_judge":
        options = definition.get("options") or []
        ids = [o.get("id") for o in options if isinstance(o, dict)]
        if len(ids) < 2 or len(ids) != len(options) or len(set(ids)) != len(ids) or "other" in ids:
            raise ValueError("Choice options require unique IDs; other is reserved")
        if any(not str(o.get("text") or "").strip() for o in options):
            raise ValueError("Empty choice option")
        if definition.get("correct_option") not in ids or definition.get("review_status") != "verified":
            raise ValueError("Choice key or review missing")
    if scorer in {"media_set", "atm_list"}:
        refs = definition.get("target_media_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("Media-list target set must be explicit and nonempty")
        if definition.get("retrieval_requirement") not in {"all", "any"}:
            raise ValueError("Missing retrieval requirement")


def scoring_options(value: object = None) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "open_scale": "ternary",
        "choice_semantic_aux": False,
        "number_semantic_aux": True,
    }
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("Invalid evaluation_options")
    result = defaults | value
    if result["open_scale"] not in {"ternary", "binary"} or any(
        type(result[k]) is not bool for k in ("choice_semantic_aux", "number_semantic_aux")
    ):
        raise ValueError("Invalid evaluation option value")
    return result


def media_key(ref: Mapping[str, Any]) -> str:
    kind = str(ref.get("media_type") or "image")
    name = PurePosixPath(str(ref.get("media_id") or ref.get("file_name") or "")).name
    return kind + ":" + (PurePosixPath(name).stem if kind == "video" else name).casefold()


def media_set_metrics(
    target: Sequence[Mapping[str, Any]], prediction: Sequence[Mapping[str, Any]], *, available: bool = True
) -> dict[str, Any]:
    gt, pred = {media_key(x) for x in target}, {media_key(x) for x in prediction}
    common = gt & pred
    recall = len(common) / len(gt) if gt and available else None
    precision = len(common) / len(pred) if pred and available else (0.0 if gt and available else None)
    return {
        "status": "available" if available else "unavailable",
        "recall": recall,
        "precision": precision,
        "f1": 2 * len(common) / (len(gt) + len(pred)) if gt and available else None,
        "jaccard": len(common) / len(gt | pred) if gt and available else None,
        "target": sorted(gt),
        "predicted": sorted(pred),
        "intersection": sorted(common),
        "missing": sorted(gt - pred),
        "extra": sorted(pred - gt),
        "matched": len(common),
        "gt": len(gt),
        "predicted_count": len(pred),
    }


def result_record(
    definition: Mapping[str, Any],
    score: float | int | None,
    *,
    reason: str = "",
    status: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    maximum = definition.get("max_score", 1)
    return {
        "version": VERSION,
        "scorer": definition["scorer"],
        **({"source_qtype": definition["source_qtype"]} if definition.get("source_qtype") else {}),
        "answer_format": definition["answer_format"],
        "max_score": maximum,
        "score": score,
        "normalized_score": score / maximum if score is not None else None,
        "judge_status": status or ("completed" if score is not None else "failed"),
        "reason": reason,
        **details,
    }


def choice_result(definition: Mapping[str, Any], parsed: Mapping[str, Any]) -> dict[str, Any]:
    options = definition["options"]
    ids = {o["id"] for o in options}
    comparisons = parsed.get("comparisons")
    if (
        not isinstance(comparisons, list)
        or len(comparisons) != len(ids)
        or {x.get("option_id") for x in comparisons if isinstance(x, dict)} != ids
    ):
        raise ValueError("Judge must compare every provided option exactly once")
    if any(type(x.get("matches")) is not bool or not str(x.get("reason") or "").strip() for x in comparisons):
        raise ValueError("Every option requires a semantic comparison and reason")
    status = parsed.get("status")
    matches = {x["option_id"] for x in comparisons if x["matches"]}
    if status == "ambiguous_options":
        if len(matches) < 2:
            raise ValueError("Overlapping options require at least two matching comparisons")
        return result_record(
            definition,
            None,
            status="needs_review",
            reason=str(parsed.get("reason") or "overlapping_options"),
            comparisons=comparisons,
        )
    if status == "unable_to_judge":
        return result_record(
            definition, None, reason=str(parsed.get("reason") or "unable_to_judge"), comparisons=comparisons
        )
    matched = parsed.get("matched_option")
    valid_status = status == "matched" or (status == "other" and matched == "other")
    if not valid_status or matched not in ids | {"other"} or not str(parsed.get("reason") or "").strip():
        raise ValueError("Invalid option match")
    if (matched == "other" and matches) or (matched != "other" and matches != {matched}):
        raise ValueError("Option selection contradicts per-option comparisons")
    return result_record(
        definition,
        int(matched == definition["correct_option"]),
        reason=parsed["reason"],
        matched_option=matched,
        comparisons=comparisons,
    )


def evaluation_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [x for x in items if x.get("evaluation") or (x.get("evaluation_result") or {}).get("scorer")]
    scored = [x for x in eligible if (x.get("evaluation_result") or {}).get("normalized_score") is not None]

    def mean(rows: Sequence[Mapping[str, Any]]) -> float | None:
        scores = [(x.get("evaluation_result") or {}).get("normalized_score") for x in rows]
        valid = [s for s in scores if isinstance(s, (int, float))]
        return sum(valid) / len(valid) if valid else None

    def question_type(item: Mapping[str, Any]) -> str:
        definition = item.get("evaluation") or item.get("evaluation_result") or {}
        # 原始题集允许不标题型；评分结果仍须可汇总和落盘。
        return str(definition.get("source_qtype") or definition.get("answer_format") or "unclassified")

    groups = sorted({question_type(item) for item in eligible})
    by_type = {}
    for kind in groups:
        rows = [x for x in eligible if question_type(x) == kind]
        by_type[kind] = {
            "count": len(rows),
            "mean": mean(rows),
            "scored": sum(
                (x.get("evaluation_result") or {}).get("normalized_score") is not None for x in rows
            ),
        }
    media = {}
    for collection in ("delivery", "tools"):
        values = [
            ((x.get("evaluation_result") or {}).get("media_metrics") or {}).get(collection) or {}
            for x in eligible
        ]
        media[collection] = {
            key: sum(v[key] for v in values if isinstance(v.get(key), (int, float)))
            / sum(isinstance(v.get(key), (int, float)) for v in values)
            if any(isinstance(v.get(key), (int, float)) for v in values)
            else None
            for key in ("recall", "precision", "f1", "jaccard")
        }
    return {
        "version": VERSION,
        "count": len(eligible),
        "scored": len(scored),
        "coverage": len(scored) / len(eligible) if eligible else None,
        "mean": mean(eligible),
        "complete_mean": mean(eligible) if len(scored) == len(eligible) else None,
        "by_type": by_type,
        "scales": sorted(
            {
                str(
                    (x.get("evaluation_result") or {}).get(
                        "max_score", (x.get("evaluation") or {}).get("max_score", 2)
                    )
                )
                for x in eligible
            }
        ),
        "other_count": sum(
            (x.get("evaluation_result") or {}).get("matched_option") == "other" for x in eligible
        ),
        "media_metrics": media,
        "number_disagreement_ids": [
            x.get("qa_id")
            for x in eligible
            if (x.get("evaluation_result") or {}).get("number_aux_disagrees") is True
        ],
    }
