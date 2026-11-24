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
from sentrix.domain.identity.models import ALIAS_PROPERTY_KEY, EntityPropertyRecord, EntityRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository

# 写入还没提交的同步任务：它比任何 ISO 时间都大，所以永远不会被当成「到期」。
WRITING = "~writing"


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
JSON_FIELDS = {"objects", "clothing", "tags", "entities"}
FIELDS = {
    "observation": {
        "caption",
        "short_caption",
        "detail_caption",
        "place",
        "activity",
        "event_type",
        "ocr_text",
        "objects",
        "clothing",
        "tags",
        "entities",
    },
    "event": {"title", "summary", "place", "activity", "event_type", "time_start", "time_end"},
    "entity": {"canonical_name", "summary"},
}


class SqliteMemoryCorrections:
    def __init__(self, database: Database, entities: SqliteEntityRepository) -> None:
        self._database = database
        self._entities = entities

    def rename_person(self, entity_id: str, name: str) -> tuple[EntityRecord, tuple[str, ...], str] | None:
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            before = self._entity_snapshot(conn, entity_id)
            written = self._entities.rename_person_in_transaction(conn, entity_id, name)
            if written is None:
                return None
            aliases = self._entities.person_aliases_in_transaction(conn, entity_id)
            job_id = ""
            if before[0]["canonical_name"] != written.canonical_name:
                job_id = self._enqueue_name_sync(conn, written.scope_id, entity_id, before)
            return written, aliases, job_id

    def save_aliases(
        self, entity_id: str, value: object, evidence_ids: Sequence[str]
    ) -> tuple[EntityPropertyRecord, str]:
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            before = self._entity_snapshot(conn, entity_id)
            written = self._entities.set_property_in_transaction(
                conn, entity_id, ALIAS_PROPERTY_KEY, value, evidence_ids=evidence_ids
            )
            job_id = self._enqueue_name_sync(conn, str(before[0]["scope_id"]), entity_id, before)
            return written, job_id

    @staticmethod
    def _entity_snapshot(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, object]]:
        return [
            dict(row)
            for sql in (
                "SELECT * FROM entities WHERE id=?",
                "SELECT * FROM entity_properties WHERE entity_id=?",
            )
            for row in conn.execute(sql, (entity_id,))
        ]

    def _enqueue_name_sync(
        self, conn: sqlite3.Connection, scope_id: str, entity_id: str, before: list[dict[str, object]]
    ) -> str:
        job_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,before_json,after_json,"
            "created_at,updated_at,sync_mode) VALUES(?,?,'entity',?,?,?,?,?,'person_names')",
            (
                job_id,
                scope_id,
                entity_id,
                json.dumps(before),
                json.dumps(self._entity_snapshot(conn, entity_id)),
                now,
                now,
            ),
        )
        return job_id

    @contextmanager
    def graph_sync_lock(self) -> Iterator[None]:
        # One correction graph build per database across workers. The OS releases the
        # lock on exit, so an interrupted worker leaves its committed jobs runnable.
        with self._database.advisory_lock("corrections"):
            yield

    def next_name_sync_batch(self, now: str) -> tuple[str, int] | None:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT scope_id FROM memory_corrections WHERE sync_mode='person_names' "
                "AND status='pending' AND next_attempt_at<=? ORDER BY rowid LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            scope_id = str(row[0])
            through = conn.execute(
                "SELECT MAX(rowid) FROM memory_corrections WHERE sync_mode='person_names' AND scope_id=?",
                (scope_id,),
            ).fetchone()[0]
        return scope_id, int(through)

    def finish_name_sync_batch(self, scope_id: str, through: int, error: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as conn, conn:
            if error:
                conn.execute(
                    "UPDATE memory_corrections SET attempts=attempts+1, error=?, updated_at=?, "
                    "status=CASE WHEN attempts>=2 THEN 'failed' ELSE 'pending' END, "
                    "next_attempt_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now',"
                    " (5 * (1 << attempts)) || ' seconds') "
                    "WHERE sync_mode='person_names' AND scope_id=? AND rowid<=? AND status='pending'",
                    (error, now, scope_id, through),
                )
            else:
                conn.execute(
                    "UPDATE memory_corrections SET status='synced',error='',next_attempt_at='',updated_at=? "
                    "WHERE sync_mode='person_names' AND scope_id=? AND rowid<=? AND status!='synced'",
                    (now, scope_id, through),
                )

    def latest_name_sync(self, entity_id: str) -> dict[str, object] | None:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT id, status, error FROM memory_corrections "
                "WHERE sync_mode='person_names' AND target_id=? ORDER BY rowid DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def retry_name_sync(self, job_id: str) -> dict[str, object] | None:
        with self._database.connect() as conn, conn:
            # 任意类型的任务都能重试；写入还没提交的任务不动（它本来就会在写完后入队）。
            conn.execute(
                "UPDATE memory_corrections SET status='pending',error='',attempts=0,next_attempt_at='' "
                "WHERE id=? AND status!='synced' AND next_attempt_at!=?",
                (job_id, WRITING),
            )
            row = conn.execute(
                "SELECT id,status,error FROM memory_corrections WHERE id=?",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def read(self, kind: str, target_id: str) -> dict[str, object] | None:
        table = TABLES.get(kind)
        if table is None:
            return None
        with self._database.connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in JSON_FIELDS:
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
                column = key + "_json" if key in JSON_FIELDS else key
                columns.append(f"{column}=?")
                values.append(json.dumps(value, ensure_ascii=False) if key in JSON_FIELDS else value)
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
    def track(self, scope_id: str, kind: str, target_id: str) -> Iterator[str]:
        """登记一条持久同步任务，产出它的 id。

        任务在写入进行中**不可领取**（``next_attempt_at`` 是 ``WRITING``）：后台同步不再
        持有请求的锁，若在写入提交前就领走这条任务，派生数据会基于旧值重建、任务却被标成
        已同步。写完（含写入抛错，那时也要核验）才把它放进队列。
        """
        record_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        before = self._snapshot(scope_id, kind, target_id)
        with self._database.connect() as conn, conn:
            conn.execute(
                "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,"
                "before_json,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,?,?)",
                (record_id, scope_id, kind, target_id, json.dumps(before), now, now, WRITING),
            )
        try:
            yield record_id
        finally:
            after = self._snapshot(scope_id, kind, target_id)
            with self._database.connect() as conn, conn:
                conn.execute(
                    "UPDATE memory_corrections SET after_json=?,updated_at=?,next_attempt_at='' WHERE id=?",
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
                    "SELECT DISTINCT scope_id FROM memory_corrections "
                    "WHERE status IN ('pending','failed') AND sync_mode='legacy' "
                    "AND next_attempt_at!=? LIMIT 100",
                    (WRITING,),
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
                "SELECT target_kind,target_id FROM memory_corrections "
                "WHERE scope_id=? AND status='pending' AND sync_mode='legacy'",
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
                    "WHERE scope_id=? AND status='pending' AND sync_mode='legacy' AND target_kind='event'",
                    (scope_id,),
                )
            ]

    def next_legacy_sync_batch(self, scope_id: str | None, now: str) -> tuple[str, tuple[str, ...]] | None:
        """领取一个相册里到期的同步任务（不给 ``scope_id`` 时取最早到期的那个相册）。

        返回**具体的任务 id**，而不是「这个相册的全部」：重建期间新提交的写入不在这一批，
        它们留给下一轮，不会被这次基于旧快照的重建标成已同步。
        """
        with self._database.connect() as conn:
            if scope_id is None:
                row = conn.execute(
                    "SELECT scope_id FROM memory_corrections WHERE sync_mode='legacy' "
                    "AND status='pending' AND next_attempt_at<=? ORDER BY rowid LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    return None
                scope_id = str(row[0])
            ids = tuple(
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM memory_corrections WHERE sync_mode='legacy' AND scope_id=? "
                    "AND status='pending' AND next_attempt_at<=? ORDER BY rowid",
                    (scope_id, now),
                )
            )
        return scope_id, ids

    def finish_legacy_sync_batch(self, ids: Sequence[str], error: str) -> None:
        if not ids:
            return
        now = datetime.now(UTC).isoformat()
        marks = ",".join("?" for _ in ids)
        with self._database.connect() as conn, conn:
            if error:
                conn.execute(
                    "UPDATE memory_corrections SET attempts=attempts+1, error=?, updated_at=?, "
                    "status=CASE WHEN attempts>=2 THEN 'failed' ELSE 'pending' END, "
                    "next_attempt_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now',"
                    " (5 * (1 << attempts)) || ' seconds') "
                    f"WHERE id IN ({marks}) AND status='pending'",
                    (error, now, *ids),
                )
            else:
                conn.execute(
                    "UPDATE memory_corrections SET status='synced',error='',next_attempt_at='',updated_at=? "
                    f"WHERE id IN ({marks}) AND status!='synced'",
                    (now, *ids),
                )

    def requeue_legacy_sync(self, scope_id: str) -> None:
        """用户点「重试同步」：失败或在退避中的任务立即重新到期。写入中的任务不动。"""
        with self._database.connect() as conn, conn:
            conn.execute(
                "UPDATE memory_corrections SET status='pending',attempts=0,next_attempt_at='' "
                "WHERE sync_mode='legacy' AND scope_id=? AND status IN ('pending','failed') "
                "AND next_attempt_at!=?",
                (scope_id, WRITING),
            )

    def sync_job(self, job_id: str) -> dict[str, object] | None:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT id,status,error FROM memory_corrections WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def enqueue_graph_sync(self, scope_id: str, entity_id: str) -> str:
        """给一次已提交的人物确认排一条「只重建记忆图」的任务（与改名同一条队列）。"""
        with self._database.connect() as conn, conn:
            return self._enqueue_name_sync(conn, scope_id, entity_id, self._entity_snapshot(conn, entity_id))
