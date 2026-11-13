"""人物瞬间仓储。SQL 只出现在这一层。

逐行移植 1.0 ``backend/db.py:6079-6109`` / ``:6158`` / ``:6218``。

**跨空间守卫是这个仓储的核心**（``:6175-6177``）：一条瞬间引用的六个对象必须同属一个
记忆空间。这种错不会自己暴露——它只会让画像把两个空间的人混成一个。

**两处与 1.0 的差异都是 2.0 的既有决定：**

1. **没有 ``run_id``**：1.0 那列指向 ``person_insight_runs``，而那张表在 0006 里没建
   （``0006_identity.sql:234`` 写明这一列随之取消）。照抄 1.0 的列清单会直接报错。
2. **``selection`` 在 T86f 之后读写对称了**。此前是一处不对称：表里有
   ``selection_json``、写入照旧，但 ``PersonMomentRecord`` 没有这个字段，读路径不还原
   它。当初把这件事挂在消费方名下是对的——T86f（瞬间抽取）正是那个消费方，它落地时
   一并给记录加了 ``selection`` 字段、把 ``selection_json`` 补进 ``_COLUMNS``。
   留一个只写不读的列等于「接了一半」：数据进去了，谁都拿不出来。
3. **1.0 的兜底 ``scopes[0] if scopes else "home-default"`` 没搬**：六个引用列都有
   外键、六张表的 ``scope_id`` 都是 ``NOT NULL``，所以 ``scopes`` 不可能为空，
   那半句永远走不到。理由与 portraits 里去掉的那条完全相同。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime

from sentrix.domain.identity.models import PersonMomentRecord
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, person_id, cluster_id, event_id, observation_id, asset_id,"
    " face_instance_id, action_text, interaction_target_ids_json, interaction_text,"
    " participation_style, visible_affect, social_role_cues_json, narrative_note,"
    " selection_json, confidence, model_name, prompt_version, status, created_at, updated_at"
)

# 1.0 ``_scope_for``（``db.py:6082-6089``）的 kind → 表名映射。**只留 2.0 用得上的**：
# 1.0 那张表里 ``person`` 与 ``entity`` 指向同一张表，取其一即可。
_SCOPE_TABLES = {
    "entity": "entities",
    "cluster": "face_clusters",
    "event": "events",
    "observation": "observations",
    "asset": "assets",
}


def _new_id(prefix: str) -> str:
    """1.0 ``make_id``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_list(value: Sequence[str] | None) -> str:
    return json.dumps(list(value or []), ensure_ascii=False)


def _json_object(value: Mapping[str, object] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False)


