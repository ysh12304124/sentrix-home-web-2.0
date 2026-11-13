"""Exact typed evidence subtraction. Identity endpoints are never source evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sentrix.application.media.analyze_deletion import DependencyAnalysis, parse_json


@dataclass(frozen=True)
class Withdrawal:
    kept: list[Any] | None
    removed: list[dict[str, Any]]
    valid_count: int
    unresolved: bool
    has_invalid_remaining: bool

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def withdraw(value: object, default_kind: str, analysis: DependencyAnalysis) -> Withdrawal:
    parsed, valid = parse_json(value)
    if not valid or not isinstance(parsed, list):
        return Withdrawal(None, [], 0, True, False)
    kept, removed = [], []
    valid_ids: set[tuple[str, str]] = set()
    unresolved, invalid = False, False
    for entry in parsed:
        if isinstance(entry, str):
            kind, identity = default_kind, entry
        elif (
            isinstance(entry, dict)
            and isinstance(entry.get("kind"), str)
            and isinstance(entry.get("id"), str)
        ):
            kind, identity = entry["kind"], entry["id"]
        else:
            kept.append(entry)
            unresolved = True
            continue
        assets, unknown = analysis.resolve(kind, identity)
        # A shared event reference is not equivalent to an individual observation.
        # Removing the entire reference would also remove other materials' evidence.
        removable = (
            kind not in {"entity", "person", "face_cluster"}
            and assets
            and assets <= analysis.targets
            and not unknown
        )
        if removable:
            removed.append({"kind": kind, "id": identity, "original": entry})
            continue
        kept.append(entry)
        all_valid = bool(assets) and all(analysis.valid_asset(aid) for aid in assets)
        if all_valid and not unknown:
            valid_ids.add((kind, identity))
        else:
            invalid = True
        unresolved |= unknown or not bool(assets)
    return Withdrawal(kept, removed, len(valid_ids), unresolved, invalid)


def encode_key(key: dict[str, Any]) -> str:
    return json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
