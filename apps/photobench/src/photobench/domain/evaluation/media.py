"""Media measurements from recorded delivery and retrieval facts, without Judge calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.contract import media_set_metrics
from photobench.domain.evaluation.profiles import stable_media_key


def delivered_media_aliases(record: Mapping[str, Any]) -> list[dict[str, str]]:
    """Resolve recorded handles by asset identity, without guessing from list order."""
    by_asset: dict[str, set[tuple[str, str]]] = {}
    for ref in record.get("predicted_media") or []:
        if ref.get("asset_id") and ref.get("media_type") in {"image", "video"} and ref.get("media_id"):
            kind, identifier = stable_media_key(ref).split(":", 1)
            by_asset.setdefault(str(ref["asset_id"]), set()).add((kind, identifier))
    aliases: dict[str, set[tuple[str, str]]] = {}
    unresolved: set[str] = set()
    for attachment in record.get("attachments") or []:
        handle, asset_id = attachment.get("handle"), attachment.get("asset_id")
        if isinstance(handle, str) and handle.strip() and asset_id is not None:
            refs = by_asset.get(str(asset_id), set())
            if not refs:
                unresolved.add(handle)
            aliases.setdefault(handle, set()).update(refs)
        elif isinstance(handle, str):
            unresolved.add(handle)
    return [
        {"handle": handle, "media_type": kind, "media_id": identifier}
        for handle, refs in aliases.items()
        if len(refs) == 1 and handle not in unresolved
        for kind, identifier in refs
    ]


def media_metrics_for_sets(
    definition: Mapping[str, Any],
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    retrieved: Sequence[Mapping[str, Any]],
    *,
    retrieval_available: bool,
) -> dict[str, Any]:
    if definition.get("media_metrics_policy") == "exclude":
        return {
            name: {
                **media_set_metrics([], [], available=False),
                "status": "not_applicable",
                "reason": "excluded_by_evaluation_policy",
            }
            for name in ("delivery", "tools")
        }
    evidence = record.get("retrieval_media_refs", row.get("retrieval_media_refs")) or []
    target = definition.get("target_media_refs", evidence)
    prediction = record.get("predicted_media")
    return {
        "delivery": media_set_metrics(
            target,
            prediction or [],
            available=isinstance(prediction, list) and record.get("media_mapping_complete") is True,
        ),
        "tools": media_set_metrics(evidence, retrieved, available=retrieval_available),
    }


def saved_media_metrics(row: Mapping[str, Any], item: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Recover only mappings present in the saved answer/trace; missing facts remain unavailable."""
    turns = item.get("runtime_turns") or [item]
    record = turns[index] if 0 <= index < len(turns) else {}
    refs = [
        *(item.get("retrieved_candidate_media") or []),
        *(item.get("predicted_media") or []),
        *(ref for turn in turns for ref in turn.get("predicted_media") or []),
    ]
    by_asset = {
        str(ref[field]): ref
        for ref in refs
        for field in ("asset_id", "source_keyframe_asset_id")
        if ref.get(field)
    }
    observations = [(turn.get("retrieval_observations") or {}) for turn in turns[: index + 1]]
    ids = list(
        dict.fromkeys(str(i) for observation in observations for i in observation.get("asset_ids", []))
    )
    return media_metrics_for_sets(
        row.get("evaluation") or {},
        row,
        record,
        [by_asset[i] for i in ids if i in by_asset],
        retrieval_available=all(o.get("complete") is True for o in observations)
        and all(i in by_asset for i in ids),
    )
