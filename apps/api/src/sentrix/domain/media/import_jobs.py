"""持久导入作业：接收确认、阶段进度与取消。

“已接收”只在原件落盘与作业入库都完成后成立。素材处理本身在任务 5 的执行器里，
这里只定义状态与仓储契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ImportJobStatus(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    WAITING_DEPENDENCY = "waiting_dependency"
    RETRY_SCHEDULED = "retry_scheduled"
    NEEDS_ATTENTION = "needs_attention"


TERMINAL_STATUSES = frozenset(
    {
        ImportJobStatus.COMPLETED,
        ImportJobStatus.PARTIAL,
        ImportJobStatus.CANCELLED,
        ImportJobStatus.NEEDS_ATTENTION,
    }
)

#: 固定阶段顺序。不适用的阶段记 ``skipped`` 与理由，不跳过记账。
STAGES: tuple[str, ...] = (
    "asset_staged",
    "prepared",
    "visual_indexed",
    "observation_saved",
    "faces_saved",
    "event_assigned",
    "text_indexed",
    "entities_saved",
    "facts_saved",
    "lexical_indexed",
    "video_scenes_done",
)


@dataclass(frozen=True, slots=True)
class ImportJob:
    id: str
    asset_id: str
    scope_id: str
    receive_seq: int
    file_name: str
    media_type: str
    content_sha256: str
    size_bytes: int
    status: str
    stage: str
    attempts: int
    error_category: str
    error_message: str
    idempotency_key: str
    accepted_at: datetime
    updated_at: datetime
    next_retry_at: datetime | None = None
    lease_expires_at: datetime | None = None
    fence: int = 0
    completed_items: int = 0
    failed_items: int = 0
    original_path: str = ""


@dataclass(frozen=True, slots=True)
class ImportJobStage:
    job_id: str
    stage: str
    status: str
    reason: str
    attempts: int
    error_category: str
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class UploadQuotaExceeded(Exception):
    """原件配额或单文件上限不满足。"""


class UploadBusy(Exception):
    """同一用户在传文件达到并发上限。"""


class IdempotencyConflict(Exception):
    """同一个幂等键被用于不同内容。"""


@runtime_checkable
class ImportJobRepository(Protocol):
    def reserve_upload(
        self,
        expected_bytes: int,
        *,
        quota_bytes: int,
        max_inflight: int,
        free_bytes: int,
        min_free_bytes: int,
    ) -> str: ...

    def release_upload(self, reservation_id: str) -> None: ...

    def clear_reservations(self) -> int: ...

    def accept(
        self,
        job: ImportJob,
        *,
        reservation_id: str | None,
        quota_bytes: int,
    ) -> tuple[ImportJob, bool]: ...

    def get(self, job_id: str) -> ImportJob | None: ...

    def list_for_scope(self, scope_id: str | None, *, limit: int = 200) -> list[ImportJob]: ...

    def cancel(self, job_id: str) -> ImportJob | None: ...

    def original_bytes(self) -> int: ...

    def referenced_paths(self) -> set[str]: ...

    def stage_rows(self, job_id: str) -> list[ImportJobStage]: ...
