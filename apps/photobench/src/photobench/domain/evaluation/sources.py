"""邮件/文档来源单独计算，不混入图片和视频分母。"""

from collections.abc import Mapping, Sequence
from typing import Any


def source_key(ref: Mapping[str, Any]) -> str:
    return str(ref["source_type"]) + ":" + str(ref["source_id"])


def source_set_metrics(
    target: Sequence[Mapping[str, Any]], predicted: Sequence[Mapping[str, Any]], *, available: bool = True
) -> dict[str, Any]:
    gt, pred = {source_key(r) for r in target}, {source_key(r) for r in predicted}
    hits = gt & pred
    return {
        "status": ("available" if available else "unavailable") if gt else "not_applicable",
        "target": sorted(gt),
        "predicted": sorted(pred),
        "intersection": sorted(hits),
        "missing": sorted(gt - pred),
        "extra": sorted(pred - gt),
        "matched": len(hits),
        "gt": len(gt),
        "recall": len(hits) / len(gt) if gt and available else None,
        "precision": len(hits) / len(pred)
        if gt and pred and available
        else (0.0 if gt and available else None),
        "f1": 2 * len(hits) / (len(gt) + len(pred)) if gt and available else None,
        "jaccard": len(hits) / len(gt | pred) if gt and available else None,
        "all": bool(gt <= pred) if gt and available else None,
        "any": bool(hits) if gt and available else None,
    }


def resolve_sources(
    asset_ids: Sequence[str], assets: Mapping[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    by_id = {str(a["id"]): a for group in assets.values() for a in group if a.get("id")}
    result = []
    for identifier in dict.fromkeys(asset_ids):
        asset = by_id.get(identifier)
        if not asset or asset.get("media_type") != "text":
            continue
        metadata = asset.get("source_metadata") or {}
        result.append(
            {
                "source_type": metadata.get("document_type") or "document",
                "source_id": asset.get("external_source_id") or identifier,
                "asset_id": identifier,
                "file_name": asset.get("file_name"),
                "title": metadata.get("title", ""),
                "summary": metadata.get("summary", ""),
                "source_timestamp": asset.get("captured_at"),
            }
        )
    return result


def source_mapping_complete(result: Mapping[str, Any], assets: Mapping[str, list[dict[str, Any]]]) -> bool:
    trace = result.get("trace") or {}
    if "source_reads" not in trace:
        return False
    known = {str(asset["id"]) for group in assets.values() for asset in group if asset.get("id")}
    identifiers = {
        str(item["asset_id"])
        for item in [*(result.get("attachments") or []), *(trace.get("source_reads") or [])]
        if item.get("asset_id")
    }
    identifiers.update(str(identifier) for identifier in result.get("retrieved_candidates") or [])
    return identifiers <= known


def source_metrics_for_record(row: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    evidence = record.get("retrieval_source_refs", row.get("retrieval_source_refs")) or []
    answer_evidence = (
        record.get("answer_evidence_source_refs", row.get("answer_evidence_source_refs")) or evidence
    )
    return {
        "candidates": source_set_metrics(
            evidence,
            record.get("retrieved_source_refs") or [],
            available=record.get("source_mapping_complete") is True,
        ),
        "reads": source_set_metrics(
            answer_evidence,
            record.get("read_source_refs") or [],
            available=record.get("source_mapping_complete") is True,
        ),
        "citations": source_set_metrics(
            answer_evidence,
            record.get("predicted_source_refs") or [],
            available=record.get("source_mapping_complete") is True,
        ),
    }
