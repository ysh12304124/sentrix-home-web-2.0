"""Email retrieval and cited-source metrics from recorded facts, never answer-string guesses."""

from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.contract import media_set_metrics


def _eligible(row: Mapping[str, Any]) -> bool:
    return (row.get("evaluation") or {}).get("media_metrics_policy") != "exclude" and row.get(
        "answerability"
    ) not in {"unanswerable", "insufficient_evidence"}


def text_retrieval_metrics(
    row: Mapping[str, Any], turns: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    gt = [r for r in row.get("retrieval_media_refs", ()) if r.get("media_type") == "email"]
    if not gt or not _eligible(row):
        return None
    traces = [t.get("trace_bundle", t.get("trace", {})) or {} for t in turns]
    observed = bool(traces) and all(t.get("text_retrieval", {}).get("version") == 1 for t in traces)
    available = observed and all(t["text_retrieval"].get("complete") is True for t in traces)
    ranked: dict[str, dict[str, str]] = {}
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
    cited_gt = row.get("answer_evidence_media_refs") or gt
    cited_gt = [r for r in cited_gt if r.get("media_type") == "email"]
    return {
        "version": 1,
        "retrieved_evidence_ids": list(ranked),
        "cited_evidence_ids": list(dict.fromkeys(s["media_id"] for s in citations)),
        "retrieval": retrieval,
        "citation": media_set_metrics(
            cited_gt, citations, available=observed and traces[-1].get("text_citations_recorded") is True
        ),
        "recall_at_k": {
            str(k): media_set_metrics(gt, list(ranked.values())[:k], available=available)["recall"]
            for k in (1, 5, 10)
        },
        "reason": "" if available else "text_retrieval_not_recorded_or_failed",
    }


def aggregate_text_retrieval(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [
        i
        for i in items
        if _eligible(i) and any(r.get("media_type") == "email" for r in i.get("retrieval_media_refs", ()))
    ]
    if not eligible:
        return {}
    values = [i["text_retrieval_metrics"] for i in eligible if i.get("text_retrieval_metrics")]
    result: dict[str, Any] = {"question_count": len(eligible), "recorded_count": len(values)}
    for group in ("retrieval", "citation"):
        rows = [m[group] for m in values if m[group]["status"] == "available"]
        gt = sum(m["gt"] for m in rows)
        pred = sum(m["predicted_count"] for m in rows)
        matched = sum(m["matched"] for m in rows)
        result[group] = {
            "sample_count": len(rows),
            "gt": gt,
            "predicted_count": pred,
            "matched": matched,
            "recall": matched / gt if gt else None,
            "precision": matched / pred if pred else 0.0 if rows else None,
            "f1": 2 * matched / (gt + pred) if gt + pred else None,
        }
    result["recall_at_k"] = {
        str(k): sum(v) / len(v)
        if (v := [m["recall_at_k"][str(k)] for m in values if m["recall_at_k"][str(k)] is not None])
        else None
        for k in (1, 5, 10)
    }
    return {"text_retrieval_summary": result}
