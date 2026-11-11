"""Human scoring audits never overwrite scoring results."""

from collections.abc import Mapping
from typing import Any

from photobench.domain.evaluation.profiles import digest, result_key

VERDICTS = {
    "needs_review",
    "confirmed_misgrade",
    "reason_error_score_unchanged",
    "dataset_review",
    "confirmed_score",
}


def scoring_review(state: Mapping[str, Any], raw: Mapping[str, Any], at: str) -> dict[str, Any]:
    qa, batch, profile = (str(raw.get(k) or "") for k in ("qa_id", "batch_id", "profile_id"))
    turn = raw.get("turn_index")
    if type(turn) is not int or turn < 0:
        raise ValueError("Review requires a valid turn_index")
    key = result_key(batch, qa, turn)
    result = (state.get("scoring_results") or {}).get(key)
    if not result or result.get("profile_id") != profile:
        raise ValueError("Review must refer to an existing standard, batch, QA and turn")
    if not raw.get("answer_fingerprint") or raw["answer_fingerprint"] != result.get("answer_fingerprint"):
        raise ValueError("Review answer fingerprint does not match frozen result")
    verdict = str(raw.get("verdict") or "")
    note, reviewer = str(raw.get("note") or "").strip(), str(raw.get("reviewer") or "").strip()
    if verdict not in VERDICTS or not note or not reviewer or len(note) > 10000 or len(reviewer) > 200:
        raise ValueError("Review requires a valid verdict, evidence note and reviewer")
    calls = [
        r["call_id"]
        for attempt in [*(result.get("history") or []), result]
        for r in attempt.get("requests") or []
        if r.get("call_id")
    ]
    value = {
        "qa_id": qa,
        "profile_id": profile,
        "batch_id": batch,
        "turn_index": turn,
        "answer_fingerprint": raw["answer_fingerprint"],
        "verdict": verdict,
        "note": note,
        "reviewer": reviewer,
        "updated_at": at,
        "result_key": key,
        "result_fingerprint": digest(result),
        "request_call_ids": calls,
        "score_unchanged": True,
    }
    return {**value, "review_id": digest(value)}
