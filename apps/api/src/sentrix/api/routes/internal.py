"""只含运行元数据的内部状态投影，供控制面监测使用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sentrix.api.deps import get_container
from sentrix.bootstrap.container import Container

router = APIRouter(tags=["internal"])


@router.get("/internal/status")
def internal_status(container: Container = Depends(get_container)) -> dict[str, object]:
    if not container.settings.experience_mode:
        raise HTTPException(status_code=404, detail="not found")
    jobs = container.import_jobs.list_for_scope(None, limit=1000)
    job_counts: dict[str, int] = {}
    active_statuses = {"accepted", "queued", "running", "waiting_dependency", "retry_scheduled"}
    active_jobs = [job for job in jobs if job.status in active_statuses]
    for job in jobs:
        job_counts[job.status] = job_counts.get(job.status, 0) + 1
    return {
        "jobs": job_counts,
        "job_total": len(jobs),
        "oldest_active_job": min(
            (job.updated_at.isoformat() for job in active_jobs), default=None
        ),
        "turns": container.turn_repository.status_counts(),
        "oldest_active_turn": container.turn_repository.oldest_active_updated_at(),
        "assets": len(container.asset_repository.list_by_scope(None, limit=100000)),
        "database": container.database_healthy(),
        "embeddings": container.embedding_health.available,
    }
