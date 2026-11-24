from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryExportCreateRequest(BaseModel):
    scope_id: str = Field(..., min_length=1, max_length=255)
    asset_ids: list[str] = Field(default_factory=list)
    compat_mode: str = Field("strict_v3", pattern=r"^(compat|strict_v3)$")
    id_map: dict[str, str] = Field(default_factory=dict)


class ExportFilePayload(BaseModel):
    name: str
    sha256: str
    size_bytes: int


class MemoryExportPayload(BaseModel):
    id: str
    scope_id: str
    status: str
    schema_version: str
    compat_mode: str
    selection: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    files: list[ExportFilePayload] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class MemoryExportListResponse(BaseModel):
    scope_id: str
    items: list[MemoryExportPayload] = Field(default_factory=list)
