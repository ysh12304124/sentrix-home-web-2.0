"""控制库：账号、会话、反馈、审计、事件与采样。

复用产品侧的 SQLite 迁移器（``infrastructure.persistence.database``），但迁移目录
是本包的 ``migrations/``，与用户数据根完全分开。
"""

from __future__ import annotations

from pathlib import Path

from sentrix.infrastructure.persistence.database import Database

CONTROL_MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class ControlStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    @property
    def database(self) -> Database:
        return self._database

    def migrate(self) -> list[str]:
        return self._database.migrate()

    def ping(self) -> bool:
        try:
            with self._database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001 - ready 只看可用性，不吞栈到调用方
            return False
        return True

    def account_count(self, *, role: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM accounts"
        parameters: tuple[object, ...] = ()
        if role is not None:
            sql += " WHERE role = ? AND disabled = 0"
            parameters = (role,)
        with self._database.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return int(row["n"]) if row is not None else 0

    def schema_version(self) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return str(row["version"]) if row is not None else ""
