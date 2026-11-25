"""Email retrieval and cited-source metrics from recorded facts, never answer-string guesses."""

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from photobench.domain.evaluation.contract import media_set_metrics


def _eligible(row: Mapping[str, Any]) -> bool:
    return (row.get("evaluation") or {}).get("media_metrics_policy") != "exclude" and row.get(
        "answerability"
    ) not in {"unanswerable", "insufficient_evidence"}


def text_retrieval_metrics(
    row: Mapping[str, Any],
    turns: Sequence[Mapping[str, Any]],
    *,
    tool_trace: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    gt = [r for r in row.get("retrieval_media_refs", ()) if r.get("media_type") == "email"]
    if not gt or not _eligible(row):
        return None
    traces = [t.get("trace_bundle", t.get("trace", {})) or {} for t in turns]
    observed = bool(traces) and all(t.get("text_retrieval", {}).get("version") == 1 for t in traces)
    available = observed
    ranked: dict[str, dict[str, str]] = {}
    # Legacy search is also a real retrieval. Recover only server-recorded
    # previews, never source IDs mentioned in generated answer prose.
    for call in tool_trace:
        if call.get("tool") not in {"search_memories", "search_text_memories", "get_result_page"}:
            continue
        result = call.get("result") or {}
        if call.get("status") != "ok":
            continue
        available = True
        for preview in result.get("preview", ()):
            if preview.get("media_kind") != "text":
                continue
            identifier = preview.get("evidence_id") or PurePosixPath(preview.get("file_name") or "").stem
            if identifier:
                ranked.setdefault(str(identifier), {"media_type": "email", "media_id": str(identifier)})
    for trace in traces:
        for source in trace.get("text_retrieval", {}).get("sources", []):
            if source.get("source_type") == "email" and source.get("evidence_id"):
                identifier = str(source["evidence_id"])
                ranked.setdefault(identifier, {"media_type": "email", "media_id": identifier})
    citations = [
        {"media_type": "email", "media_id": s["evidence_id"]}
        for t in traces[-1:]
        for s in t.get("text_evidence", ())
        if s.get("source_type") == "email" and s.get("evidence_id")
    ]
    retrieval = media_set_metrics(gt, list(ranked.values()), available=available)
    cited_gt = gt
    cited_gt = [r for r in cited_gt if r.get("media_type") == "email"]
    return {
        "version": 1,
        "retrieved_evidence_ids": list(ranked),
        "cited_evidence_ids": list(dict.fromkeys(s["media_id"] for s in citations)),
        "retrieval": retrieval,
        "citation": media_set_metrics(cited_gt, citations, available=observed),
        "recall_at_k": {
            str(k): media_set_metrics(gt, list(ranked.values())[:k], available=available)["recall"]
            for k in (1, 5, 10)
        },
        "reason": "" if available else "text_retrieval_not_recorded_or_failed",
    }


def project_text_metrics(item: Mapping[str, Any]) -> dict[str, Any]:
    """Put recorded email evidence into the existing candidate/delivery counters."""
    refs = item.get("retrieval_media_refs") or []
    if not refs or any(r.get("media_type") != "email" for r in refs):
        return dict(item)
    turns = item.get("runtime_turns") or []
    metrics = (
        text_retrieval_metrics(item, turns, tool_trace=item.get("tool_trace") or [])
        if turns
        else item.get("text_retrieval_metrics")
    )
    if not metrics:
        return dict(item)
    result = {**item, "text_retrieval_metrics": metrics}
    for group, prefix in (("retrieval", "candidate_retrieval"), ("citation", "media_retrieval")):
        values = metrics[group]
        if values["status"] != "available":
            continue
        result[prefix + "_counts"] = {
            "gt": values["gt"],
            "predicted": values["predicted_count"],
            "matched": values["matched"],
            **{k: values[k] for k in ("precision", "recall", "f1")},
        }
        for key in ("precision", "recall", "f1"):
            result[prefix + "_" + key] = values[key]
            if group == "citation":
                result["retrieval_" + key] = values[key]
    return result
