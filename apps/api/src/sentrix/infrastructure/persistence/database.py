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
        # 图谱/家庭图谱会在同一线程嵌套读取，因此连接锁必须是可重入的 RLock。
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
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
        return column in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

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
            record_version = version
            # A prior database can contain the numeric 0020 marker while
            # agent_result_sets still lacks the attempt ownership columns.
            # Validate the schema as well as the ledger and repair idempotently.
            if script.name == "0020_result_set_attempts.sql":
                table_exists = Database._table_exists(connection, "agent_result_sets")
                has_turn_id = table_exists and Database._column_exists(
                    connection, "agent_result_sets", "turn_id"
                )
                has_attempt_no = table_exists and Database._column_exists(
                    connection, "agent_result_sets", "attempt_no"
                )
                index_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' "
                    "AND name='idx_agent_result_sets_turn'"
                ).fetchone() is not None
                if not has_turn_id:
                    connection.execute(
                        "ALTER TABLE agent_result_sets ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''"
                    )
                if not has_attempt_no:
                    connection.execute(
                        "ALTER TABLE agent_result_sets ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 0"
                    )
                if not index_exists:
                    connection.execute(
                        "CREATE INDEX idx_agent_result_sets_turn "
                        "ON agent_result_sets(turn_id, attempt_no)"
                    )
                if done is None:
                    connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
                    )
                elif not (has_turn_id and has_attempt_no and index_exists):
                    connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?)",
                        ("0020_result_set_attempts_schema",),
                    )
                connection.execute("COMMIT")
                return not (done is not None and has_turn_id and has_attempt_no and index_exists)
            # Two published hpq-only migrations used the same numeric revisions as
            # newer main migrations. If the old revision is recorded but the main
            # column is absent, apply the main migration under a unique ledger key.
            if (
                done is not None
                and script.name == "0021_postprocess_backoff.sql"
                and not Database._column_exists(connection, "scope_postprocess", "next_attempt_at")
            ):
                done, record_version = None, "0021_postprocess_backoff"
            if (
                done is not None
                and script.name == "0022_turn_failure_count.sql"
                and not Database._column_exists(connection, "agent_turns", "failure_count")
            ):
                done, record_version = None, "0022_turn_failure_count"
            # 本分支与 main 在 0015–0018 使用了不同的迁移编号。main 的数据库里
            # 同名版本号已经被别的迁移占用，但本分支的表/列还没建：不能跳过，也
            # 不能覆盖原版本号，只能执行后用带后缀的版本记录。
            if (
                done is not None
                and script.name == "0015_import_jobs.sql"
                and not Database._table_exists(connection, "import_jobs")
            ):
                done, record_version = None, "0015_import_jobs"
            if (
                done is not None
                and script.name == "0016_import_replay_keys.sql"
                and not Database._table_exists(connection, "scope_postprocess")
            ):
                done, record_version = None, "0016_import_replay_keys"
            if (
                done is not None
                and script.name == "0017_turn_recovery.sql"
                and not Database._column_exists(connection, "agent_turns", "idempotency_key")
            ):
                done, record_version = None, "0017_turn_recovery"
            if (
                done is not None
                and script.name == "0018_memory_rotation.sql"
                and not Database._table_exists(connection, "memory_rotation_sequences")
            ):
                done, record_version = None, "0018_memory_rotation"
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
            if (
                done is None
                and script.name == "0024_text_vectors.sql"
                and Database._table_exists(connection, "text_vectors")
            ):
                connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                connection.execute("COMMIT")
                return True
            if (
                done is None
                and script.name == "0025_video_jobs.sql"
                and Database._table_exists(connection, "video_jobs")
            ):
                connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                connection.execute("COMMIT")
                return True
            if (
                done is None
                and script.name == "0026_video_job_config_hash.sql"
                and Database._column_exists(connection, "video_jobs", "config_hash")
            ):
                connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                connection.execute("COMMIT")
                return True
            if done is None and script.name == "0027_capture_time.sql":
                # main 用 0018 记录同一组列，本分支从 0027 起接。跨分支升级时
                # 列已经存在就只补版本记录，绝不重复 ALTER。
                capture_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
                place_columns = {row[1] for row in connection.execute("PRAGMA table_info(asset_places)")}
                if "capture_time_json" in capture_columns and "nearest_evidence_json" in place_columns:
                    connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                    connection.execute("COMMIT")
                    return True
            if done is not None:
                connection.execute("COMMIT")
                return False
            if script.name == "0028_media_deletion_history.sql":
                columns = {row[1] for row in connection.execute("PRAGMA table_info(media_cleanup_jobs)")}
                if {"scope_id", "file_name", "media_type", "deleted_at"} <= columns:
                    connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                    connection.execute("COMMIT")
                    return True
            if script.name == "0029_memory_corrections.sql":
                entity_columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
                if Database._table_exists(connection, "memory_corrections") and "revision" in entity_columns:
                    connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                    connection.execute("COMMIT")
                    return True
            if script.name == "0030_result_set_graph_provenance.sql":
                columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_result_sets)")}
                if "graph_provenance_json" in columns:
                    # Mac legacy databases gained this column before the
                    # versioned migration. Record it without running ALTER twice.
                    connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                    connection.execute("COMMIT")
                    return True
            for statement in _statements(script.read_text(encoding="utf-8")):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (record_version,))
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
