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


class LibraryProgressView(BaseModel):
    """第一段：逐个文件建库。"""

    total: int = 0
    completed: int = 0
    processing: int = 0
    failed: int = 0
    percent: int = 0


class PostprocessProgressView(BaseModel):
    """第二段：整册后处理。阶段与事件计数用于解释“为什么还没结束”。"""

    state: str = "idle"  # idle | pending | running | waiting_retry | done
    stage: str = ""
    events_done: int = 0
    events_total: int = 0
    updated_at: str | None = None
    next_attempt_at: str | None = None


class ImportProgressResponse(BaseModel):
    library: LibraryProgressView
    postprocess: PostprocessProgressView
