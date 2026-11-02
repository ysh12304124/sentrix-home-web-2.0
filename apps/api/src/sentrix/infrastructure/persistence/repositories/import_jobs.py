"""导入作业与上传预留的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

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
                original = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM import_jobs"
                ).fetchone()["n"]
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

                original = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM import_jobs"
                ).fetchone()["n"]
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
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE import_jobs SET status = 'cancelled', updated_at = ? "
                "WHERE id = ? AND status NOT IN ('running', 'completed', 'partial', 'cancelled', 'needs_attention')",
                (_now(), job_id),
            )
        return self.get(job_id)

    def original_bytes(self) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM import_jobs"
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def referenced_paths(self) -> set[str]:
        """作业原件与已登记资产路径的并集；启动清理只删不在这张表里的文件。"""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT original_path AS path FROM import_jobs WHERE original_path <> '' "
                "UNION SELECT path FROM assets"
            ).fetchall()
        return {str(row["path"]) for row in rows}

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
