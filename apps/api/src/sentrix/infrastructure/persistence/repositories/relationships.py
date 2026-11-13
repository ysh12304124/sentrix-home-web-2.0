"""关系仓储。SQL 只出现在这一层。

逐行移植 1.0 ``backend/db.py:5590`` / ``:5601`` / ``:5970`` / ``:6014`` / ``:5609``。

**这一层只写关系行。** 1.0 的 ``create_relationship`` 还会（``status == 'active'`` 时）
调 ``maintain_relationship_claim`` 往事实/声明层写；2.0 拆开，那一半由 application
用例编排——``infrastructure-purity`` 契约不允许基础设施层直接调用例。
**且不回滚**（用户 2026-09-28 裁决）：claim 维护失败时这一行留着，「关系已确认但事实层
没有对应声明」是可能出现且被接受的中间状态。

**``confirm`` 里那段 supersede 够不着（2026-09-28 实测，留着不删）。** ``create`` 在
``(scope, subject, predicate, object)`` 上按 ``status IN (active, pending)`` 去重，
所以同一三元组只会有一行，「把其它 active 的顶掉」那条 UPDATE 匹配不到东西。

不删的理由与 portraits / moments 里去掉的两条兜底**不同**：那两条是被**表约束**挡死的
（外键 + ``NOT NULL``），是 schema 保证，永远走不到；这一段只被 ``create`` 的**去重策略**
挡住，而 schema 明确支持同一三元组多行——``supersedes_relationship_id`` 就是那条链。
策略会变、schema 不会。所以留着它，但**不假装它被用例覆盖到了**。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import RelationshipRecord
from sentrix.infrastructure.persistence.database import Database

# ``r.*`` 之外的别名只在 list 用；单条读取不需要 JOIN，用这张清单就够。
_COLUMNS = (
    "id, scope_id, subject_entity_id, predicate, object_entity_id, inverse_predicate,"
    " status, confidence, evidence_ids_json, revision, created_at, updated_at"
)

# ``retracted`` 是「撤回」不是「不存在」：留痕，但不再作为边参与图谱。
RETRACTED = "retracted"
ACTIVE = "active"
PENDING = "pending"
SUPERSEDED = "superseded"


def _new_id() -> str:
    """1.0 ``make_id("rel")``。"""
    return f"rel_{uuid.uuid4().hex[:12]}"


def _load_ids(raw: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


class SqliteRelationshipRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- 读 ----------------------------------------------------------------

    def list_person_relationships(self, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        scope_clause = " AND r.scope_id = ?" if scope_id else ""
        params: tuple[str, ...] = (scope_id,) if scope_id else ()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name"
                " FROM readable_relationships r"
                " JOIN readable_entities s ON s.id = r.subject_entity_id"
                " JOIN readable_entities o ON o.id = r.object_entity_id"
                " WHERE s.entity_type = 'person' AND o.entity_type = 'person'"
                f" AND r.status != '{RETRACTED}'{scope_clause}"
                " ORDER BY r.updated_at DESC",
                params,
            ).fetchall()
        return [record for row in rows if (record := _record(row, names=True)) is not None]

    def list_relationships(self, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """1.0 ``db.py:5590`` 的 ``entity_id=None`` 那一支。**没有 status 过滤。**"""
        scope_clause = " WHERE r.scope_id = ?" if scope_id else ""
        params: tuple[str, ...] = (scope_id,) if scope_id else ()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name"
                " FROM readable_relationships r"
                " JOIN readable_entities s ON s.id = r.subject_entity_id"
                " JOIN readable_entities o ON o.id = r.object_entity_id"
                f"{scope_clause} ORDER BY r.updated_at DESC",
                params,
            ).fetchall()
        return [record for row in rows if (record := _record(row, names=True)) is not None]

    def list_for_entity(self, entity_id: str, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        scope_clause = " AND r.scope_id = ?" if scope_id else ""
        params: tuple[str, ...] = (entity_id, entity_id, scope_id) if scope_id else (entity_id, entity_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name"
                " FROM readable_relationships r"
                " JOIN readable_entities s ON s.id = r.subject_entity_id"
                " JOIN readable_entities o ON o.id = r.object_entity_id"
                " WHERE (r.subject_entity_id = ? OR r.object_entity_id = ?)"
                f"{scope_clause} ORDER BY r.updated_at DESC",
                params,
            ).fetchall()
        return [record for row in rows if (record := _record(row, names=True)) is not None]

    def get(self, relationship_id: str) -> RelationshipRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_relationships WHERE id = ?", (relationship_id,)
            ).fetchone()
        return _record(row)

    # ---- 写 ----------------------------------------------------------------

    def create(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_ids: Sequence[str] = (),
        confidence: float = 0.5,
        status: str = PENDING,
    ) -> RelationshipRecord:
        # 1.0 是 ``list(dict.fromkeys(...))``：去重且**保序**。用 dict 而不是 set，
        # 因为证据顺序会在证据包里显示出来。
        wanted = list(dict.fromkeys(evidence_ids))
        scope_id = self._common_scope(subject_entity_id, object_entity_id)
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            existing = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_relationships"
                " WHERE scope_id = ? AND subject_entity_id = ? AND predicate = ?"
                " AND object_entity_id = ? AND status IN (?, ?)"
                " ORDER BY revision DESC LIMIT 1",
                (scope_id, subject_entity_id, predicate, object_entity_id, ACTIVE, PENDING),
            ).fetchone()
            if existing is not None:
                merged = list(dict.fromkeys([*_load_ids(existing["evidence_ids_json"]), *wanted]))
                next_status = ACTIVE if status == ACTIVE else str(existing["status"])
                connection.execute(
                    "UPDATE relationships SET status = ?, evidence_ids_json = ?,"
                    " confidence = MAX(confidence, ?), updated_at = ?, revision = revision + 1"
                    " WHERE id = ?",
                    (
                        next_status,
                        json.dumps(merged, ensure_ascii=False),
                        float(confidence or 0),
                        now,
                        existing["id"],
                    ),
                )
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM readable_relationships WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                relationship_id = _new_id()
                connection.execute(
                    "INSERT INTO relationships("
                    " id, scope_id, subject_entity_id, predicate, object_entity_id, status,"
                    " confidence, evidence_ids_json, supersedes_relationship_id, revision,"
                    " created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)",
                    (
                        relationship_id,
                        scope_id,
                        subject_entity_id,
                        predicate,
                        object_entity_id,
                        status,
                        float(confidence or 0),
                        json.dumps(wanted, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM readable_relationships WHERE id = ?", (relationship_id,)
                ).fetchone()
        record = _record(row)
        assert record is not None, "刚写进去的那条关系读不回来"
        return record

    def confirm(self, relationship_id: str) -> RelationshipRecord | None:
        current = self.get(relationship_id)
        if current is None:
            return None
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            # 同一对人在同一谓词上只能有一条生效的：先把其它 active 的顶掉。
            connection.execute(
                "UPDATE relationships SET status = ?, updated_at = ?"
                " WHERE subject_entity_id = ? AND predicate = ? AND object_entity_id = ?"
                " AND status = ? AND id != ?",
                (
                    SUPERSEDED,
                    now,
                    current.subject_entity_id,
                    current.predicate,
                    current.object_entity_id,
                    ACTIVE,
                    relationship_id,
                ),
            )
            connection.execute(
                "UPDATE relationships SET status = ?, updated_at = ? WHERE id = ?",
                (ACTIVE, now, relationship_id),
            )
        return self.get(relationship_id)

    def retract(self, relationship_id: str) -> RelationshipRecord | None:
        if self.get(relationship_id) is None:
            return None
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE relationships SET status = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (RETRACTED, datetime.now(UTC).isoformat(), relationship_id),
            )
        return self.get(relationship_id)

    # ---- 跨空间守卫 ----------------------------------------------------------

    def _common_scope(self, subject_entity_id: str, object_entity_id: str) -> str:
        """两端必须在同一个记忆空间，返回那个空间（1.0 ``db.py:5975``）。

        **实体查不到也抛**——1.0 在这里把「查不到」与「不同空间」并成同一个 ``ValueError``
        （``if not subject or not object_entity or ... != ...``），照搬：两端都有外键，
        查不到本来就意味着调用方拿错了 id，与跨空间一样是编程错误而不是数据状态。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, scope_id FROM readable_entities WHERE id IN (?, ?)",
                (subject_entity_id, object_entity_id),
            ).fetchall()
        scopes = {str(row["scope_id"]) for row in rows}
        if len(rows) != 2 or len(scopes) != 1:
            raise ValueError("relationship entities must belong to the same memory space")
        return scopes.pop()


def _record(row: sqlite3.Row | None, *, names: bool = False) -> RelationshipRecord | None:
    if row is None:
        return None
    keys = set(row.keys())
    return RelationshipRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        subject_entity_id=row["subject_entity_id"],
        predicate=row["predicate"],
        object_entity_id=row["object_entity_id"],
        inverse_predicate=row["inverse_predicate"],
        status=row["status"],
        confidence=float(row["confidence"]),
        evidence_ids=_load_ids(row["evidence_ids_json"]),
        revision=int(row["revision"]),
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
        subject_name=str(row["subject_name"]) if names and "subject_name" in keys else "",
        object_name=str(row["object_name"]) if names and "object_name" in keys else "",
    )
