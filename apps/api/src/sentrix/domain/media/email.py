"""Email is a text source, not a picture or a new graph node type."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailItem:
    external_id: str
    timestamp: str
    summary: str
    body: str

    @classmethod
    def parse(cls, value: dict[str, object]) -> EmailItem:
        fields = [value.get(key) for key in ("id", "timestamp", "short_summary", "detail")]
        if any(not isinstance(v, str) or not v.strip() for v in fields):
            raise ValueError("邮件必须包含非空 id/timestamp/short_summary/detail 字符串")
        identifier, stamp, summary, body = (str(v).strip() for v in fields)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", identifier):
            raise ValueError("邮件 ID 只能包含字母、数字、下划线、点和短横线")
        if len(identifier) > 200 or len(body) > 200_000 or len(summary) > 10_000:
            raise ValueError("邮件字段超过长度限制")
        datetime.fromisoformat(stamp)
        return cls(identifier, stamp, summary, body)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.external_id, self.timestamp, self.summary, self.body], ensure_ascii=False
            ).encode()
        ).hexdigest()


class EmailRepository(Protocol):
    def find(self, scope_id: str, external_id: str) -> dict[str, object] | None: ...
    def insert(self, scope_id: str, item: EmailItem, path: str) -> dict[str, object]: ...
    def finish(self, asset_id: str, revision: int, error: str) -> None: ...


def email_asset_id(scope_id: str, external_id: str) -> str:
    from sentrix.domain.media.text_memory import text_asset_id

    return text_asset_id(scope_id, external_id)
