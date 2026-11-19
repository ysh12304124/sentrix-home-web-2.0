"""One text-memory contract; provenance labels never choose a processing pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TextMemoryItem:
    external_id: str
    timestamp: str
    summary: str
    body: str
    metadata: dict[str, object]

    @classmethod
    def parse(cls, value: dict[str, object]) -> TextMemoryItem:
        body = value.get("text")
        if not isinstance(body, str) or not body.strip() or len(body) > 200_000:
            raise ValueError("text 必须是非空文本且不超过200000字符")
        stamp = value.get("recorded_at", "")
        if stamp is None:
            stamp = ""
        if not isinstance(stamp, str):
            raise ValueError("recorded_at 必须为 ISO 时间字符串")
        if stamp:
            datetime.fromisoformat(stamp)
        for key in ("title", "summary"):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise ValueError("title/summary 必须为文本")
        summary = value.get("summary") or value.get("title") or body.strip().splitlines()[0][:200]
        if not isinstance(summary, str) or len(summary) > 10_000:
            raise ValueError("title/summary 必须为文本且不超过10000字符")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict) or len(json.dumps(metadata, ensure_ascii=False)) > 16_000:
            raise ValueError("metadata 必须为不超过16000字符的 JSON 对象")
        metadata = dict(metadata)
        origin = metadata.setdefault("source_type", "unknown")
        if not isinstance(origin, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", origin):
            raise ValueError("metadata.source_type 必须为简短来源标签，例如 email/note/document")
        identifier = value.get("external_id")
        if identifier is None:
            identifier = (
                "text_"
                + hashlib.sha256(
                    json.dumps(
                        [stamp, summary.strip(), body.strip(), metadata], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()[:24]
            )
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", identifier
        ):
            raise ValueError("external_id 必须是安全的字母、数字、点、下划线或短横线标识")
        return cls(identifier, stamp, summary.strip(), body.strip(), metadata)

    @property
    def fingerprint(self) -> str:
        # Preserve old email source fingerprints during idempotent reimport.
        return hashlib.sha256(
            json.dumps(
                [self.external_id, self.timestamp, self.summary, self.body], ensure_ascii=False
            ).encode()
        ).hexdigest()


class TextMemoryRepository(Protocol):
    def find(self, scope_id: str, external_id: str) -> dict[str, object] | None: ...
    def insert(self, scope_id: str, item: TextMemoryItem, path: str) -> dict[str, object]: ...
    def finish(self, asset_id: str, revision: int, error: str) -> None: ...


def text_asset_id(scope_id: str, external_id: str) -> str:
    return "text_" + hashlib.sha256((scope_id + "\0" + external_id).encode()).hexdigest()[:24]
