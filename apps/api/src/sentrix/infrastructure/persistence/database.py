"""SQLite connection factory and versioned migration runner.

1.0 had 59 tables built by ``CREATE TABLE IF NOT EXISTS`` plus ad-hoc
``ALTER TABLE ADD COLUMN`` patching at runtime, with no version record.  This
runner replaces that: every schema change is a numbered .sql file applied once,
inside a transaction, and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

from sentrix.domain.media.capture_time import time_sort_key, within_time_bounds

BUSY_TIMEOUT_MS = 30_000
_connection_locks: dict[Path, threading.RLock] = {}
_connection_locks_guard = threading.Lock()


class MigrationError(RuntimeError):
    """迁移不可执行——宁可启动失败，也不静默跑出一个没有 schema 的库。"""


def _statements(sql_text: str) -> list[str]:
    """把迁移脚本切成语句，并丢掉整行注释。

    不用 ``Connection.executescript``：它会先把挂起事务隐式 COMMIT 掉，那样
    ``BEGIN IMMEDIATE`` 拿到的写锁会在脚本执行前就释放，串行化与回滚保护同时失效。
    代价是这里不解析字符串字面量——迁移脚本里不要写含 ``;`` 的字符串常量。
    """
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


class Database:
    def __init__(self, path: Path, migrations_dir: Path) -> None:
        self.path = Path(path)
        self.migrations_dir = Path(migrations_dir)
        # 同一文件的实例共享连接锁，避免并发切换WAL及SQLite文件句柄竞争。
        # 图谱查询会在同一线程嵌套读取，因此连接锁允许重入。
        with _connection_locks_guard:
            self._connect_lock = _connection_locks.setdefault(self.path.resolve(), threading.RLock())

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._connect_lock:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "capture_sort_key",
                1,
                lambda value: time_sort_key(datetime.fromisoformat(value) if value else None),
                deterministic=True,
            )
            connection.create_function("capture_in_bounds", 3, _capture_in_bounds, deterministic=True)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            try:
                yield connection
            finally:
                connection.close()

    def migrations(self) -> list[tuple[str, Path]]:
        """磁盘上的 ``(版本号, 脚本路径)``，按版本号排序。

        目录不存在、或一个 ``.sql`` 都没有，都是硬错误：那说明部署漏了 package-data，
        或迁移根本没被带上。放过去只会让服务带着空 schema 起来，直到第一次写入才炸。
        """
        if not self.migrations_dir.is_dir():
            raise MigrationError(f"迁移目录不存在: {self.migrations_dir}")
        scripts = sorted(self.migrations_dir.glob("*.sql"))
        if not scripts:
            raise MigrationError(f"迁移目录里没有任何 .sql: {self.migrations_dir}")
        versions = [(script.stem.split("_", 1)[0], script) for script in scripts]
        if len({version for version, _ in versions}) != len(versions):
            raise MigrationError("迁移版本号重复")
        return versions

    def applied_versions(self) -> set[str]:
        with self.connect() as connection:
            return {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}

    def migrate(self) -> list[str]:
        pending = self.migrations()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        applied: list[str] = []
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY,"
                " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            for version, script in pending:
                if self._apply(connection, script, version):
                    applied.append(version)
        return applied

    @staticmethod
    def _apply(connection: sqlite3.Connection, script: Path, version: str) -> bool:
        """一个迁移一个事务，且「读已应用集合」也在同一事务里。

        ``BEGIN IMMEDIATE`` 先拿写锁再读：两个 worker 同时启动时，后到的会阻塞在锁上，
        拿到锁时前一个已经提交，于是看见版本号已存在并跳过，而不是重复执行 DDL。
        若改成在 autocommit 下先读、再另起事务写，就留下 TOCTOU 窗口，撞上
        ``table ... already exists``。
        """
        previous = connection.isolation_level
        connection.isolation_level = None  # 关掉 sqlite3 的隐式事务，边界由本函数管
        try:
            connection.execute("BEGIN IMMEDIATE")
            done = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if done is not None and script.name == "0015_text_vectors.sql":
                text_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='text_vectors'"
                ).fetchone()
                if text_table is None:
                    # 已发布的xy曾用0015记录拍摄时间迁移；main的0015是文本向量。
                    # 在同一事务内按已知表结构改记编号，再创建文本表。
                    capture_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
                    place_columns = {row[1] for row in connection.execute("PRAGMA table_info(asset_places)")}
                    if (
                        "capture_time_json" not in capture_columns
                        or "nearest_evidence_json" not in place_columns
                    ):
                        raise MigrationError("0015记录与文本向量/拍摄时间表结构不一致")
                    connection.execute("UPDATE schema_migrations SET version='0018' WHERE version='0015'")
                    done = None
            if done is not None and script.name == "0016_video_jobs.sql":
                video_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_jobs'"
                ).fetchone()
                if video_table is None:
                    capture_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
                    place_columns = {row[1] for row in connection.execute("PRAGMA table_info(asset_places)")}
                    if (
                        "capture_time_json" not in capture_columns
                        or "nearest_evidence_json" not in place_columns
                    ):
                        raise MigrationError("0016记录与视频任务/拍摄时间表结构不一致")
                    connection.execute("UPDATE schema_migrations SET version='0018' WHERE version='0016'")
                    done = None
            if done is not None:
                connection.execute("COMMIT")
                return False
            for statement in _statements(script.read_text(encoding="utf-8")):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            connection.execute("COMMIT")
            return True
        except BaseException:
            with suppress(sqlite3.OperationalError):  # BEGIN 就没成功时没有事务可回滚
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.isolation_level = previous


def _capture_in_bounds(value: str | None, start: str | None, end: str | None) -> bool:
    if not value:
        return False
    moment = datetime.fromisoformat(value)
    bound = datetime.fromisoformat(start or end or "0001-01-01")
    if start and end:
        return (
            within_time_bounds((datetime.fromisoformat(start), datetime.fromisoformat(end)), moment) is True
        )
    if bound.utcoffset() is None:
        moment = moment.replace(tzinfo=None)
    elif moment.utcoffset() is None:
        return False
    return moment >= bound if start else moment < bound
