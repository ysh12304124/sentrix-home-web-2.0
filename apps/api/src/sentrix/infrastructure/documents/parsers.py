from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias
from xml.etree import ElementTree

from charset_normalizer import from_bytes

from sentrix.domain.documents.models import DocumentChunk, DocumentElement, ParsedDocument

PARSER_VERSION = "sentrix-document-parser/2"
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 1000
MAX_CHUNK_CHARS = 2200
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DocumentLocator: TypeAlias = dict[str, int | str]
DocumentRecord: TypeAlias = tuple[str, str, int | None, str, DocumentLocator, float]


class DocumentFormatError(ValueError):
    """The upload is unsupported or its declared format does not match its bytes."""


class DeterministicDocumentParser:
    """Parse TXT, DOCX and text-bearing PDF without generating model-authored facts."""

    def parse(
        self,
        *,
        asset_id: str,
        scope_id: str,
        path: Path,
        file_name: str,
        content_type: str | None,
        revision: int,
    ) -> ParsedDocument:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".doc":
            raise DocumentFormatError("旧式 .doc 暂不支持，请先转换为 .docx")
        if suffix not in {".txt", ".docx", ".pdf"}:
            raise DocumentFormatError(f"不支持的文档格式: {suffix or '无扩展名'}")
        raw = path.read_bytes()
        self._validate_signature(suffix, raw, content_type)
        if suffix == ".txt":
            records = self._txt(raw)
        elif suffix == ".docx":
            records = self._docx(path)
        else:
            records = self._pdf(path)
        elements = tuple(
            DocumentElement(
                id=f"de_{asset_id}_{revision}_{ordinal:06d}",
                document_asset_id=asset_id,
                revision=revision,
                ordinal=ordinal,
                kind=kind,
                text=text,
                source_locator=json.dumps(locator, ensure_ascii=False, sort_keys=True),
                parser_version=PARSER_VERSION,
                page_no=page_no,
                section_path=section,
                confidence=confidence,
            )
            for ordinal, (kind, text, page_no, section, locator, confidence) in enumerate(records)
            if text.strip()
        )
        if not elements:
            raise DocumentFormatError("文档没有可索引文字；扫描 PDF 需要下一阶段的 OCR")
        return ParsedDocument(
            format=suffix[1:],
            parser_version=PARSER_VERSION,
            elements=elements,
            chunks=self._chunks(asset_id, scope_id, revision, elements),
        )

    @staticmethod
    def _validate_signature(suffix: str, raw: bytes, content_type: str | None) -> None:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        allowed = {
            ".txt": {"", "text/plain", "application/octet-stream"},
            ".docx": {
                "",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/octet-stream",
                "application/zip",
            },
            ".pdf": {"", "application/pdf", "application/octet-stream"},
        }[suffix]
        if mime not in allowed:
            raise DocumentFormatError(f"MIME 与 {suffix} 不一致: {mime}")
        if suffix == ".pdf" and not raw.startswith(b"%PDF-"):
            raise DocumentFormatError("PDF 文件签名无效")
        if suffix == ".docx" and not raw.startswith(b"PK"):
            raise DocumentFormatError("DOCX 文件签名无效")

    @staticmethod
    def _txt(raw: bytes) -> list[DocumentRecord]:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        elif raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            match = from_bytes(raw).best()
            if match is None or (match.percent_chaos or 0) > 20:
                raise DocumentFormatError("无法可靠识别 TXT 编码")
            text = str(match)
        records: list[DocumentRecord] = []
        paragraph: list[str] = []
        offset = 0
        start_line = 1
        start_offset = 0
        lines = text.splitlines(keepends=True)
        for line_no, line in enumerate(lines, 1):
            clean = line.strip()
            if clean:
                if not paragraph:
                    start_line = line_no
                    start_offset = offset
                paragraph.append(clean)
            elif paragraph:
                value = "\n".join(paragraph)
                records.append(
                    (
                        "paragraph",
                        value,
                        None,
                        "",
                        {
                            "line_start": start_line,
                            "line_end": line_no - 1,
                            "char_start": start_offset,
                            "char_end": offset,
                        },
                        1.0,
                    )
                )
                paragraph = []
            offset += len(line)
        if paragraph:
            records.append(
                (
                    "paragraph",
                    "\n".join(paragraph),
                    None,
                    "",
                    {
                        "line_start": start_line,
                        "line_end": len(lines),
                        "char_start": start_offset,
                        "char_end": len(text),
                    },
                    1.0,
                )
            )
        return records

    @staticmethod
    def _docx(path: Path) -> list[DocumentRecord]:
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise DocumentFormatError("DOCX 解压后超过 100 MiB 限制")
                if (
                    "word/document.xml" not in archive.namelist()
                    or "[Content_Types].xml" not in archive.namelist()
                ):
                    raise DocumentFormatError("DOCX 包缺少必要结构")
                root = ElementTree.fromstring(archive.read("word/document.xml"))
        except (zipfile.BadZipFile, ElementTree.ParseError) as error:
            raise DocumentFormatError(f"DOCX 解析失败: {error}") from error
        body = root.find(f"{_W}body")
        records: list[DocumentRecord] = []
        section, paragraph_index, table_index = "", 0, 0
        if body is None:
            return records
        for child in body:
            if child.tag == f"{_W}p":
                text = "".join(node.text or "" for node in child.iter(f"{_W}t")).strip()
                if not text:
                    paragraph_index += 1
                    continue
                style_node = child.find(f"{_W}pPr/{_W}pStyle")
                style = style_node.get(f"{_W}val", "") if style_node is not None else ""
                kind = "heading" if style.lower().startswith("heading") else "paragraph"
                if kind == "heading":
                    section = text
                records.append(
                    (kind, text, None, section, {"paragraph": paragraph_index, "style": style}, 1.0)
                )
                paragraph_index += 1
            elif child.tag == f"{_W}tbl":
                rows = []
                for row in child.findall(f"{_W}tr"):
                    cells = [
                        "".join(n.text or "" for n in cell.iter(f"{_W}t")).strip()
                        for cell in row.findall(f"{_W}tc")
                    ]
                    if any(cells):
                        rows.append("\t".join(cells))
                if rows:
                    records.append(("table", "\n".join(rows), None, section, {"table": table_index}, 1.0))
                table_index += 1
        return records

    @staticmethod
    def _pdf(path: Path) -> list[DocumentRecord]:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise DocumentFormatError("PDF 解析依赖 pypdf 未安装") from error
        try:
            reader = PdfReader(path)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise DocumentFormatError("PDF 超过 1000 页限制")
            records: list[DocumentRecord] = []
            for page_no, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                blocks = [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", text)]
                for block_no, block in enumerate(blocks):
                    if block:
                        records.append(
                            ("paragraph", block, page_no, "", {"page": page_no, "block": block_no}, 1.0)
                        )
            return records
        except Exception as error:
            raise DocumentFormatError(f"PDF 解析失败: {error}") from error

    @staticmethod
    def _chunks(
        asset_id: str, scope_id: str, revision: int, elements: Sequence[DocumentElement]
    ) -> tuple[DocumentChunk, ...]:
        """Group adjacent elements, splitting oversized atomic parser blocks.

        PDF extraction often returns an entire page as one paragraph.  The former
        implementation only split *between* elements, so a 6k-character page
        silently bypassed the advertised chunk limit.  Keep the original element
        IDs for provenance while storing each text fragment in its own chunk.
        """
        groups: list[tuple[list[DocumentElement], list[str]]] = []
        current_elements: list[DocumentElement] = []
        current_parts: list[str] = []
        size = 0

        def flush() -> None:
            nonlocal current_elements, current_parts, size
            if current_elements:
                groups.append((current_elements, current_parts))
            current_elements, current_parts, size = [], [], 0

        for element in elements:
            for fragment in _split_text(element.text, MAX_CHUNK_CHARS):
                separator = 2 if current_parts else 0
                should_start_chunk = size + separator + len(fragment) > MAX_CHUNK_CHARS
                if current_elements and (should_start_chunk or element.kind == "heading"):
                    flush()
                current_elements.append(element)
                current_parts.append(fragment)
                size += (2 if len(current_parts) > 1 else 0) + len(fragment)
        flush()
        chunks: list[DocumentChunk] = []
        for ordinal, (group, parts) in enumerate(groups):
            text = "\n\n".join(parts)
            pages = [item.page_no for item in group if item.page_no is not None]
            section = next((item.section_path for item in reversed(group) if item.section_path), "")
            chunks.append(
                DocumentChunk(
                    id=f"dc_{asset_id}_{revision}_{ordinal:06d}",
                    document_asset_id=asset_id,
                    scope_id=scope_id,
                    revision=revision,
                    ordinal=ordinal,
                    text=text,
                    start_element_id=group[0].id,
                    end_element_id=group[-1].id,
                    section_path=section,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    token_count=max(1, len(text) // 4),
                )
            )
        return tuple(chunks)


def _split_text(text: str, limit: int) -> tuple[str, ...]:
    """Split a single parser element at whitespace, falling back to a hard cut."""
    value = text.strip()
    if len(value) <= limit:
        return (value,) if value else ()
    fragments: list[str] = []
    start = 0
    while len(value) - start > limit:
        stop = start + limit
        boundary = max(value.rfind(" ", start, stop + 1), value.rfind("\n", start, stop + 1))
        if boundary <= start:
            boundary = stop
        fragments.append(value[start:boundary].strip())
        start = boundary
        while start < len(value) and value[start].isspace():
            start += 1
    if start < len(value):
        fragments.append(value[start:].strip())
    return tuple(fragment for fragment in fragments if fragment)
