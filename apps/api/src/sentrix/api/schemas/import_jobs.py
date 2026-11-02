"""持久导入作业的 HTTP 形状。不返回原件路径或任何内部目录。"""

from __future__ import annotations

from pydantic import BaseModel


class UploadAcceptedResponse(BaseModel):
    job_id: str
    asset_id: str
    status: str = "accepted"


class ImportJobStageView(BaseModel):
    stage: str
    status: str
    reason: str = ""
    attempts: int = 0
    error_category: str = ""


class ImportJobView(BaseModel):
    job_id: str
    asset_id: str
    scope_id: str
    receive_seq: int
    file_name: str
    media_type: str
    size_bytes: int
    status: str
    stage: str
    attempts: int
    error_category: str = ""
    error_message: str = ""
    next_retry_at: str | None = None
    accepted_at: str
    updated_at: str
    completed_items: int = 0
    failed_items: int = 0
    stages: list[ImportJobStageView] = []


class ImportJobListResponse(BaseModel):
    items: list[ImportJobView]
