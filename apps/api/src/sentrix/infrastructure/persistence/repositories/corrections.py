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
from sentrix.domain.memories.ports import DependentTextMemory
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
    "observation": {
        "caption",
        "place",
        "activity",
        "event_type",
        "ocr_text",
        "objects",
        "clothing",
        "text_content",
    },
    "event": {"title", "summary", "place", "activity", "event_type", "time_start", "time_end"},
    "entity": {"canonical_name", "summary"},
}

DEPENDENT_FIELDS = {
    "moment": (
        "person_moments",
        ("action_text", "interaction_text", "participation_style", "visible_affect", "narrative_note"),
    ),
    "semantic_profile": (
        "semantic_profiles",
        (
            "summary_zh",
            "activity_summary_zh",
            "place_summary_zh",
            "appearance_summary_zh",
            "preference_summary_zh",
        ),
    ),
    "family_portrait": ("family_person_portraits", ("portrait_text",)),
    "person_portrait": ("person_portrait_revisions", ("portrait_text",)),
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
            changes = dict(changes)
            text_state = (
                conn.execute(
                    "SELECT * FROM text_import_state WHERE "
                    + ("observation_id=?" if kind == "observation" else "event_id=?"),
                    (target_id,),
                ).fetchone()
                if kind in {"event", "observation"}
                else None
            )
            if "text_content" in changes and (
                kind != "observation" or before["source_type"] not in {"text", "email"}
            ):
                raise ValueError("只有文本记忆支持正文修正")
            if text_state and kind == "event" and "time_start" in changes and "time_end" not in changes:
                # Text record time is a point, rather than the duration of a scene.
                changes["time_end"] = changes["time_start"]
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
            if kind == "observation" and "text_content" in changes:
                columns.append("source_text=?")
                values.append(changes["text_content"])
            conn.execute(
                f"UPDATE {table} SET {','.join(columns)},revision=revision+1,updated_at=? WHERE id=?",
                (*values, now, target_id),
            )
            after = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone())
            if text_state:
                self._sync_text(conn, kind, text_state, after, changes, now)
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

    @staticmethod
    def _sync_text(
        conn: sqlite3.Connection,
        kind: str,
        state: sqlite3.Row,
        after: dict[str, object],
        changes: Mapping[str, object],
        now: str,
    ) -> None:
        if kind == "observation" and "caption" in changes:
            conn.execute(
                "UPDATE events SET summary=?,title=?,revision=revision+1,updated_at=? WHERE id=?",
                (after["caption"], str(after["caption"])[:120], now, state["event_id"]),
            )
        if kind == "event":
            if "summary" in changes:
                conn.execute(
                    "UPDATE observations SET caption=?,revision=revision+1,updated_at=? WHERE id=?",
                    (after["summary"], now, state["observation_id"]),
                )
            if "time_start" in changes:
                conn.execute(
                    "UPDATE observations SET captured_at=?,revision=revision+1,updated_at=? WHERE id=?",
                    (after["time_start"], now, state["observation_id"]),
                )
                conn.execute(
                    "UPDATE assets SET captured_at=?,capture_time_json=?,updated_at=? WHERE id=?",
                    (
                        after["time_start"],
                        json.dumps(
                            {
                                "raw": after["time_start"],
                                "source": "user_correction",
                                "timezone": "",
                                "status": "known" if after["time_start"] else "unknown",
                            }
                        ),
                        now,
                        state["asset_id"],
                    ),
                )
        conn.execute("UPDATE text_import_state SET indexed_revision=0 WHERE asset_id=?", (state["asset_id"],))
        conn.execute(
            "DELETE FROM text_vectors WHERE (source_type='event' AND source_id=?) OR "
            "(source_type='observation' AND source_id=?)",
            (state["event_id"], state["observation_id"]),
        )

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
                result.append(self._dependencies(conn, scope_id, kind, target_id))
                return result
            # 家庭关系与实体属性有自己的版本表，保留完整范围快照供回溯。
            if kind == "family":
                result = [
                    dict(r)
                    for table in (
                        "family_relationships",
                        "family_person_memberships",
                        "family_person_portraits",
                    )
                    for r in conn.execute(f"SELECT * FROM {table} WHERE scope_id=?", (scope_id,))
                ]
                result.append(self._dependencies(conn, scope_id, kind, target_id))
                return result
            if kind == "video":
                video_snapshot: dict[str, object] = dict(self._dependencies(conn, scope_id, kind, target_id))
                return [video_snapshot]
            if kind == "face":
                return [
                    *[dict(r) for r in conn.execute("SELECT * FROM face_clusters WHERE id=?", (target_id,))],
                    *[
                        dict(r)
                        for r in conn.execute(
                            "SELECT e.* FROM entities e JOIN face_clusters c ON c.entity_id=e.id "
                            "WHERE c.id=?",
                            (target_id,),
                        )
                    ],
                    self._dependencies(conn, scope_id, kind, target_id),
                ]
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM entity_properties WHERE entity_id=?", (target_id,))
            ]

    @staticmethod
    def _dependencies(conn: sqlite3.Connection, scope: str, kind: str, target: str) -> dict[str, list[str]]:
        if kind in {"fact", "relationship", "family"}:
            dependent_observations: set[str] = set()
            entities: set[str] = set()
            if kind == "family":
                entities.add(target)
                for relation in conn.execute(
                    "SELECT subject_entity_id,object_entity_id FROM family_relationships WHERE scope_id=? "
                    "AND (subject_entity_id=? OR object_entity_id=?)",
                    (scope, target, target),
                ):
                    entities.update(str(value) for value in relation)
            else:
                record = conn.execute(
                    f"SELECT * FROM {TABLES[kind]} WHERE id=? AND scope_id=?", (target, scope)
                ).fetchone()
                if record:
                    refs = json.loads(record["evidence_ids_json"] or "[]")
                    dependent_observations.update(
                        str(r[0])
                        for r in conn.execute(
                            "SELECT id FROM observations WHERE scope_id=? "
                            "AND (id IN (SELECT value FROM json_each(?)) "
                            "OR asset_id IN (SELECT value FROM json_each(?)))",
                            (scope, json.dumps(refs), json.dumps(refs)),
                        )
                    )
                    if kind == "relationship":
                        entities.update((str(record["subject_entity_id"]), str(record["object_entity_id"])))
            for entity in entities:
                dependent_observations.update(
                    SqliteMemoryCorrections._dependencies(conn, scope, "entity", entity)[
                        "_affected_observations"
                    ]
                )
            events = [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT event_id FROM event_observations "
                    "WHERE observation_id IN (SELECT value FROM json_each(?))",
                    (json.dumps(sorted(dependent_observations)),),
                )
            ]
            return {
                "_affected_observations": sorted(dependent_observations),
                "_affected_events": events,
                "_affected_entities": sorted(entities),
            }
        if kind == "face":
            query = "SELECT observation_id FROM face_instances WHERE cluster_id=?"
        elif kind == "video":
            query = "SELECT o.id FROM observations o JOIN assets a ON a.id=o.asset_id "
            query += "WHERE a.id=? OR a.parent_asset_id=?"
        elif kind == "event":
            query = "SELECT observation_id FROM event_observations WHERE event_id=?"
        elif kind == "observation":
            query = "SELECT id FROM observations WHERE id=?"
        else:
            query = (
                "SELECT observation_id FROM entity_mentions WHERE entity_id=? "
                "UNION SELECT observation_id FROM entity_observations WHERE entity_id=? "
                "UNION SELECT o.id FROM event_participants ep JOIN json_each(ep.evidence_ids_json) ref "
                "JOIN observations o ON o.id=ref.value WHERE ep.person_id=? "
                "UNION SELECT f.observation_id FROM face_instances f JOIN face_clusters c "
                "ON c.id=f.cluster_id WHERE c.entity_id=? AND c.status<>'rejected'"
            )
        parameters = (
            (target, target)
            if kind == "video"
            else ((target,) if kind in {"face", "event", "observation"} else (target, target, target, target))
        )
        observations = sorted({str(r[0]) for r in conn.execute(query, parameters) if r[0]})
        events = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT eo.event_id FROM event_observations eo "
                "JOIN observations o ON o.id=eo.observation_id "
                "WHERE o.scope_id=? AND o.id IN (SELECT value FROM json_each(?))",
                (scope, json.dumps(observations)),
            )
        ]
        return {"_affected_observations": observations, "_affected_events": events}

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
        affected = self.affected_observations(scope_id)
        with self._database.connect() as conn, conn:
            for row in conn.execute(
                "SELECT id,people_json FROM observations WHERE scope_id=? "
                "AND id IN (SELECT value FROM json_each(?))",
                (scope_id, json.dumps(affected)),
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
                named = conn.execute(
                    "SELECT DISTINCT e.id,e.canonical_name FROM entities e "
                    "JOIN face_clusters c ON c.entity_id=e.id JOIN face_instances f ON f.cluster_id=c.id "
                    "WHERE f.observation_id=? AND e.scope_id=? AND e.status='pending' "
                    "AND e.name_state='confirmed' AND c.status<>'rejected' ORDER BY e.id",
                    (row["id"], scope_id),
                ).fetchall()
                kept.extend(
                    {"entity_id": r["id"], "name": r["canonical_name"], "status": "named_pending"}
                    for r in named
                )
                if kept != people:
                    conn.execute(
                        "UPDATE observations SET people_json=?,revision=revision+1,updated_at=? WHERE id=?",
                        (json.dumps(kept, ensure_ascii=False), datetime.now(UTC).isoformat(), row["id"]),
                    )
            self._refresh_relationship_claims(conn, scope_id)

    def refresh_dependencies(self, scope_id: str) -> None:
        events = self.affected_events(scope_id)
        automatic = self.summary_events(scope_id)
        identities = self.identity_updates(scope_id)
        with self._database.connect() as conn, conn:
            for event_id in events:
                # Rebuild the identity projection, including removals after reject/split.
                # Explicitly entered participants with other roles are retained.
                conn.execute(
                    "DELETE FROM event_participants WHERE event_id=? AND role='visible_subject'", (event_id,)
                )
                for person in conn.execute(
                    "SELECT m.entity_id,json_group_array(DISTINCT m.observation_id) AS evidence,"
                    "max(m.confidence) AS confidence "
                    "FROM entity_mentions m JOIN entities e ON e.id=m.entity_id "
                    "JOIN event_observations eo ON eo.observation_id=m.observation_id "
                    "WHERE eo.event_id=? AND e.scope_id=? AND e.status='confirmed' GROUP BY m.entity_id",
                    (event_id, scope_id),
                ).fetchall():
                    conn.execute(
                        "INSERT INTO event_participants(id,event_id,person_id,role,evidence_ids_json,"
                        "confidence,created_at,updated_at) "
                        "VALUES(?,?,?,'visible_subject',?,?,?,?)",
                        (
                            uuid.uuid4().hex,
                            event_id,
                            person[0],
                            person[1],
                            float(person[2]),
                            datetime.now(UTC).isoformat(),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
            for event_id in automatic:
                conn.execute(
                    "UPDATE events SET title='',summary='',revision=revision+1,updated_at=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), event_id),
                )
                conn.execute(
                    "DELETE FROM text_vectors WHERE source_type='event' AND source_id=?", (event_id,)
                )
            # These claims carry a stable entity reference; free prose and aliases do not.
            conn.execute(
                "UPDATE semantic_claims SET "
                "value_text=(SELECT canonical_name FROM entities WHERE id=value_entity_id), "
                "normalized_value_text=(SELECT canonical_name FROM entities WHERE id=value_entity_id), "
                "revision=revision+1,updated_at=? WHERE scope_id=? AND value_entity_id IN "
                "(SELECT id FROM entities WHERE scope_id=? AND status='confirmed') "
                "AND value_text<>(SELECT canonical_name FROM entities WHERE id=value_entity_id)",
                (datetime.now(UTC).isoformat(), scope_id, scope_id),
            )
            for identity in identities:
                old, name = str(identity["previous_name"]), str(identity["name"])
                if not old or old == name:
                    continue
                ambiguous = conn.execute(
                    "SELECT 1 FROM entities WHERE scope_id=? AND canonical_name=? AND id<>? LIMIT 1",
                    (scope_id, old, identity["entity_id"]),
                ).fetchone()
                if ambiguous:
                    continue
                observation_ids = identity["observation_ids"]
                refs = set(str(i) for i in observation_ids) if isinstance(observation_ids, list) else set()
                refs.update(
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT asset_id FROM observations WHERE scope_id=? "
                        "AND id IN (SELECT value FROM json_each(?))",
                        (scope_id, json.dumps(sorted(refs))),
                    )
                )
                for fact in conn.execute(
                    "SELECT id,subject,object,evidence_ids_json FROM facts WHERE scope_id=? "
                    "AND (subject=? OR object=?)",
                    (scope_id, old, old),
                ).fetchall():
                    evidence = set(json.loads(fact["evidence_ids_json"] or "[]"))
                    if evidence and evidence.issubset(refs):
                        conn.execute(
                            "UPDATE facts SET subject=?,object=?,revision=revision+1,updated_at=? WHERE id=?",
                            (
                                name if fact["subject"] == old else fact["subject"],
                                name if fact["object"] == old else fact["object"],
                                datetime.now(UTC).isoformat(),
                                fact["id"],
                            ),
                        )
                        after = dict(conn.execute("SELECT * FROM facts WHERE id=?", (fact["id"],)).fetchone())
                        checkpoint = hashlib.sha256(
                            f"{self._generation(conn, scope_id)}|fact|{fact['id']}".encode()
                        ).hexdigest()
                        now = datetime.now(UTC).isoformat()
                        conn.execute(
                            "INSERT OR IGNORE INTO memory_corrections(id,scope_id,target_kind,target_id,"
                            "before_json,after_json,reason,status,created_at,updated_at) "
                            "VALUES(?,?,'derived_rebuild',?,?,?,'身份绑定事实名称同步','synced',?,?)",
                            (
                                checkpoint,
                                scope_id,
                                fact["id"],
                                json.dumps(dict(fact)),
                                json.dumps(after),
                                now,
                                now,
                            ),
                        )

    def summary_events(self, scope_id: str) -> Sequence[str]:
        affected = self.affected_events(scope_id)
        with self._database.connect() as conn:
            return [
                str(row[0])
                for row in conn.execute(
                    "SELECT id FROM events WHERE scope_id=? AND status='active' "
                    "AND (coalesce(source_type,'') NOT IN ('text','email') OR EXISTS ("
                    "SELECT 1 FROM text_import_state s JOIN memory_corrections c "
                    "ON c.target_id=s.observation_id WHERE s.event_id=events.id "
                    "AND c.scope_id=events.scope_id AND c.status='pending' AND c.target_kind='observation' "
                    "AND json_extract(c.before_json,'$.text_content')<>"
                    "json_extract(c.after_json,'$.text_content') "
                    "AND json_extract(c.before_json,'$.caption')=json_extract(c.after_json,'$.caption'))) "
                    "AND id IN (SELECT value FROM json_each(?)) "
                    "AND id NOT IN (SELECT target_id FROM memory_corrections "
                    "WHERE scope_id=? AND status='pending' AND target_kind='event') ORDER BY id",
                    (scope_id, json.dumps(affected), scope_id),
                )
            ]

    def identity_updates(self, scope_id: str) -> Sequence[dict[str, object]]:
        updates: list[dict[str, object]] = []
        affected = set(self.affected_observations(scope_id))
        with self._database.connect() as conn:
            previous: dict[str, str] = {}
            for record in conn.execute(
                "SELECT before_json FROM memory_corrections WHERE scope_id=? AND status='pending' "
                "AND target_kind IN ('entity','face','family') ORDER BY created_at",
                (scope_id,),
            ):
                before = json.loads(record[0])
                rows = before if isinstance(before, list) else [before]
                for row in rows:
                    if not isinstance(row, dict) or row.get("entity_type") != "person":
                        continue
                    previous.setdefault(str(row["id"]), str(row.get("canonical_name") or ""))
            for entity in conn.execute(
                "SELECT id,canonical_name,status,name_state FROM entities WHERE scope_id=? "
                "AND entity_type='person' AND (status='confirmed' OR name_state='confirmed') ORDER BY id",
                (scope_id,),
            ).fetchall():
                dependencies = self._dependencies(conn, scope_id, "entity", str(entity["id"]))
                observations = dependencies["_affected_observations"]
                if not isinstance(observations, list) or not affected.intersection(observations):
                    continue
                updates.append(
                    {
                        "entity_id": str(entity["id"]),
                        "name": str(entity["canonical_name"]),
                        "previous_name": previous.get(str(entity["id"]), ""),
                        "status": str(entity["status"]),
                        "name_state": str(entity["name_state"]),
                        "observation_ids": observations,
                    }
                )
        return updates

    @staticmethod
    def _generation(conn: sqlite3.Connection, scope: str) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM memory_corrections WHERE scope_id=? AND status='pending' ORDER BY id",
                        (scope,),
                    )
                ]
            ).encode()
        ).hexdigest()

    def dependent_text(self, scope_id: str) -> Sequence[DependentTextMemory]:
        affected = set(self.affected_observations(scope_id))
        targets: list[DependentTextMemory] = []
        with self._database.connect() as conn:
            generation = self._generation(conn, scope_id)
            entities = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT entity_id FROM entity_mentions "
                    "WHERE observation_id IN (SELECT value FROM json_each(?)) "
                    "UNION SELECT entity_id FROM entity_observations "
                    "WHERE observation_id IN (SELECT value FROM json_each(?)) "
                    "UNION SELECT c.entity_id FROM face_clusters c "
                    "JOIN face_instances f ON f.cluster_id=c.id "
                    "WHERE f.observation_id IN (SELECT value FROM json_each(?)) AND c.entity_id IS NOT NULL",
                    (json.dumps(sorted(affected)),) * 3,
                )
            }
            for record in conn.execute(
                "SELECT target_kind,target_id,before_json,after_json FROM memory_corrections "
                "WHERE scope_id=? AND status='pending'",
                (scope_id,),
            ):
                if record[0] == "entity":
                    entities.add(str(record[1]))
                for raw in record[2:]:
                    data = json.loads(raw)
                    for item in data if isinstance(data, list) else [data]:
                        if isinstance(item, dict):
                            entities.update(str(e) for e in item.get("_affected_entities", []) if e)
                            if item.get("entity_type") == "person":
                                entities.add(str(item["id"]))
            for kind, (table, columns) in DEPENDENT_FIELDS.items():
                for row in conn.execute(f"SELECT * FROM {table} WHERE scope_id=?", (scope_id,)).fetchall():
                    entity_id = str(row["entity_id" if kind == "family_portrait" else "person_id"])
                    if entity_id not in entities:
                        continue
                    if kind == "moment" and row["status"] != "active":
                        continue
                    if kind == "family_portrait" and (
                        row["state"] != "effective" or row["locked"] or row["source"] == "user_override"
                    ):
                        continue
                    if kind == "person_portrait" and row["status"] != "active":
                        continue
                    checkpoint = hashlib.sha256(
                        f"{generation}|{kind}|{entity_id if 'portrait' in kind else row['id']}".encode()
                    ).hexdigest()
                    if conn.execute("SELECT 1 FROM memory_corrections WHERE id=?", (checkpoint,)).fetchone():
                        continue
                    deps = self._dependencies(conn, scope_id, "entity", entity_id)
                    observation_ids = tuple(deps["_affected_observations"])
                    if kind == "moment":
                        observation_ids = (str(row["observation_id"]),)
                    elif kind == "person_portrait":
                        # 画像重建只需要人物瞬间对应的直接证据。把人物在整个相册
                        # 的所有提及都塞进一次模型请求会超过上下文窗口，也会把同场
                        # 其他人的泛称错误归给此人；瞬间表是已经完成筛选的稳定依赖。
                        moment_observations = tuple(
                            str(item[0])
                            for item in conn.execute(
                                "SELECT observation_id FROM person_moments "
                                "WHERE person_id=? AND scope_id=? AND status='active' "
                                "ORDER BY id",
                                (entity_id, scope_id),
                            ).fetchall()
                        )
                        if moment_observations:
                            observation_ids = moment_observations
                    refs: tuple[dict[str, object], ...] = ()
                    if kind == "person_portrait":
                        refs = tuple(
                            {"kind": "person_moment", "id": str(r[0])}
                            for r in conn.execute(
                                "SELECT id FROM person_moments WHERE person_id=? AND scope_id=? "
                                "AND status='active' ORDER BY id",
                                (entity_id, scope_id),
                            )
                        )
                    targets.append(
                        DependentTextMemory(
                            kind,
                            str(row["id"]),
                            entity_id,
                            str(row["updated_at"]),
                            {c: str(row[c] or "") for c in columns},
                            observation_ids,
                            refs,
                            generation,
                        )
                    )
        return targets

    def write_dependent_text(self, target: DependentTextMemory, fields: Mapping[str, str]) -> None:
        table, columns = DEPENDENT_FIELDS[target.kind]
        if set(fields) != set(columns) or any(not isinstance(value, str) for value in fields.values()):
            raise ValueError("invalid dependent memory fields")
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (target.id,)).fetchone()
            if row is None:
                raise CorrectionMissing("dependent memory no longer exists")
            scope = str(row["scope_id"])
            checkpoint_key = target.entity_id if "portrait" in target.kind else target.id
            checkpoint = hashlib.sha256(
                f"{target.generation}|{target.kind}|{checkpoint_key}".encode()
            ).hexdigest()
            if conn.execute("SELECT 1 FROM memory_corrections WHERE id=?", (checkpoint,)).fetchone():
                return
            if str(row["updated_at"]) != target.version or self._generation(conn, scope) != target.generation:
                raise CorrectionConflict("关联记忆已更新，请重试同步")
            now = datetime.now(UTC).isoformat()
            before = dict(row)
            if target.kind == "person_portrait":
                if row["status"] != "active":
                    raise CorrectionConflict("人物画像已锁定或更新，请重试同步")
                conn.execute(
                    "UPDATE person_portrait_revisions SET status='superseded',updated_at=? WHERE id=?",
                    (now, target.id),
                )
                conn.execute(
                    "INSERT INTO person_portrait_revisions(id,scope_id,person_id,revision,portrait_text,"
                    "themes_json,evidence_refs_json,trigger_type,model_name,prompt_version,status,"
                    "created_at,updated_at) "
                    "VALUES(?,?,?,?,?,'[]',?,'pipeline','memory_correction','correction_v1','active',?,?)",
                    (
                        uuid.uuid4().hex,
                        scope,
                        target.entity_id,
                        int(row["revision"]) + 1,
                        fields["portrait_text"],
                        json.dumps(target.evidence_refs),
                        now,
                        now,
                    ),
                )
            elif target.kind == "family_portrait":
                if row["state"] != "effective" or row["locked"]:
                    raise CorrectionConflict("家庭画像已锁定或更新，请重试同步")
                conn.execute(
                    "UPDATE family_person_portraits SET state='superseded',updated_at=? WHERE id=?",
                    (now, target.id),
                )
                conn.execute(
                    "INSERT INTO family_person_portraits(id,scope_id,entity_id,portrait_text,"
                    "evidence_refs_json,source,state,revision,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,'model','effective',?,?,?)",
                    (
                        uuid.uuid4().hex,
                        scope,
                        target.entity_id,
                        fields["portrait_text"],
                        json.dumps(target.observation_ids),
                        int(row["revision"]) + 1,
                        now,
                        now,
                    ),
                )
            else:
                columns_sql = ",".join(f"{column}=?" for column in columns)
                extra = (
                    ",revision=revision+1,patterns_json='[]',event_memory_json='[]'"
                    if target.kind == "semantic_profile"
                    else ",social_role_cues_json='[]',interaction_target_ids_json='[]'"
                )
                conn.execute(
                    f"UPDATE {table} SET {columns_sql}{extra},updated_at=? WHERE id=?",
                    (*[fields[column] for column in columns], now, target.id),
                )
            conn.execute(
                "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,before_json,"
                "after_json,reason,status,created_at,updated_at) "
                "VALUES(?,?,'derived_rebuild',?,?,?,'关联记忆重建','synced',?,?)",
                (
                    checkpoint,
                    scope,
                    target.id,
                    json.dumps(before),
                    json.dumps(dict(fields), ensure_ascii=False),
                    now,
                    now,
                ),
            )

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
                "SELECT target_kind,target_id,before_json,after_json FROM memory_corrections "
                "WHERE scope_id=? AND status='pending'",
                (scope_id,),
            ):
                kind, target = row[0], row[1]
                for raw in (row[2], row[3]):
                    snapshot = json.loads(raw)
                    for item in snapshot if isinstance(snapshot, list) else [snapshot]:
                        if isinstance(item, dict):
                            ids.update(str(i) for i in item.get("_affected_observations", []) if i)
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
        observations = self.affected_observations(scope_id)
        with self._database.connect() as conn:
            ids = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT target_id FROM memory_corrections "
                    "WHERE scope_id=? AND status='pending' AND target_kind='event'",
                    (scope_id,),
                )
            }
            ids.update(
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT event_id FROM event_observations "
                    "WHERE observation_id IN (SELECT value FROM json_each(?))",
                    (json.dumps(observations),),
                )
            )
            for row in conn.execute(
                "SELECT before_json,after_json FROM memory_corrections WHERE scope_id=? AND status='pending'",
                (scope_id,),
            ):
                for raw in row:
                    snapshot = json.loads(raw)
                    for item in snapshot if isinstance(snapshot, list) else [snapshot]:
                        if isinstance(item, dict):
                            ids.update(str(i) for i in item.get("_affected_events", []) if i)
            return sorted(ids)

    def finish(self, scope_id: str, error: str) -> None:
        observations = self.affected_observations(scope_id)
        with self._database.connect() as conn, conn:
            conn.execute(
                "UPDATE text_import_state SET indexed_revision=CASE WHEN ?='' THEN "
                "(SELECT revision FROM observations WHERE id=observation_id) ELSE 0 END,last_error=? "
                "WHERE observation_id IN (SELECT value FROM json_each(?))",
                (error, error, json.dumps(observations)),
            )
            conn.execute(
                "UPDATE memory_corrections SET status=?,error=?,updated_at=? "
                "WHERE scope_id=? AND status='pending'",
                ("pending" if error else "synced", error, datetime.now(UTC).isoformat(), scope_id),
            )
