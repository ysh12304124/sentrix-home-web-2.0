"""文档记录校验及来源适配。ATM 的一封邮件始终对应一份原文。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sentrix.application.media.import_assets import ImportRequest
from sentrix.domain.media.models import MediaType
from sentrix.domain.media.source_metadata import SourceMetadata


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    external_source_id: str
    text: str
    document_type: str = "document"
    source_timestamp: str | None = None
    title: str = ""
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) for value in (self.external_source_id, self.text, self.title, self.summary)
        ):
            raise ValueError("来源ID、正文、标题和摘要需为字符串")
        if (
            not self.external_source_id.strip()
            or len(self.external_source_id) > 200
            or "\x00" in self.external_source_id
        ):
            raise ValueError("external_source_id 必须为 1–200 字符")
        if self.document_type not in {"email", "document"}:
            raise ValueError("document_type 必须为 email 或 document")
        if not self.text.strip() or "\x00" in self.text:
            raise ValueError("文档正文不能为空或含 NUL")
        if self.source_timestamp is not None:
            if not isinstance(self.source_timestamp, str):
                raise ValueError("source_timestamp 需为字符串")
            if len(self.source_timestamp) <= 10:
                raise ValueError("source_timestamp 需包含时间")
            datetime.fromisoformat(self.source_timestamp)

    def import_request(self, scope_id: str) -> ImportRequest:
        return ImportRequest(
            scope_id=scope_id,
            file_name=self.external_source_id + ".txt",
            content_type="text/plain; charset=utf-8",
            media_type=MediaType.TEXT,
            content=self.text.encode("utf-8"),
            external_source_id=self.external_source_id,
            source_timestamp=self.source_timestamp,
            source_metadata=SourceMetadata(
                document_type=self.document_type,
                title=self.title,
                summary=self.summary,
                metadata=self.metadata,
            ),
        )


def parse_document_package(content: bytes, file_name: str, format: str = "auto") -> list[DocumentRecord]:
    text = content.decode("utf-8-sig")
    if file_name.lower().endswith((".txt", ".md")):
        return [DocumentRecord(external_source_id=file_name, text=text, title=file_name)]
    if file_name.lower().endswith(".jsonl"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = json.loads(text)
    if not isinstance(rows, list):
        raise ValueError("文档包需为 JSON 数组或 JSONL")
    records: list[DocumentRecord] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index + 1} 条文档必须为对象")
        is_atm = format == "atm_email" or (
            format == "auto" and {"id", "timestamp", "short_summary", "detail"} <= row.keys()
        )
        if is_atm:
            if set(row) - {"id", "timestamp", "short_summary", "detail"}:
                raise ValueError(f"第 {index + 1} 条不是 ATM 邮件材料；QA 不可导入记忆")
            match = re.search(r"^Subject:\s*(.+)$", row["detail"], re.MULTILINE)
            record = DocumentRecord(
                external_source_id=row["id"],
                text=row["detail"],
                document_type="email",
                source_timestamp=row["timestamp"],
                title=match.group(1).strip() if match else "",
                summary=row["short_summary"],
                metadata={"source_dataset": "atm-bench", "original_record": row},
            )
        else:
            record = DocumentRecord(**row)
        if record.external_source_id in seen:
            raise ValueError(f"重复来源 ID: {record.external_source_id}")
        seen.add(record.external_source_id)
        # 包来源单独记录；允许将失败子集重试，身份比较不依赖所在包的顺序或 SHA。
        records.append(record)
    return records
