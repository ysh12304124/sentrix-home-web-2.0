"""MemorySpace 仓储。2.0 是空库，只需 ensure/list。

**只剩 SQL。** 记录类型与端口都搬去了 ``domain/spaces``（Task 76）：``MemorySpace`` 是
领域的数据，不是这个适配器的返回形状——``include_in_people`` 那条规则（评测空间聚出来的
人脸不进人物视图）由两个仓储按它过滤，那是领域规则，只是恰好存在一行数据里。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.spaces.models import MemorySpace
from sentrix.infrastructure.persistence.database import Database


class SqliteSpaceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def ensure(self, scope_id: str, name: str, kind: str = "household") -> str:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'active', ?, ?)"
                " ON CONFLICT(id) DO NOTHING",
                (scope_id, name, kind, now, now),
            )
        return scope_id

    def list_all(self) -> Sequence[MemorySpace]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, kind, status, created_at FROM memory_spaces ORDER BY created_at"
            ).fetchall()
        return [
            MemorySpace(row["id"], row["name"], row["kind"], row["status"], row["created_at"]) for row in rows
        ]

    def get(self, scope_id: str) -> MemorySpace | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, name, kind, status, created_at FROM memory_spaces WHERE id = ?", (scope_id,)
            ).fetchone()
        return (
            MemorySpace(row["id"], row["name"], row["kind"], row["status"], row["created_at"])
            if row
            else None
        )
