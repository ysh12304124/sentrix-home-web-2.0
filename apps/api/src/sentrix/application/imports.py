from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class UploadRoute(StrEnum):
    DOCUMENT = "document"
    MEDIA = "media"


DOCUMENT_SUFFIXES = {".txt", ".doc", ".docx", ".pdf"}
MEDIA_SUFFIX_KIND = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".heic": "image",
    ".heif": "image",
    ".webp": "image",
    ".gif": "image",
    ".tif": "image",
    ".tiff": "image",
    ".mov": "video",
    ".mp4": "video",
    ".m4v": "video",
    ".avi": "video",
    ".mkv": "video",
    ".wav": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".flac": "audio",
    ".ogg": "audio",
}
DOCUMENT_MIMES = {
    "text/plain",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def classify_upload(file_name: str, content_type: str | None, content: bytes) -> UploadRoute:
    """Choose a pipeline without allowing contradictory metadata to cross boundaries."""

    suffix = Path(file_name).suffix.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    mime_kind = next(
        (kind for kind in ("image", "audio", "video") if mime.startswith(f"{kind}/")),
        None,
    )
    has_pdf_signature = content.startswith(b"%PDF-")

    if suffix in DOCUMENT_SUFFIXES:
        if mime_kind is not None:
            raise ValueError(f"文件后缀 {suffix} 与媒体 MIME {mime} 冲突")
        if has_pdf_signature and suffix != ".pdf":
            raise ValueError(f"文件内容是 PDF，但后缀是 {suffix}")
        return UploadRoute.DOCUMENT

    suffix_kind = MEDIA_SUFFIX_KIND.get(suffix)
    if suffix_kind is not None:
        if mime in DOCUMENT_MIMES or has_pdf_signature:
            raise ValueError(f"媒体后缀 {suffix} 与文档内容或 MIME 冲突")
        if mime_kind is not None and mime_kind != suffix_kind:
            raise ValueError(f"文件后缀 {suffix} 与 MIME {mime} 冲突")
        return UploadRoute.MEDIA

    if has_pdf_signature or mime in DOCUMENT_MIMES:
        raise ValueError("识别为文档，但缺少受支持的 .txt、.docx 或 .pdf 后缀")
    if mime_kind is not None:
        return UploadRoute.MEDIA
    raise ValueError(f"无法识别文件类型: {suffix or '无扩展名'} / {mime or '无 MIME'}")
