"""结果集的 SQLite 仓储。

1.0 把结果集同时放在内存 dict 和 ``agent_result_sets`` 表里（``result_set.py:167-220``），
读取时内存优先。2.0 只留 SQLite 一份：内存副本带来的是「同一份数据两个真相」，而它换来
的加速在一次单行查询上量不出来。

1.0 还有两处没写进库的字段（``ordering`` / ``owner`` / ``revision`` / ``page_size`` /
``shown``），进程重启后静默变默认值。2.0 的表列就是结果集的全部字段，不存在这种半持久化。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from sentrix.domain.assistant.result_set import ResultSet
from sentrix.infrastructure.persistence.database import Database
from sentrix.runtime.turn import current_turn


def _row_to_result_set(row: sqlite3.Row) -> ResultSet:
    return ResultSet(
        id=row["id"],
        scope_id=row["scope_id"],
        query=row["query"],
        asset_ids=tuple(json.loads(row["asset_ids_json"] or "[]")),
        created_at=datetime.fromisoformat(row["created_at"]),
        evidence_classes=dict(json.loads(row["evidence_classes_json"] or "{}")),
    )


class SqliteResultSetRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, result_set: ResultSet) -> None:
        context = current_turn()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO agent_result_sets"
                " (id, scope_id, query, asset_ids_json, evidence_classes_json, total, created_at,"
                "  turn_id, attempt_no)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    result_set.id,
                    result_set.scope_id,
                    result_set.query,
                    json.dumps(list(result_set.asset_ids)),
                    json.dumps(dict(result_set.evidence_classes)),
                    len(result_set.asset_ids),
                    result_set.created_at.isoformat(),
                    context.turn_id if context is not None else "",
                    context.attempt_no if context is not None else 0,
                ),
            )

    def purge_other_attempts(self, turn_id: str, attempt_no: int) -> int:
        """同一回合只保留当前尝试的结果集。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM agent_result_sets WHERE turn_id = ? AND attempt_no <> ?",
                (turn_id, attempt_no),
            )
        return cursor.rowcount

    def get(self, result_set_id: str) -> ResultSet | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM readable_agent_result_sets WHERE id = ?", (result_set_id,)
            ).fetchone()
        return _row_to_result_set(row) if row else None

    def list_for_scope(self, scope_id: str, limit: int) -> Sequence[ResultSet]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM readable_agent_result_sets WHERE scope_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (scope_id, max(1, limit)),
            ).fetchall()
        return [_row_to_result_set(row) for row in rows]