def _load_list(raw: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


def _load_object(raw: object) -> Mapping[str, object]:
    """``selection_json`` 一格。解不出来就是空表——那一列 ``NOT NULL DEFAULT '{}'``，
    所以「空」与「没有」在库里本来就是同一个值。"""
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


class SqliteMomentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert(
        self,
        *,
        person_id: str,
        observation_id: str,
        prompt_version: str,
        cluster_id: str,
        event_id: str,
        asset_id: str,
        face_instance_id: str,
        action_text: str = "",
        interaction_target_ids: Sequence[str] = (),
        interaction_text: str = "",
        participation_style: str = "",
        visible_affect: str = "",
        social_role_cues: Sequence[str] = (),
        narrative_note: str = "",
        selection: Mapping[str, object] | None = None,
        confidence: float = 0.0,
        model_name: str = "unknown",
    ) -> PersonMomentRecord:
        scope_id = self._common_scope(
            person_id=person_id,
            cluster_id=cluster_id,
            event_id=event_id,
            observation_id=observation_id,
            asset_id=asset_id,
            face_instance_id=face_instance_id,
        )
        now = datetime.now(UTC).isoformat()
        moment_id = _new_id("moment")
        with self._database.write_transaction() as connection, ExitStack() as permissions:
            scoped = connection.execute(
                "SELECT 1 FROM media_scope_epochs WHERE scope_id=?", (scope_id,)
            ).fetchone()
            if scoped:
                from sentrix.domain.media.deletion import MediaIsolated

                if not connection.execute("SELECT media_generation_allowed(?)", (scope_id,)).fetchone()[0]:
                    raise MediaIsolated("moment inference generation changed")
                proof = connection.execute(
                    "SELECT 1 FROM readable_face_instances f JOIN readable_observatio"
                    "ns o ON o.id=f.observation_id "
                    "JOIN face_clusters fc ON fc.id=f.cluster_id WHERE f.id=? AND f.asset_id=? AND o.id=? "
                    "AND fc.id=? AND fc.entity_id=?",
                    (face_instance_id, asset_id, observation_id, cluster_id, person_id),
                ).fetchone()
                if not proof:
                    raise MediaIsolated("moment source identity mismatch")
                permissions.enter_context(self._database.reconciliation(connection))
            connection.execute(
                "INSERT INTO person_moments("
                " id, scope_id, person_id, cluster_id, event_id, observation_id, asset_id,"
                " face_instance_id, action_text, interaction_target_ids_json, interaction_text,"
                " participation_style, visible_affect, social_role_cues_json, narrative_note,"
                " selection_json, confidence, model_name, prompt_version, status,"
                " created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(person_id, observation_id, prompt_version) DO UPDATE SET"
                "  action_text = excluded.action_text,"
                "  interaction_target_ids_json = excluded.interaction_target_ids_json,"
                "  interaction_text = excluded.interaction_text,"
                "  participation_style = excluded.participation_style,"
                "  visible_affect = excluded.visible_affect,"
                "  social_role_cues_json = excluded.social_role_cues_json,"
                "  narrative_note = excluded.narrative_note,"
                "  selection_json = excluded.selection_json,"
                "  confidence = excluded.confidence,"
                "  model_name = excluded.model_name,"
                "  updated_at = excluded.updated_at",
                (
                    moment_id,
                    scope_id,
                    person_id,
                    cluster_id,
                    event_id,
                    observation_id,
                    asset_id,
                    face_instance_id,
                    action_text,
                    _json_list(interaction_target_ids),
                    interaction_text,
                    participation_style,
                    visible_affect,
                    _json_list(social_role_cues),
                    narrative_note,
                    _json_object(selection),
                    float(confidence),
                    model_name,
                    prompt_version,
                    "active",
                    now,
                    now,
                ),
            )
            if scoped:
                actual = connection.execute(
                    "SELECT id FROM person_moments WHERE person_id=? AND observation_"
                    "id=? AND prompt_version=?",
                    (person_id, observation_id, prompt_version),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE media_object_states SET validity='valid',summary_valid=1,"
                    "generation=generation+1,reason='current_generation_moment' WHERE"
                    " object_table='person_moments' AND object_key=json_object('id',?"
                    ")",
                    (actual,),
                )
                connection.execute(
                    "INSERT INTO media_rebuild_publications VALUES ('person_moments',"
                    "json_object('id',?),?,(SELECT generation FROM media_scope_epochs"
                    " WHERE scope_id=?)) ON CONFLICT(object_table,object_key) DO UPDA"
                    "TE SET generation=excluded.generation,scope_id=excluded.scope_id",
                    (actual, scope_id, scope_id),
                )
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_moments"
                " WHERE person_id = ? AND observation_id = ? AND prompt_version = ?",
                (person_id, observation_id, prompt_version),
            ).fetchone()
        record = _record(row)
        assert record is not None, "刚 upsert 进去的那条读不回来"
        return record

    def list_for(
        self,
        *,
        person_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = "active",
    ) -> Sequence[PersonMomentRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if person_id is not None:
            clauses.append("person_id = ?")
            params.append(person_id)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_moments {where} ORDER BY created_at DESC",
                tuple(params),
            ).fetchall()
        return [record for row in rows if (record := _record(row)) is not None]

    # ---- 跨空间守卫 ----------------------------------------------------------

    def _common_scope(
        self,
        *,
        person_id: str,
        cluster_id: str,
        event_id: str,
        observation_id: str,
        asset_id: str,
        face_instance_id: str,
    ) -> str:
        """六个引用对象必须同属一个空间，返回那个空间。

        **多余一个就抛**（1.0 ``db.py:6176``）。不是「取第一个」——那会把上游的归属
        错误吞掉，而它之后只会以「画像把两个人混成一个」的形式浮出来。

        ``face_instance_id`` 自己没有 ``scope_id``（``face_instances`` 表没有那一列），
        要经它的 ``asset_id`` 找（1.0 ``_face_instance_scope``，``db.py:6097``）。
        """
        scopes = {
            scope
            for scope in (
                self._scope_of("entity", person_id),
                self._scope_of("cluster", cluster_id),
                self._scope_of("event", event_id),
                self._scope_of("observation", observation_id),
                self._scope_of("asset", asset_id),
                self._face_instance_scope(face_instance_id),
            )
            if scope
        }
        if len(scopes) > 1:
            raise ValueError("person moment must reference entities in the same memory space")
        # 六个引用列都有外键、六张表的 scope_id 都是 NOT NULL，所以这里必然恰好一个。
        # 1.0 有半句 ``else "home-default"`` 的兜底，2.0 没搬——它走不到。
        assert scopes, "有外键约束时不可能一个 scope 都查不到"
        return scopes.pop()

    def _scope_of(self, kind: str, record_id: str) -> str | None:
        table = _SCOPE_TABLES.get(kind)
        if table is None:
            raise ValueError(f"unknown scope kind: {kind}")
        with self._database.connect() as connection:
            row = connection.execute(f"SELECT scope_id FROM {table} WHERE id = ?", (record_id,)).fetchone()
        return str(row["scope_id"]) if row is not None else None

    def _face_instance_scope(self, face_instance_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT asset_id FROM readable_face_instances WHERE id = ?", (face_instance_id,)
            ).fetchone()
        return self._scope_of("asset", str(row["asset_id"])) if row is not None else None


def _record(row: sqlite3.Row | None) -> PersonMomentRecord | None:
    if row is None:
        return None
    return PersonMomentRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        cluster_id=row["cluster_id"],
        event_id=row["event_id"],
        observation_id=row["observation_id"],
        asset_id=row["asset_id"],
        face_instance_id=row["face_instance_id"],
        action_text=row["action_text"],
        interaction_target_ids=_load_list(row["interaction_target_ids_json"]),
        interaction_text=row["interaction_text"],
        participation_style=row["participation_style"],
        visible_affect=row["visible_affect"],
        social_role_cues=_load_list(row["social_role_cues_json"]),
        narrative_note=row["narrative_note"],
        selection=_load_object(row["selection_json"]),
        confidence=float(row["confidence"]),
        model_name=row["model_name"],
        prompt_version=row["prompt_version"],
        status=row["status"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )
