"""持久导入作业查询与取消。只做 HTTP 映射。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sentrix.api.deps import get_container
from sentrix.api.schemas.import_jobs import (
    ImportJobListResponse,
    ImportJobStageView,
    ImportJobView,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.media.import_jobs import ImportJob

router = APIRouter(tags=["import-jobs"])


def to_job_view(job: ImportJob, stages: list[ImportJobStageView] | None = None) -> ImportJobView:
    return ImportJobView(
        job_id=job.id,
        asset_id=job.asset_id,
        scope_id=job.scope_id,
        receive_seq=job.receive_seq,
        file_name=job.file_name,
        media_type=job.media_type,
        size_bytes=job.size_bytes,
        status=job.status,
        stage=job.stage,
        attempts=job.attempts,
        error_category=job.error_category,
        error_message=job.error_message,
        next_retry_at=job.next_retry_at.isoformat() if job.next_retry_at else None,
        accepted_at=job.accepted_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        stages=stages or [],
    )


@router.get("/import-jobs")
def list_import_jobs(
    scope_id: str = "",
    container: Container = Depends(get_container),
) -> ImportJobListResponse:
    jobs = container.import_jobs.list_for_scope(scope_id or None)
    return ImportJobListResponse(items=[to_job_view(job) for job in jobs])


@router.get("/import-jobs/{job_id}")
def get_import_job(
    job_id: str,
    container: Container = Depends(get_container),
) -> ImportJobView:
    job = container.import_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="import job not found")
    stages = [
        ImportJobStageView(
            stage=row.stage,
            status=row.status,
            reason=row.reason,
            attempts=row.attempts,
            error_category=row.error_category,
        )
        for row in container.import_jobs.stage_rows(job_id)
    ]
    return to_job_view(job, stages)


@router.post("/import-jobs/{job_id}/cancel")
def cancel_import_job(
    job_id: str,
    container: Container = Depends(get_container),
) -> ImportJobView:
    job = container.import_jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="import job not found")
    return to_job_view(job)


@router.post("/import-jobs/{job_id}/requeue")
def requeue_import_job(
    job_id: str,
    container: Container = Depends(get_container),
) -> ImportJobView:
    """管理员重排：只接受已停下、未被执行器持有的作业。"""
    job = container.import_jobs.requeue(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="import job not found")
    if job.status != "queued":
        raise HTTPException(status_code=409, detail="job is not in a requeueable state")
    return to_job_view(job)
