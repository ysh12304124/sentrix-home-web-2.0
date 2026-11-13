"""``facts`` 仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py:3397`` 的 ``maintain_fact`` / ``:3432`` ``confirm_fact`` /
``:3455`` ``reject_fact`` / ``:3448`` ``list_facts``。判断逻辑（值是否变了、新行该是
active 还是 pending、revision 取几）在 domain/memories/facts.py，这里只执行。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.memories.facts import Fact, FactStatus
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, subject, predicate, object, status, confidence,"
    " evidence_ids_json, supersedes_fact_id, revision"
)


def _row_to_fact(row: sqlite3.Row) -> Fact:
    try:
        evidence = json.loads(row["evidence_ids_json"] or "[]")
    except json.JSONDecodeError:
        evidence = []
    return Fact(
        id=row["id"],
        scope_id=row["scope_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        status=FactStatus(row["status"]),
        confidence=row["confidence"],
        evidence_ids=tuple(str(item) for item in evidence) if isinstance(evidence, list) else (),
        revision=row["revision"],
        supersedes_fact_id=row["supersedes_fact_id"],
    )


class SqliteFactRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def active_for(self, *, scope_id: str, subject: str, predicate: str) -> Fact | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_facts WHERE scope_id = ? AND subject = ? AND predicate = ?"
                " AND status = 'active' ORDER BY revision DESC LIMIT 1",
                (scope_id, subject, predicate),
            ).fetchone()
        return _row_to_fact(row) if row else None

    def get(self, fact_id: str) -> Fact | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_facts WHERE id = ?", (fact_id,)
            ).fetchone()
        return _row_to_fact(row) if row else None

    def insert(self, fact: Fact) -> Fact:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO facts (id, scope_id, subject, predicate, object, status, confidence,"
                " evidence_ids_json, supersedes_fact_id, revision, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact.id,
                    fact.scope_id,
                    fact.subject,
                    fact.predicate,
                    fact.object,
                    fact.status.value,
                    fact.confidence,
                    json.dumps(list(fact.evidence_ids), ensure_ascii=False),
                    fact.supersedes_fact_id,
                    fact.revision,
                    now,
                    now,
                ),
            )
        stored = self.get(fact.id)
        assert stored is not None
        return stored

    def reinforce(self, fact_id: str, *, evidence_ids: tuple[str, ...], confidence: float) -> Fact:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE facts SET evidence_ids_json = ?, confidence = MAX(confidence, ?),"
                " updated_at = ? WHERE id = ?",
                (
                    json.dumps(list(evidence_ids), ensure_ascii=False),
                    float(confidence),
                    datetime.now(UTC).isoformat(),
                    fact_id,
                ),
            )
        updated = self.get(fact_id)
        assert updated is not None
        return updated

    def set_status(self, fact_id: str, status: str) -> Fact:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE facts SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(UTC).isoformat(), fact_id),
            )
        updated = self.get(fact_id)
        assert updated is not None
        return updated

    def supersede_others(self, *, scope_id: str, subject: str, predicate: str, keep_fact_id: str) -> int:
        """把同 scope 同 (subject, predicate) 的其他 active 行置为 superseded。

        1.0 ``confirm_fact`` 的 UPDATE **不带 scope_id**（``db.py:3434-3438``），
        跨相册的同名事实会被一起作废。2.0 把范围收在本 scope 内。
        见 ``s4-deviations.md`` §2 第 7 项。
        """
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE facts SET status = 'superseded', updated_at = ?"
                " WHERE scope_id = ? AND subject = ? AND predicate = ?"
                " AND status = 'active' AND id != ?",
                (datetime.now(UTC).isoformat(), scope_id, subject, predicate, keep_fact_id),
            )
            return int(cursor.rowcount)

    def purge_unanchored(self, scope_id: str) -> int:
        """删掉 subject 对不上本 scope 任何「已确认人物本名」的事实（1.0 ``db.py:3433``）。

        判据与写入时的门禁是同一条（``canonical_name`` 精确相等、**不看别名**），否则
        「写得进、删不掉」——别名放行一次就会把另一个人的同名事实一起删掉。
        """
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM facts WHERE scope_id = ? AND NOT EXISTS ("
                " SELECT 1 FROM readable_entities WHERE readable_entities.scope_id = facts.scope_id"
                " AND readable_entities.entity_type = 'person' AND readable_entities.status = 'confirmed'"
                " AND readable_entities.canonical_name = facts.subject)",
                (scope_id,),
            )
            return int(cursor.rowcount)

    def list_facts(self, scope_id: str | None, *, limit: int) -> Sequence[Fact]:
        """按 scope 取事实；``scope_id`` 为 ``None`` 时取全部相册（1.0 的行为）。

        1.0 的 ``list_facts(limit)`` 没有 scope 参数，一次返回全库；2.0 加了 scope
        过滤，但**必须保留「不传 = 全部」这条路**——否则「看全部相册的事实」这件事
        在 2.0 做不出来，而 1.0 一直在做。
        """
        where = "" if scope_id is None else " WHERE scope_id = ?"
        params: tuple[object, ...] = (limit,) if scope_id is None else (scope_id, limit)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_facts{where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_fact(row) for row in rows]
