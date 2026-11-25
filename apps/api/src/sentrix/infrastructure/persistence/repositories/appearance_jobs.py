"""外观取样任务队列。SQL 只出现在这一层。

确认 / 合并一个人之后要给他采外观证据：每个事件挑一张脸送视觉模型，一次约 10 秒，
一个出现在 16 个事件里的人要三分多钟。这件事从请求里挪出来，由这张表记账、后台线程消费。

- **一个人一行**：重复入队（确认之后马上合并）合并成一行，``generation`` 加一；
- **跑到一半又入队**：``finish`` 只删 ``generation`` 没变的那一行，变了就留着重跑；
- **进程中途退出**：留在 ``running`` 的行在下次启动时放回 ``pending``。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sentrix.infrastructure.persistence.database import Database

PENDING = "pending"
RUNNING = "running"
FAILED = "failed"

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 30


def _now() -> datetime:
    return datetime.now(UTC)


class SqlitePersonAppearanceJobs:
    def __init__(self, database: Database) -> None:
        self._database = database

    def enqueue(self, person_id: str) -> None:
        now = _now().isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO person_appearance_jobs"
                " (person_id, status, generation, attempts, next_attempt_at, error, created_at, updated_at)"
                " VALUES (?, ?, 1, 0, '', '', ?, ?)"
                " ON CONFLICT(person_id) DO UPDATE SET status = excluded.status,"
                " generation = person_appearance_jobs.generation + 1, attempts = 0,"
                " next_attempt_at = '', error = '', updated_at = excluded.updated_at",
                (person_id, PENDING, now, now),
            )

    def claim_next(self) -> tuple[str, int] | None:
        """取一个到期任务并标成 ``running``；没有就返回 ``None``。"""
        now = _now().isoformat()
        with self._database.connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT person_id, generation FROM person_appearance_jobs"
                " WHERE status = ? AND next_attempt_at <= ?"
                " ORDER BY updated_at LIMIT 1",
                (PENDING, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE person_appearance_jobs SET status = ?, updated_at = ? WHERE person_id = ?",
                (RUNNING, now, row["person_id"]),
            )
            return str(row["person_id"]), int(row["generation"])

    def finish(self, person_id: str, generation: int) -> None:
        """删掉跑完的任务。跑的过程中又入队过（``generation`` 变了）就不删，留给下一轮。"""
        with self._database.connect() as connection, connection:
            connection.execute(
                "DELETE FROM person_appearance_jobs WHERE person_id = ? AND generation = ?",
                (person_id, generation),
            )

    def fail(self, person_id: str, generation: int, error: str) -> None:
        now = _now()
        with self._database.connect() as connection, connection:
            row = connection.execute(
                "SELECT attempts FROM person_appearance_jobs WHERE person_id = ? AND generation = ?",
                (person_id, generation),
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            status = FAILED if attempts >= MAX_ATTEMPTS else PENDING
            retry_at = now + timedelta(seconds=BACKOFF_SECONDS * (1 << (attempts - 1)))
            connection.execute(
                "UPDATE person_appearance_jobs SET status = ?, attempts = ?, next_attempt_at = ?,"
                " error = ?, updated_at = ? WHERE person_id = ? AND generation = ?",
                (status, attempts, retry_at.isoformat(), error[:500], now.isoformat(), person_id, generation),
            )

    def requeue_running(self) -> int:
        """启动时调用：上次进程退出时还在跑的任务放回队列。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE person_appearance_jobs SET status = ?, updated_at = ? WHERE status = ?",
                (PENDING, _now().isoformat(), RUNNING),
            )
            return int(cursor.rowcount)

    def status(self, person_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM person_appearance_jobs WHERE person_id = ?", (person_id,)
            ).fetchone()
        return None if row is None else str(row["status"])
