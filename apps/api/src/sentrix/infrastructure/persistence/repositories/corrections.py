"""权威记忆修正、乐观并发检查与持久同步记录。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime

from sentrix.domain.errors import DomainError
from sentrix.infrastructure.persistence.database import Database


class CorrectionConflict(DomainError):
    code = "conflict"
    status_code = 409


class CorrectionMissing(DomainError):
    code = "not_found"
    status_code = 404


TABLES = {
    "observation": "observations",
    "event": "events",
    "entity": "entities",
    "fact": "facts",
    "relationship": "relationships",
}
FIELDS = {
    "observation": {"caption", "place", "activity", "event_type", "ocr_text", "objects", "clothing"},
    "event": {"title", "summary", "place", "activity", "event_type", "time_start", "time_end"},
    "entity": {"canonical_name", "summary"},
}


class SqliteMemoryCorrections:
    def __init__(self, database: Database) -> None:
        self._database = database

    def read(self, kind: str, target_id: str) -> dict[str, object] | None:
        table = TABLES.get(kind)
        if table is None:
            return None
        with self._database.connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in ("objects", "clothing"):
            if name + "_json" in result:
                result[name] = json.loads(str(result[name + "_json"]))
        return result

    def correct(
        self, kind: str, target_id: str, revision: int, changes: Mapping[str, object], reason: str
    ) -> dict[str, object]:
        if kind not in FIELDS or not changes or set(changes) - FIELDS[kind]:
            raise ValueError("unsupported or empty correction fields")
        table = TABLES[kind]
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
            if row is None:
                raise CorrectionMissing("memory not found")
            before = dict(row)
            if before["revision"] != revision:
                raise CorrectionConflict("记忆已更新，请重新读取后再保存")
            if kind == "event":
                start = changes.get("time_start", before.get("time_start"))
                end = changes.get("time_end", before.get("time_end"))
                if start and end:
                    first, last = datetime.fromisoformat(str(start)), datetime.fromisoformat(str(end))
                    if (first.utcoffset() is None) != (last.utcoffset() is None) or last < first:
                        raise ValueError("开始与结束时间的时区必须一致，结束时间不能早于开始时间")
            if (
                kind == "entity"
                and "canonical_name" in changes
                and not str(changes["canonical_name"]).strip()
            ):
                raise ValueError("实体名称不能为空")
            values: list[object] = []
            columns = []
            for key, value in changes.items():
                column = key + "_json" if key in {"objects", "clothing"} else key
                columns.append(f"{column}=?")
                values.append(
                    json.dumps(value, ensure_ascii=False) if key in {"objects", "clothing"} else value
                )
            now = datetime.now(UTC).isoformat()
            conn.execute(
                f"UPDATE {table} SET {','.join(columns)},revision=revision+1,updated_at=? WHERE id=?",
                (*values, now, target_id),
            )
            after = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone())
            # 旧文本向量不能继续冒充新记忆的向量；视觉向量不受文字修正影响。
            if kind in {"observation", "event"}:
                conn.execute(
                    "DELETE FROM text_vectors WHERE source_type=? AND source_id=?", (kind, target_id)
                )
            record_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,"
                "before_json,after_json,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    before["scope_id"],
                    kind,
                    target_id,
                    json.dumps(before),
                    json.dumps(after),
                    reason,
                    now,
                    now,
                ),
            )
        return {"id": record_id, "scope_id": before["scope_id"], "revision": after["revision"]}

    @contextmanager
    def track(self, scope_id: str, kind: str, target_id: str) -> Iterator[None]:
        record_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        before = self._snapshot(scope_id, kind, target_id)
        with self._database.connect() as conn, conn:
            conn.execute(
                "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,"
                "before_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (record_id, scope_id, kind, target_id, json.dumps(before), now, now),
            )
        try:
            yield
        finally:
            after = self._snapshot(scope_id, kind, target_id)
            with self._database.connect() as conn, conn:
                conn.execute(
                    "UPDATE memory_corrections SET after_json=?,updated_at=? WHERE id=?",
                    (json.dumps(after), datetime.now(UTC).isoformat(), record_id),
                )

    def _snapshot(self, scope_id: str, kind: str, target_id: str) -> list[dict[str, object]]:
        table = TABLES.get(kind)
        with self._database.connect() as conn:
            if table:
                result = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,))]
                if kind == "entity":
                    result.extend(
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM entity_properties WHERE entity_id=?", (target_id,)
                        )
                    )
                return result
            # 家庭关系与实体属性有自己的版本表，保留完整范围快照供回溯。
            if kind == "family":
                return [
                    dict(r)
                    for table in (
                        "family_relationships",
                        "family_person_memberships",
                        "family_person_portraits",
                    )
                    for r in conn.execute(f"SELECT * FROM {table} WHERE scope_id=?", (scope_id,))
                ]
            if kind == "face":
                return [dict(r) for r in conn.execute("SELECT * FROM face_clusters WHERE id=?", (target_id,))]
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM entity_properties WHERE entity_id=?", (target_id,))
            ]

    def records(self, scope_id: str, limit: int) -> Sequence[dict[str, object]]:
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_corrections WHERE scope_id=? ORDER BY created_at DESC LIMIT ?",
                (scope_id, limit),
            ).fetchall()
        return [
            {**dict(row), "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"])}
            for row in rows
        ]

    def pending_scopes(self) -> Sequence[str]:
        with self._database.connect() as conn:
            return [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT scope_id FROM memory_corrections WHERE status='pending' LIMIT 100"
                )
            ]

    def refresh_people(self, scope_id: str) -> None:
        with self._database.connect() as conn, conn:
            for row in conn.execute(
                "SELECT id,people_json FROM observations WHERE scope_id=?", (scope_id,)
            ).fetchall():
                people = json.loads(row["people_json"] or "[]")
                # 自由描述保留；有身份绑定的快照必须重读确认结果，避免改名/拒绝后旧身份复活。
                kept = [p for p in people if not isinstance(p, dict) or not p.get("entity_id")]
                confirmed = conn.execute(
                    "SELECT DISTINCT e.id,e.canonical_name FROM entity_mentions m "
                    "JOIN entities e ON e.id=m.entity_id "
                    "WHERE m.observation_id=? AND e.status='confirmed'",
                    (row["id"],),
                ).fetchall()
                kept.extend(
                    {"entity_id": r["id"], "name": r["canonical_name"], "status": "confirmed"}
                    for r in confirmed
                )
                if kept != people:
                    conn.execute(
                        "UPDATE observations SET people_json=?,revision=revision+1,updated_at=? WHERE id=?",
                        (json.dumps(kept, ensure_ascii=False), datetime.now(UTC).isoformat(), row["id"]),
                    )
            self._refresh_relationship_claims(conn, scope_id)

    @staticmethod
    def _refresh_relationship_claims(conn: sqlite3.Connection, scope_id: str) -> None:
        """关系声明是派生记忆；用户撤回不能被旧声明或旧通用关系恢复。"""
        overrides = {
            tuple(sorted((r[0], r[1])))
            for r in conn.execute(
                "SELECT subject_entity_id,object_entity_id FROM family_relationships "
                "WHERE scope_id=? AND source='user_override' UNION "
                "SELECT left_entity_id,right_entity_id FROM family_relationship_blocks WHERE scope_id=?",
                (scope_id, scope_id),
            )
        }
        candidates = [
            tuple(r)
            for r in conn.execute(
                "SELECT subject_entity_id,predicate,object_entity_id,confidence FROM relationships "
                "WHERE scope_id=? AND status='active'",
                (scope_id,),
            )
            if tuple(sorted((r[0], r[2]))) not in overrides
        ]
        desired = []
        for candidate in candidates:
            subject, predicate, object_id, _ = candidate
            target = conn.execute("SELECT canonical_name FROM entities WHERE id=?", (object_id,)).fetchone()
            names = {str(target[0])} if target else set()
            for prop in conn.execute(
                "SELECT value_json FROM entity_properties WHERE entity_id=? "
                "AND property_key='aliases' AND status='active'",
                (object_id,),
            ):
                aliases = json.loads(prop[0])
                if isinstance(aliases, list):
                    names.update(str(name) for name in aliases)
            existing = conn.execute(
                "SELECT value_entity_id,value_text FROM semantic_claims "
                "WHERE scope_id=? AND person_id=? AND predicate=? "
                "AND dimension='relationship' AND confidence_source IN "
                "('user-confirmed','memory-correction')",
                (scope_id, subject, predicate),
            ).fetchall()
            # 旧接口确认 pending 关系不创建 claim；这里只同步已经存在的旧声明。
            if any(row[0] == object_id or row[1] in names for row in existing):
                desired.append(candidate)
        desired.extend(
            tuple(r)
            for r in conn.execute(
                "SELECT subject_entity_id,predicate,object_entity_id,confidence FROM family_relationships "
                "WHERE scope_id=? AND state='effective' AND locked=1",
                (scope_id,),
            )
        )
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE semantic_claims SET status='retracted',revision=revision+1,updated_at=? "
            "WHERE scope_id=? AND dimension='relationship' AND status IN ('active','pending') "
            "AND confidence_source IN ('user-confirmed','memory-correction')",
            (now, scope_id),
        )
        for subject, predicate, object_id, confidence in desired:
            entity = conn.execute(
                "SELECT canonical_name FROM entities WHERE id=? AND status='confirmed'", (object_id,)
            ).fetchone()
            if entity is None:
                continue
            claim_id = (
                "corrected_relationship_"
                + hashlib.sha256(f"{scope_id}|{subject}|{predicate}|{object_id}".encode()).hexdigest()[:24]
            )
            conn.execute(
                "INSERT INTO semantic_claims(id,scope_id,person_id,dimension,predicate,value_text,"
                "normalized_value_text,value_entity_id,status,confidence,confidence_source,"
                "created_at,updated_at) "
                "VALUES(?,?,?,'relationship',?,?,?,?,'active',?,'memory-correction',?,?) "
                "ON CONFLICT(id) DO UPDATE SET value_text=excluded.value_text,"
                "normalized_value_text=excluded.normalized_value_text,status='active',"
                "confidence=excluded.confidence,updated_at=excluded.updated_at",
                (
                    claim_id,
                    scope_id,
                    subject,
                    predicate,
                    entity[0],
                    entity[0],
                    object_id,
                    confidence,
                    now,
                    now,
                ),
            )

    def affected_observations(self, scope_id: str) -> Sequence[str]:
        with self._database.connect() as conn:
            ids: set[str] = set()
            for row in conn.execute(
                "SELECT target_kind,target_id FROM memory_corrections WHERE scope_id=? AND status='pending'",
                (scope_id,),
            ):
                kind, target = row
                if kind == "observation":
                    ids.add(target)
                elif kind == "event":
                    ids.update(
                        r[0]
                        for r in conn.execute(
                            "SELECT observation_id FROM event_observations WHERE event_id=?", (target,)
                        )
                    )
                elif kind in {"entity", "family"}:
                    ids.update(
                        r[0]
                        for r in conn.execute(
                            "SELECT observation_id FROM entity_mentions WHERE entity_id=?", (target,)
                        )
                    )
                    ids.update(
                        r[0]
                        for r in conn.execute(
                            "SELECT observation_id FROM entity_observations WHERE entity_id=?", (target,)
                        )
                    )
                elif kind == "face":
                    ids.update(
                        r[0]
                        for r in conn.execute(
                            "SELECT observation_id FROM face_instances WHERE cluster_id=?", (target,)
                        )
                        if r[0]
                    )
            return sorted(ids)

    def affected_events(self, scope_id: str) -> Sequence[str]:
        with self._database.connect() as conn:
            return [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT target_id FROM memory_corrections "
                    "WHERE scope_id=? AND status='pending' AND target_kind='event'",
                    (scope_id,),
                )
            ]

    def finish(self, scope_id: str, error: str) -> None:
        with self._database.connect() as conn, conn:
            conn.execute(
                "UPDATE memory_corrections SET status=?,error=?,updated_at=? "
                "WHERE scope_id=? AND status='pending'",
                ("pending" if error else "synced", error, datetime.now(UTC).isoformat(), scope_id),
            )
