"""Read-only comparison of answer-text IDs with frozen list scoring facts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from photobench.domain.evaluation.media import delivered_media_aliases, tool_media_alias_resolution
from photobench.domain.evaluation.profiles import stable_media_key, tool_list_score, valid_result

_TOKEN = re.compile(
    r"(?<![a-zA-Z0-9_])(?:asset_[a-zA-Z0-9]+|(?:photo|image|video)_\d+|"
    r"\d{8}_\d{6}(?:\.(?:jpe?g|png|heic|webp|mp4|mov|mkv|webm))?)(?![a-zA-Z0-9_])",
    re.IGNORECASE,
)
_RANGE = re.compile(
    r"(?:photo|image|video)_\d+[`\s]*(?:到|至|through|to|[-~—–])[`\s]*"
    r"(?:photo|image|video)_\d+",
    re.IGNORECASE,
)


def list_answer_comparison(
    definition: Mapping[str, Any], item: Mapping[str, Any], index: int, value: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Never promotes mentioned IDs to delivery, modifies scores, or consults GT to resolve IDs."""
    if definition.get("scorer") not in {"media_set", "atm_list"} or value.get("purpose") != "list":
        return None
    turns = item.get("runtime_turns") or [item]
    record = turns[index] if 0 <= index < len(turns) else {}
    aliases, unresolved = tool_media_alias_resolution(item, index)
    mapping: dict[str, set[str]] = {}

    def add(token: str, key: str) -> None:
        mapping.setdefault(token.casefold(), set()).add(key)

    for ref in [
        *(item.get("retrieved_candidate_media") or []),
        *(ref for turn in turns[: index + 1] for ref in turn.get("predicted_media") or []),
    ]:
        if ref.get("media_type") not in {"image", "video"} or not ref.get("media_id"):
            continue
        key = stable_media_key(ref)
        for name in (ref.get("media_id"), ref.get("file_name")):
            if name:
                add(PurePosixPath(str(name)).name, key)
                add(PurePosixPath(str(name)).stem, key)
        if ref.get("asset_id"):
            add(str(ref["asset_id"]), key)
    for alias in [*aliases, *delivered_media_aliases(record)]:
        add(alias["handle"], stable_media_key(alias))

    answer = record.get("answer")
    text = answer if isinstance(answer, str) else ""
    tokens = list(dict.fromkeys(match.group() for match in _TOKEN.finditer(text)))
    ranges = list(dict.fromkeys(match.group() for match in _RANGE.finditer(text)))
    predicted: set[str] = set()
    matches = []
    unresolved_tokens = []
    blocked = {handle.casefold() for handle in unresolved}
    for token in tokens:
        refs = mapping.get(token.casefold(), set())
        if len(refs) == 1 and token.casefold() not in blocked:
            key = next(iter(refs))
        elif not refs and re.fullmatch(r"\d{8}_\d{6}\.(?:jpe?g|png|heic|webp|mp4|mov|mkv|webm)", token, re.I):
            suffix = PurePosixPath(token).suffix.casefold()
            kind = "video" if suffix in {".mp4", ".mov", ".mkv", ".webm"} else "image"
            key = stable_media_key({"media_type": kind, "media_id": token})
        else:
            unresolved_tokens.append(token)
            continue
        predicted.add(key)
        matches.append({"token": token, "media_key": key})
    target = set(value.get("target") or [])
    available = bool(target and tokens and not unresolved_tokens and not ranges and isinstance(answer, str))
    return {
        "version": "answer-text-ids-v1",
        "diagnostic_only": True,
        "model_delivery_jaccard": value.get("score") if valid_result(value) else None,
        "tool_candidate_jaccard": tool_list_score(definition, value),
        "answer_text": {
            "status": "available" if available else "unavailable",
            "score": len(target & predicted) / len(target | predicted) if available else None,
            "target": sorted(target),
            "predicted": sorted(predicted),
            "intersection": sorted(target & predicted),
            "matches": matches,
            "unresolved_tokens": unresolved_tokens,
            "unsupported_ranges": ranges,
            "reason": "range_not_expanded"
            if ranges
            else "unresolved_ids"
            if unresolved_tokens
            else "no_explicit_media_ids"
            if not tokens
            else "missing_frozen_target"
            if not target
            else "explicit_ids",
        },
    }
