"""有界导入执行器：每个用户一个线程，数据库是唯一待办来源。

只有一个提交线程：同一相册的事件归属因此天然串行，准备步骤仍按既有上限并行。
租约由独立心跳续期，长模型调用不会让作业被别人抢走；阶段写入都带 fence。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from sentrix.application.media.import_assets import (
    ImportAssets,
    ImportRequest,
    ImportResult,
    StagedImport,
)
from sentrix.domain.media.import_jobs import ImportJob
from sentrix.domain.media.models import AssetStatus, MediaType
from sentrix.domain.media.ports import AssetRepository

log = logging.getLogger("sentrix.media.executor")

LEASE_S = 120.0
HEARTBEAT_S = 20.0
POLL_S = 1.0
MAX_ATTEMPTS = 5


class JobFailure(Exception):
    category = "temporary_failure"


class DependencyUnavailable(JobFailure):
    category = "dependency_unavailable"


class TemporaryFailure(JobFailure):
    category = "temporary_failure"


class InputInvalid(JobFailure):
    category = "input_invalid"


class JobCancelled(JobFailure):
    category = "cancelled"


class _Heartbeat:
    def __init__(
        self,
        jobs,
        job_id: str,
        fence: int,
        clock: Callable[[], datetime],
        interval: float = HEARTBEAT_S,
    ) -> None:
        self._jobs = jobs
        self._job_id = job_id
        self._fence = fence
        self._clock = clock
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._jobs.heartbeat(
                    self._job_id, self._fence, now=self._clock(), lease_s=int(LEASE_S)
                )
            except Exception:  # noqa: BLE001 - 心跳失败不杀执行线程
                log.warning("导入作业心跳失败 job=%s", self._job_id, exc_info=True)


def _backoff(attempt: int) -> float:
    base = min(5.0 * 2 ** max(0, attempt - 1), 300.0)
    return base + random.uniform(0, min(30.0, base * 0.1))


class ImportJobExecutor:
    def __init__(
        self,
        *,
        settings,
        jobs,
        assets: AssetRepository,
        importer_factory: Callable[[], ImportAssets],
        dependencies_ready: Callable[[], tuple[bool, str]],
        # 返回未完成的阶段名；空列表表示整代后处理成功。
        postprocess: Callable[[str], list[str]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._assets = assets
        self._importer_factory = importer_factory
        self._dependencies_ready = dependencies_ready
        self._postprocess = postprocess
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready_streak = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="import-executor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self._run_once()
            except Exception:  # noqa: BLE001 - 循环必须活到进程结束
                log.exception("导入执行器迭代失败")
                worked = False
            if not worked:
                self._stop.wait(POLL_S)

    def _run_once(self) -> bool:
        now = self._clock()
        job = self._jobs.claim_due(now=now, lease_s=int(LEASE_S))
        if job is not None:
            self._execute(job)
            return True
        ready, _ = self._dependencies_ready()
        # 依赖恢复要稳定两轮才唤醒等待作业，避免一次抖动就把整批放出来。
        self._ready_streak = self._ready_streak + 1 if ready else 0
        if self._ready_streak >= 2:
            self._jobs.wake_waiting(now=now)
        self._run_postprocess(now)
        return False

    def _run_postprocess(self, now: datetime) -> None:
        if self._postprocess is None:
            return
        claimed = self._jobs.claim_postprocess(now=now)
        if claimed is None:
            return
        scope_id, generation = claimed
        try:
            failures = list(self._postprocess(scope_id))
        except Exception:  # noqa: BLE001 - 保留成功资产，整代重试
            log.exception("相册后处理失败 scope=%s generation=%s", scope_id, generation)
            self._jobs.mark_scope_postprocess_partial(
                scope_id, message="后处理失败，未完成阶段：postprocess", now=self._clock()
            )
            self._jobs.finish_postprocess(scope_id, generation, success=False, now=self._clock())
            return
        if failures:
            message = "后处理未完成：" + "、".join(failures)
            log.warning(
                "相册后处理部分失败 scope=%s generation=%s stages=%s",
                scope_id,
                generation,
                ",".join(failures),
            )
            self._jobs.mark_scope_postprocess_partial(
                scope_id, message=message, now=self._clock()
            )
            # 五分钟后重试整代；成功时 clear_scope_postprocess_partial 会把作业清回 completed。
            self._jobs.finish_postprocess(scope_id, generation, success=False, now=self._clock())
            return
        self._jobs.clear_scope_postprocess_partial(scope_id, now=self._clock())
        self._jobs.finish_postprocess(scope_id, generation, success=True, now=self._clock())

    def _execute(self, job: ImportJob) -> None:
        heartbeat = _Heartbeat(self._jobs, job.id, job.fence, self._clock)
        heartbeat.start()
        try:
            if self._jobs.is_cancel_requested(job.id):
                raise JobCancelled("cancelled before start")
            ready, reason = self._dependencies_ready()
            if not ready:
                raise DependencyUnavailable(reason)
            self._process(job)
        except JobFailure as error:
            self._handle_failure(job, error)
        except Exception as error:  # noqa: BLE001 - 分类后决定重试或转人工
            log.exception("导入作业执行失败 job=%s", job.id)
            ready, reason = self._dependencies_ready()
            if not ready:
                self._handle_failure(job, DependencyUnavailable(reason))
            else:
                self._handle_failure(job, TemporaryFailure(str(error)))
        finally:
            heartbeat.stop()

    def _process(self, job: ImportJob) -> None:
        importer = self._importer_factory()
        asset = self._assets.get(job.asset_id)
        if asset is None:
            source = Path(job.original_path)
            if not source.is_file():
                raise InputInvalid("original media file is missing")
            self._mark(job, "asset_staged", "running")
            outcome = importer.stage(
                ImportRequest(
                    scope_id=job.scope_id,
                    file_name=job.file_name,
                    content_type=None,
                    source_path=source,
                    source_move=False,
                    asset_id=job.asset_id,
                    content_sha256=job.content_sha256,
                    defer_processing=True,
                )
            )
            if isinstance(outcome, ImportResult):
                self._mark(job, "asset_staged", "done")
                self._complete_deduplicated(job, outcome)
                return
            staged: StagedImport = outcome
            self._mark(job, "asset_staged", "done")
        else:
            self._mark(job, "asset_staged", "done")
            staged = importer.resume(asset)

        self._mark(job, "prepared", "running")
        prepared = importer.prepare_image(staged.asset)
        self._mark(job, "prepared", "done")
        if self._jobs.is_cancel_requested(job.id):
            raise JobCancelled("cancelled before commit")

        result = importer.finish(staged, prepared)
        if result.video_scene_error:
            if "no_duration" in result.video_scene_error:
                raise InputInvalid(f"video scenes: {result.video_scene_error}")
            raise TemporaryFailure(f"video scenes: {result.video_scene_error}")

        self._record_stages(job, result)
        self._assets.update_status(staged.asset.id, AssetStatus.PROCESSED)
        self._jobs.bump_scope_generation(job.scope_id, now=self._clock())
        self._jobs.set_job_status(
            job.id,
            job.fence,
            now=self._clock(),
            status="completed",
            stage="completed",
            completed_items=1,
        )

    def _record_stages(self, job: ImportJob, result: ImportResult) -> None:
        stages: list[tuple[str, bool, str]] = [
            ("visual_indexed", result.indexed, "no_visual_vector"),
            ("observation_saved", result.observation_id is not None, "no_observation"),
            ("faces_saved", result.faces > 0, "no_faces"),
            ("event_assigned", result.event_id is not None, "no_event"),
            ("text_indexed", result.text_rows > 0, "no_text"),
            ("entities_saved", bool(result.entity_ids), "no_entities"),
            ("facts_saved", bool(result.fact_ids), "no_facts"),
            ("lexical_indexed", result.lexical_terms > 0, "no_lexical_terms"),
            (
                "video_scenes_done",
                result.media_type is not MediaType.VIDEO or result.video_scenes > 0,
                "no_video_scenes",
            ),
        ]
        for stage, done, reason in stages:
            self._mark(job, stage, "done" if done else "skipped", "" if done else reason)

    def _complete_deduplicated(self, job: ImportJob, result: ImportResult) -> None:
        for stage in (
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
        ):
            self._mark(job, stage, "skipped", "deduplicated")
        self._jobs.set_job_status(
            job.id,
            job.fence,
            now=self._clock(),
            status="completed",
            stage="completed",
            asset_id=result.asset_id,
            completed_items=1,
        )

    def _mark(self, job: ImportJob, stage: str, status: str, reason: str = "") -> None:
        if status in {"running", "done", "skipped"}:
            self._jobs.set_stage_name(job.id, job.fence, stage, now=self._clock())
        self._jobs.set_stage(
            job.id,
            job.fence,
            stage,
            status,
            now=self._clock(),
            reason=reason,
            error_category="" if status in {"done", "skipped"} else status,
        )

    def _handle_failure(self, job: ImportJob, error: JobFailure) -> None:
        now = self._clock()
        stage = job.stage or "asset_staged"
        if isinstance(error, JobCancelled):
            self._mark(job, stage, "skipped", "cancelled")
            self._jobs.set_job_status(
                job.id,
                job.fence,
                now=now,
                status="cancelled",
                stage="cancelled",
                error_category="cancelled",
                error_message=str(error),
            )
            return
        if isinstance(error, DependencyUnavailable):
            # 等待依赖不消耗阶段重试次数：认领时加过的这一次退回去。
            self._mark(job, stage, "waiting_dependency", str(error))
            self._jobs.set_job_status(
                job.id,
                job.fence,
                now=now,
                status="waiting_dependency",
                stage=stage,
                error_category=error.category,
                error_message=str(error),
                attempts=max(0, job.attempts - 1),
            )
            return
        if isinstance(error, TemporaryFailure) and job.attempts < MAX_ATTEMPTS:
            next_retry = now + timedelta(seconds=_backoff(job.attempts))
            self._mark(job, stage, "failed", str(error))
            self._jobs.set_job_status(
                job.id,
                job.fence,
                now=now,
                status="retry_scheduled",
                stage=stage,
                error_category=error.category,
                error_message=str(error),
                next_retry_at=next_retry,
            )
            return
        self._mark(job, stage, "failed", str(error))
        self._jobs.set_job_status(
            job.id,
            job.fence,
            now=now,
            status="needs_attention",
            stage=stage,
            error_category=error.category,
            error_message=str(error),
            failed_items=1,
        )
