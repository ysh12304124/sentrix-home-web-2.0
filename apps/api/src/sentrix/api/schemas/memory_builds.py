"""图片构建状态的读取契约。只读接口，不暴露原始响应正文。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MemoryBuildStageState(BaseModel):
    stage: str
    status: str


class MemoryBuildAssetPayload(BaseModel):
    asset_id: str
    file_name: str
    media_kind: str
    derived_kind: str
    parent_asset_id: str
    observation_id: str | None
    observation_revision: int
    available_fields: list[str]
    build_states: list[MemoryBuildStageState]
    status: str
    pending_tasks: int


class MemoryBuildInventoryResponse(BaseModel):
    scope_id: str
    counts: dict[str, int]
    assets: list[MemoryBuildAssetPayload]


class MemoryBuildPayload(BaseModel):
    id: str
    scope_id: str
    asset_id: str
    observation_id: str | None
    observation_revision: int
    stage: str
    status: str
    build_key: str
    input_hash: str
    model_name: str
    protocol_version: str
    prompt_hash: str
    params: dict[str, Any]
    input_transform: dict[str, Any]
    raw_response_ref: str
    error_category: str
    error: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class MemoryEvidencePayload(BaseModel):
    id: str
    scope_id: str
    owner_kind: str
    owner_id: str
    field_path: str
    role: str
    value: Any
    value_hash: str
    source_kind: str
    source_modality: str
    source_channel: str
    locator: dict[str, Any]
    observation_revision: int
    build_id: str
    correction_id: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None


class MemoryBuildDetailResponse(BaseModel):
    build: MemoryBuildPayload
    evidence: list[MemoryEvidencePayload]
