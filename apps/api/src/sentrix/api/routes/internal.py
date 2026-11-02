"""只含运行元数据的内部状态投影，供控制面监测使用。

**不读问答正文、不读媒体内容**：管理员看到的是状态、阶段、耗时与计数，
不是用户问了什么、模型答了什么。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sentrix.api.deps import get_container
from sentrix.bootstrap.container import Container
from sentrix.domain.media.import_jobs import STAGES, ImportJob

router = APIRouter(tags=["internal"])

ACTIVE_JOB_STATUSES = {"accepted", "queued", "running", "waiting_dependency", "retry_scheduled"}
RECENT_LIMIT = 10
ACTIVE_LIMIT = 20


def _job_view(job: ImportJob, *, with_stages: bool, container: Container) -> dict[str, object]:
    view: dict[str, object] = {
        "job_id": job.id,
        "asset_id": job.asset_id,
        "scope_id": job.scope_id,
        "file_name": job.file_name,
        "media_type": job.media_type,
        "size_bytes": job.size_bytes,
        "status": job.status,
        "stage": job.stage,
        "attempts": job.attempts,
        "accepted_at": job.accepted_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "error_category": job.error_category,
        "error_message": job.error_message,
    }
    if not with_stages:
        return view
    rows = container.import_jobs.stage_rows(job.id)
    done = sum(1 for row in rows if row.status in {"done", "skipped"})
    view["progress"] = {"done": done, "total": len(STAGES)}
    view["stages"] = [
        {
            "stage": row.stage,
            "status": row.status,
            "reason": row.reason,
            "attempts": row.attempts,
            "error_category": row.error_category,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]
    return view


def _turn_view(record) -> dict[str, object]:
    """回合运行元数据；question/answer 永不出这个投影。"""
    return {
        "turn_id": record.id,
        "conversation_id": record.conversation_id,
        "scope_id": record.scope_id,
        "status": record.status.value,
        "execution_state": record.execution_state,
        "attempt_no": record.attempt_no,
        "cancel_requested": record.cancel_requested,
        "waiting_reason": record.waiting_reason,
        "next_retry_at": record.next_retry_at.isoformat() if record.next_retry_at else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


@router.get("/internal/status")
def internal_status(container: Container = Depends(get_container)) -> dict[str, object]:
    if not container.settings.experience_mode:
        raise HTTPException(status_code=404, detail="not found")
    jobs = container.import_jobs.list_for_scope(None, limit=1000)
    job_counts: dict[str, int] = {}
    active_jobs = [job for job in jobs if job.status in ACTIVE_JOB_STATUSES]
    recent_jobs = [job for job in jobs if job.status not in ACTIVE_JOB_STATUSES]
    recent_jobs.sort(key=lambda job: job.updated_at, reverse=True)
    for job in jobs:
        job_counts[job.status] = job_counts.get(job.status, 0) + 1
    active_turns = container.turn_repository.list_active(limit=ACTIVE_LIMIT)
    recent_turns = container.turn_repository.list_recent(limit=RECENT_LIMIT)
    return {
        "jobs": job_counts,
        "job_total": len(jobs),
        "jobs_active": len(active_jobs),
        "oldest_active_job": min(
            (job.updated_at.isoformat() for job in active_jobs), default=None
        ),
        "active_jobs": [
            _job_view(job, with_stages=True, container=container)
            for job in sorted(active_jobs, key=lambda item: item.updated_at)[:ACTIVE_LIMIT]
        ],
        "recent_jobs": [
            _job_view(job, with_stages=False, container=container)
            for job in recent_jobs[:RECENT_LIMIT]
        ],
        "turns": container.turn_repository.status_counts(),
        "turns_active": len(active_turns),
        "oldest_active_turn": container.turn_repository.oldest_active_updated_at(),
        "active_turns": [_turn_view(item) for item in active_turns],
        "recent_turns": [_turn_view(item) for item in recent_turns],
        "assets": len(container.asset_repository.list_by_scope(None, limit=100000)),
        "database": container.database_healthy(),
        "embeddings": container.embedding_health.available,
        "build_revision": container.build_revision,
    }
