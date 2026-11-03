"""导入作业与上传预留的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from sentrix.domain.media.import_jobs import (
    ImportJob,
    ImportJobStage,
    IdempotencyConflict,
    UploadBusy,
    UploadQuotaExceeded,
)
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, asset_id, scope_id, receive_seq, file_name, media_type, content_sha256, "
    "size_bytes, status, stage, attempts, lease_expires_at, fence, next_retry_at, "
    "error_category, error_message, idempotency_key, accepted_at, updated_at, "
    "completed_items, failed_items, original_path"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _unique_original_bytes(connection) -> int:
    """按唯一原件路径计配额；空路径的作业各自单独计数。"""
    unique = connection.execute(
        "SELECT COALESCE(SUM(size), 0) AS n FROM ("
        "  SELECT MAX(size_bytes) AS size FROM import_jobs"
        "  WHERE original_path <> '' GROUP BY original_path"
        ")"
    ).fetchone()["n"]
    empty = connection.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM import_jobs WHERE original_path = ''"
    ).fetchone()["n"]
    return int(unique) + int(empty)


class SqliteImportJobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- 上传预留 -----------------------------------------------------------

    def reserve_upload(
        self,
        expected_bytes: int,
        *,
        quota_bytes: int,
        max_inflight: int,
        free_bytes: int,
        min_free_bytes: int,
    ) -> str:
        if expected_bytes < 1:
            raise UploadQuotaExceeded("empty upload")
        if free_bytes < min_free_bytes:
            raise UploadQuotaExceeded("server disk space below upload threshold")
        reservation_id = f"upl_{uuid.uuid4().hex[:16]}"
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inflight = connection.execute(
                    "SELECT COUNT(*) AS n FROM upload_reservations"
                ).fetchone()["n"]
                if int(inflight) >= max_inflight:
                    raise UploadBusy("too many concurrent uploads")
                original = _unique_original_bytes(connection)
                reserved = connection.execute(
                    "SELECT COALESCE(SUM(expected_bytes), 0) AS n FROM upload_reservations"
                ).fetchone()["n"]
                if int(original) + int(reserved) + expected_bytes > quota_bytes:
                    raise UploadQuotaExceeded("original media quota exceeded")
                connection.execute(
                    "INSERT INTO upload_reservations (id, asset_id, expected_bytes, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (reservation_id, "", expected_bytes, _now()),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return reservation_id

    def release_upload(self, reservation_id: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "DELETE FROM upload_reservations WHERE id = ?", (reservation_id,)
            )

    def clear_reservations(self) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute("DELETE FROM upload_reservations")
        return cursor.rowcount

    # ---- 接收确认 -----------------------------------------------------------

    def accept(
        self,
        job: ImportJob,
        *,
        reservation_id: str | None,
        quota_bytes: int,
    ) -> tuple[ImportJob, bool]:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    f"SELECT {_COLUMNS} FROM import_jobs WHERE idempotency_key = ?",
                    (job.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if reservation_id is not None:
                        connection.execute(
                            "DELETE FROM upload_reservations WHERE id = ?", (reservation_id,)
                        )
                    connection.commit()
                    if str(existing["content_sha256"]) != job.content_sha256:
                        raise IdempotencyConflict("idempotency key reused for different content")
                    return _job(existing), False

                original = _unique_original_bytes(connection)
                reserved_other = connection.execute(
                    "SELECT COALESCE(SUM(expected_bytes), 0) AS n FROM upload_reservations "
                    "WHERE id <> ?",
                    (reservation_id or "",),
                ).fetchone()["n"]
                if int(original) + int(reserved_other) + job.size_bytes > quota_bytes:
                    raise UploadQuotaExceeded("original media quota exceeded")

                sequence = connection.execute(
                    "SELECT COALESCE(MAX(receive_seq), 0) + 1 AS n FROM import_jobs WHERE scope_id = ?",
                    (job.scope_id,),
                ).fetchone()["n"]
                if reservation_id is not None:
                    connection.execute(
                        "DELETE FROM upload_reservations WHERE id = ?", (reservation_id,)
                    )
                connection.execute(
                    """
                    INSERT INTO import_jobs (
                        id, asset_id, scope_id, receive_seq, file_name, media_type,
                        content_sha256, size_bytes, status, stage, attempts,
                        lease_expires_at, fence, next_retry_at, error_category,
                        error_message, idempotency_key, accepted_at, updated_at,
                        completed_items, failed_items, original_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, '', '', ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        job.id,
                        job.asset_id,
                        job.scope_id,
                        int(sequence),
                        job.file_name,
                        job.media_type,
                        job.content_sha256,
                        job.size_bytes,
                        job.status,
                        job.stage,
                        job.attempts,
                        job.idempotency_key,
                        job.accepted_at.isoformat(),
                        job.updated_at.isoformat(),
                        job.original_path,
                    ),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        stored = self.get(job.id)
        if stored is None:  # pragma: no cover - 提交后必然可读
            raise RuntimeError("import job insert did not persist")
        return stored, True

    # ---- 查询与取消 ---------------------------------------------------------

    def get(self, job_id: str) -> ImportJob | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM import_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _job(row) if row is not None else None

    def list_for_scope(self, scope_id: str | None, *, limit: int = 200) -> list[ImportJob]:
        sql = f"SELECT {_COLUMNS} FROM import_jobs"
        parameters: tuple[object, ...] = ()
        if scope_id:
            sql += " WHERE scope_id = ?"
            parameters = (scope_id,)
        sql += " ORDER BY receive_seq DESC LIMIT ?"
        parameters += (limit,)
        with self._database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [_job(row) for row in rows]

    def cancel(self, job_id: str) -> ImportJob | None:
        stamp = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE import_jobs SET cancel_requested = 1, updated_at = ? "
                "WHERE id = ? AND status NOT IN "
                "('completed', 'partial', 'cancelled', 'needs_attention')",
                (stamp, job_id),
            )
            # 没在执行的作业立即终止；running 的由执行器在安全检查点收尾。
            connection.execute(
                "UPDATE import_jobs SET status = 'cancelled', stage = 'cancelled',"
                " lease_expires_at = NULL, updated_at = ? "
                "WHERE id = ? AND status NOT IN "
                "('running', 'completed', 'partial', 'cancelled', 'needs_attention')",
                (stamp, job_id),
            )
        return self.get(job_id)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM import_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return bool(row["cancel_requested"]) if row is not None else False

    def adopt_original_path(
        self,
        job_id: str,
        fence: int,
        *,
        asset_id: str,
        original_path: str,
        now: datetime,
    ) -> bool:
        """去重命中后把作业指向权威原件；围栏不匹配时不改。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE import_jobs SET asset_id = ?, original_path = ?, updated_at = ?"
                " WHERE id = ? AND fence = ?",
                (asset_id, original_path, now.isoformat(), job_id, fence),
            )
        return cursor.rowcount > 0

    def requeue(self, job_id: str) -> ImportJob | None:
        """管理员重排：只对已停下且可安全重放的作业生效；围栏加一使旧执行者失效。"""
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE import_jobs SET status = 'queued', stage = 'requeued',"
                " error_category = '', error_message = '', next_retry_at = NULL,"
                " lease_expires_at = NULL, cancel_requested = 0, fence = fence + 1,"
                " updated_at = ? WHERE id = ? AND status IN ('needs_attention', 'partial')",
                (_now(), job_id),
            )
        return self.get(job_id)

    def original_bytes(self) -> int:
        with self._database.connect() as connection:
            return _unique_original_bytes(connection)

    def referenced_paths(self) -> set[str]:
        """作业原件与已登记资产路径的并集；启动清理只删不在这张表里的文件。"""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT original_path AS path FROM import_jobs WHERE original_path <> '' "
                "UNION SELECT path FROM assets"
            ).fetchall()
        return {str(row["path"]) for row in rows}

    # ---- 执行器：认领、租约与阶段 -------------------------------------------

    def claim_due(self, *, now: datetime, lease_s: int) -> ImportJob | None:
        """原子认领一条到期作业；过期 lease 的 running 作业会被重新认领。"""
        stamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_s)).isoformat()
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM import_jobs WHERE "
                    "(status IN ('accepted', 'queued', 'retry_scheduled') "
                    " AND (next_retry_at IS NULL OR next_retry_at <= ?)) "
                    "OR (status = 'running' AND lease_expires_at IS NOT NULL "
                    " AND lease_expires_at < ?) "
                    "ORDER BY receive_seq ASC LIMIT 1",
                    (stamp, stamp),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE import_jobs SET status = 'running', attempts = attempts + 1, "
                    "fence = fence + 1, lease_expires_at = ?, updated_at = ? WHERE id = ?",
                    (expires, stamp, row["id"]),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return self.get(str(row["id"]))

    def heartbeat(self, job_id: str, fence: int, *, now: datetime, lease_s: int) -> bool:
        expires = (now + timedelta(seconds=lease_s)).isoformat()
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE import_jobs SET lease_expires_at = ?, updated_at = ? "
                "WHERE id = ? AND fence = ? AND status = 'running'",
                (expires, now.isoformat(), job_id, fence),
            )
        return cursor.rowcount > 0

    def set_job_status(
        self,
        job_id: str,
        fence: int,
        *,
        now: datetime,
        status: str,
        stage: str | None = None,
        error_category: str = "",
        error_message: str = "",
        next_retry_at: datetime | None = None,
        asset_id: str | None = None,
        completed_items: int | None = None,
        failed_items: int | None = None,
        attempts: int | None = None,
    ) -> bool:
        fields = [
            "status = ?",
            "error_category = ?",
            "error_message = ?",
            "next_retry_at = ?",
            "updated_at = ?",
            "lease_expires_at = NULL",
        ]
        values: list[object] = [
            status,
            error_category,
            error_message,
            next_retry_at.isoformat() if next_retry_at else None,
            now.isoformat(),
        ]
        if stage is not None:
            fields.append("stage = ?")
            values.append(stage)
        if asset_id is not None:
            fields.append("asset_id = ?")
            values.append(asset_id)
        if completed_items is not None:
            fields.append("completed_items = ?")
            values.append(completed_items)
        if failed_items is not None:
            fields.append("failed_items = ?")
            values.append(failed_items)
        if attempts is not None:
            fields.append("attempts = ?")
            values.append(max(0, attempts))
        values.extend([job_id, fence])
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                f"UPDATE import_jobs SET {', '.join(fields)} WHERE id = ? AND fence = ?",
                tuple(values),
            )
        return cursor.rowcount > 0

    def set_stage(
        self,
        job_id: str,
        fence: int,
        stage: str,
        status: str,
        *,
        now: datetime,
        reason: str = "",
        error_category: str = "",
    ) -> bool:
        stamp = now.isoformat()
        with self._database.connect() as connection, connection:
            owner = connection.execute(
                "SELECT fence FROM import_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if owner is None or int(owner["fence"]) != fence:
                return False
            connection.execute(
                """
                INSERT INTO import_job_stages (
                    job_id, stage, status, reason, attempts, error_category,
                    started_at, finished_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(job_id, stage) DO UPDATE SET
                    status = excluded.status,
                    reason = excluded.reason,
                    attempts = import_job_stages.attempts
                        + (CASE WHEN excluded.status = 'running' THEN 1 ELSE 0 END),
                    error_category = excluded.error_category,
                    started_at = COALESCE(import_job_stages.started_at, excluded.started_at),
                    finished_at = excluded.finished_at,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    stage,
                    status,
                    reason,
                    error_category,
                    stamp,
                    stamp if status in {"done", "skipped", "failed"} else None,
                    stamp,
                ),
            )
        return True

    def set_stage_name(self, job_id: str, fence: int, stage: str, *, now: datetime) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE import_jobs SET stage = ?, updated_at = ? WHERE id = ? AND fence = ?",
                (stage, now.isoformat(), job_id, fence),
            )

    def stage_status(self, job_id: str, stage: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM import_job_stages WHERE job_id = ? AND stage = ?",
                (job_id, stage),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def wake_waiting(self, *, now: datetime) -> int:
        """依赖恢复后把等待中的作业放回可认领队列。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE import_jobs SET status = 'retry_scheduled', next_retry_at = ?, "
                "updated_at = ? WHERE status = 'waiting_dependency'",
                (now.isoformat(), now.isoformat()),
            )
        return cursor.rowcount

    # ---- 相册后处理代际 -----------------------------------------------------

    def bump_scope_generation(self, scope_id: str, *, now: datetime) -> int:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO scope_postprocess (scope_id, dirty_generation, processed_generation, updated_at) "
                    "VALUES (?, 1, 0, ?) "
                    "ON CONFLICT(scope_id) DO UPDATE SET "
                    "dirty_generation = scope_postprocess.dirty_generation + 1, updated_at = excluded.updated_at",
                    (scope_id, now.isoformat()),
                )
                row = connection.execute(
                    "SELECT dirty_generation FROM scope_postprocess WHERE scope_id = ?", (scope_id,)
                ).fetchone()
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return int(row["dirty_generation"])

    def recover_postprocess(self, *, now: datetime) -> int:
        """进程启动时清掉上次崩溃留下的“处理中”标记，否则该相册永远不会再被认领。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE scope_postprocess SET processing_generation = NULL,"
                " next_attempt_at = NULL, updated_at = ? WHERE processing_generation IS NOT NULL",
                (now.isoformat(),),
            )
        return cursor.rowcount

    def claim_postprocess(self, *, now: datetime) -> tuple[str, int] | None:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT scope_id, dirty_generation FROM scope_postprocess "
                    "WHERE dirty_generation > processed_generation AND processing_generation IS NULL "
                    "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                    "ORDER BY updated_at ASC LIMIT 1",
                    (now.isoformat(),),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE scope_postprocess SET processing_generation = ?, updated_at = ? WHERE scope_id = ?",
                    (row["dirty_generation"], now.isoformat(), row["scope_id"]),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return str(row["scope_id"]), int(row["dirty_generation"])

    def finish_postprocess(
        self, scope_id: str, generation: int, *, success: bool, now: datetime
    ) -> None:
        with self._database.connect() as connection, connection:
            if success:
                connection.execute(
                    "UPDATE scope_postprocess SET processed_generation = MAX(processed_generation, ?), "
                    "processing_generation = NULL, next_attempt_at = NULL, updated_at = ? "
                    "WHERE scope_id = ?",
                    (generation, now.isoformat(), scope_id),
                )
            else:
                connection.execute(
                    "UPDATE scope_postprocess SET processing_generation = NULL, "
                    "next_attempt_at = ?, updated_at = ? WHERE scope_id = ?",
                    (
                        (now + timedelta(minutes=5)).isoformat(),
                        now.isoformat(),
                        scope_id,
                    ),
                )

    def mark_scope_postprocess_partial(
        self, scope_id: str, *, message: str, now: datetime
    ) -> int:
        """批末阶段失败：该相册已完成的作业改为 partial，并标出后处理阶段。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE import_jobs SET status = 'partial', stage = 'postprocess',"
                " error_category = 'postprocess_failed', error_message = ?, updated_at = ?,"
                " lease_expires_at = NULL"
                " WHERE scope_id = ? AND status IN ('completed', 'partial')"
                " AND (status = 'completed' OR error_category = 'postprocess_failed')",
                (message, now.isoformat(), scope_id),
            )
        return cursor.rowcount

    def clear_scope_postprocess_partial(self, scope_id: str, *, now: datetime) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE import_jobs SET status = 'completed', stage = 'completed',"
                " error_category = '', error_message = '', updated_at = ?"
                " WHERE scope_id = ? AND status = 'partial'"
                " AND error_category = 'postprocess_failed'",
                (now.isoformat(), scope_id),
            )
        return cursor.rowcount

    def scope_backlog(self) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM scope_postprocess WHERE dirty_generation > processed_generation"
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def stage_rows(self, job_id: str) -> list[ImportJobStage]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM import_job_stages WHERE job_id = ? ORDER BY stage", (job_id,)
            ).fetchall()
        return [
            ImportJobStage(
                job_id=str(row["job_id"]),
                stage=str(row["stage"]),
                status=str(row["status"]),
                reason=str(row["reason"]),
                attempts=int(row["attempts"]),
                error_category=str(row["error_category"]),
                started_at=_parse(row["started_at"]),
                finished_at=_parse(row["finished_at"]),
                updated_at=_parse(str(row["updated_at"])) or datetime.now(UTC),
            )
            for row in rows
        ]


def _job(row: sqlite3.Row) -> ImportJob:
    accepted_at = _parse(str(row["accepted_at"])) or datetime.now(UTC)
    updated_at = _parse(str(row["updated_at"])) or accepted_at
    return ImportJob(
        id=str(row["id"]),
        asset_id=str(row["asset_id"]),
        scope_id=str(row["scope_id"]),
        receive_seq=int(row["receive_seq"]),
        file_name=str(row["file_name"]),
        media_type=str(row["media_type"]),
        content_sha256=str(row["content_sha256"]),
        size_bytes=int(row["size_bytes"]),
        status=str(row["status"]),
        stage=str(row["stage"]),
        attempts=int(row["attempts"]),
        error_category=str(row["error_category"]),
        error_message=str(row["error_message"]),
        idempotency_key=str(row["idempotency_key"]),
        accepted_at=accepted_at,
        updated_at=updated_at,
        next_retry_at=_parse(row["next_retry_at"]),
        lease_expires_at=_parse(row["lease_expires_at"]),
        fence=int(row["fence"]),
        completed_items=int(row["completed_items"]),
        failed_items=int(row["failed_items"]),
        original_path=str(row["original_path"]),
    )
