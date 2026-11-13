"""家庭图谱仓储。SQL 只出现在这一层。

逐行移植 1.0 ``backend/db.py:5631-5949`` 那一段（``get_effective_family_membership``
到 ``family_graph_scope_ids``）。

**这一层的中心是 ``locked``，而它带来三条「静默跳过」。** 用户写下的行 ``locked = 1``，
模型的推断遇到它就**什么都不做、直接返回已有行**；撤回过的边另外在
``family_relationship_blocks`` 里留一行挡着。三条跳过都**不抛异常、不返回错误**——
「保留用户的选择」就是它们要的效果，所以调用方分不出「刚写成功」与「被跳过」。
这是 1.0 原样，见端口各方法的 docstring。

**三处 1.0 的不一致照搬不统一**（复核时逐条记下的）：

1. ``get_active_portrait`` 按 ``created_at DESC`` 取当前行，而
   ``get_effective_membership`` 按 ``locked DESC, revision DESC, updated_at DESC``。
   同一批「取当前有效行」的语义，两个排序键。
2. ``set_membership`` 的新行**回填** ``supersedes_id``；``set_relationship`` 的新行
   **恒 NULL**（``returning`` 里也不给）——关系侧的历史链是断的。
3. ``write_portrait`` **不校验 ``source``**，另外两个写方法都校验 ``FAMILY_SOURCES``。

**``_person_exists`` 那条先例在这里的形态是 ``_family_entity``**：它不走
``EntityRepository``，而是在同一条连接里直接 SELECT——与 ``participants.py`` 的
``_person_exists`` 同一条理由：这是个**校验**不是一次实体读取，套一层仓储只会多一次连接。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.family import (
    FAMILY_MEMBERSHIPS,
    FAMILY_PENDING_DISPLAY_NAME,
    FAMILY_SOURCES,
    SOURCE_MODEL,
    SOURCE_USER_OVERRIDE,
    STATE_EFFECTIVE,
    STATE_RETRACTED,
    STATE_SUPERSEDED,
    FamilyAnalysisRunRecord,
    FamilyEvidenceRow,
    FamilyMembershipRecord,
    FamilyPersonProjection,
    FamilyPortraitRecord,
    FamilyRelationshipRecord,
)
from sentrix.domain.identity.family_graph import validate_relationship_pair
from sentrix.domain.identity.models import ENTITY_STATUS_PENDING
from sentrix.domain.identity.ports import EntityRepository
from sentrix.infrastructure.persistence.database import Database

# ``r.*`` 之外的别名只在读侧用；单条读取用这张清单就够。
_RUN_COLUMNS = (
    "id, scope_id, status, current_stage, config_json, stats_json, error,"
    " created_at, updated_at, completed_at"
)
_MEMBERSHIP_COLUMNS = (
    "id, scope_id, entity_id, membership, source, confidence, evidence_refs_json,"
    " inference_run_id, state, locked, revision, supersedes_id, created_at, updated_at"
)
_RELATIONSHIP_COLUMNS = (
    "id, scope_id, family_graph_id, subject_entity_id, predicate, object_entity_id,"
    " inverse_predicate, source, confidence, evidence_refs_json, inference_run_id,"
    " state, locked, revision, supersedes_id, created_at, updated_at"
)
_PORTRAIT_COLUMNS = (
    "id, scope_id, entity_id, portrait_text, evidence_refs_json, source,"
    " inference_run_id, state, locked, revision, created_at, updated_at"
)
# 1.0 ``db.py:5691`` 等的 ``scope_id or "home-default"``。三处写方法都做这个归一，
# 读方法**不做**（读一个空 scope 就是读一个不存在的 scope，返回空是对的）。
DEFAULT_SCOPE_ID = "home-default"


def _new_id(prefix: str) -> str:
    """1.0 ``make_id(prefix)``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _refs(raw: object) -> tuple[str, ...]:
    """解 ``evidence_refs_json``。1.0 的 ``_decode`` 解不开时回落空表，这里一样。"""
    try:
        parsed = json.loads(str(raw) if raw is not None else "[]")
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


def _dump(values: Sequence[str]) -> str:
    """1.0 的 ``json_value(list(dict.fromkeys(...)), [])``：**保序去重**后序列化。"""
    return json.dumps(list(dict.fromkeys(values)), ensure_ascii=False)


def _row_to_membership(row: sqlite3.Row) -> FamilyMembershipRecord:
    return FamilyMembershipRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        entity_id=row["entity_id"],
        membership=row["membership"],
        source=row["source"],
        confidence=row["confidence"],
        evidence_refs=_refs(row["evidence_refs_json"]),
        inference_run_id=row["inference_run_id"],
        state=row["state"],
        locked=bool(row["locked"]),
        revision=row["revision"],
        supersedes_id=row["supersedes_id"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


def _row_to_relationship(row: sqlite3.Row) -> FamilyRelationshipRecord:
    return FamilyRelationshipRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        family_graph_id=row["family_graph_id"],
        subject_entity_id=row["subject_entity_id"],
        predicate=row["predicate"],
        object_entity_id=row["object_entity_id"],
        inverse_predicate=row["inverse_predicate"],
        source=row["source"],
        confidence=row["confidence"],
        evidence_refs=_refs(row["evidence_refs_json"]),
        inference_run_id=row["inference_run_id"],
        state=row["state"],
        locked=bool(row["locked"]),
        revision=row["revision"],
        supersedes_id=row["supersedes_id"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


def _object(raw: object) -> Mapping[str, object]:
    """解一个 ``*_json`` 列成对象。1.0 的 ``json.loads`` 解不开时回落 ``{}``。"""
    try:
        parsed = json.loads(str(raw) if raw is not None else "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_run(row: sqlite3.Row) -> FamilyAnalysisRunRecord:
    return FamilyAnalysisRunRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        status=row["status"],
        current_stage=row["current_stage"],
        config=_object(row["config_json"]),
        stats=_object(row["stats_json"]),
        error=row["error"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
        completed_at=_timestamp(row["completed_at"]),
    )


def _row_to_portrait(row: sqlite3.Row) -> FamilyPortraitRecord:
    return FamilyPortraitRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        entity_id=row["entity_id"],
        portrait_text=row["portrait_text"],
        evidence_refs=_refs(row["evidence_refs_json"]),
        source=row["source"],
        inference_run_id=row["inference_run_id"],
        state=row["state"],
        locked=bool(row["locked"]),
        revision=row["revision"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


class SqliteFamilyGraphRepository:
    def user_relationship_pairs(self, scope_id: str) -> Sequence[tuple[str, str]]:
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT subject_entity_id,object_entity_id FROM family_relationships "
                "WHERE scope_id=? AND source='user_override' UNION "
                "SELECT left_entity_id,right_entity_id FROM family_relationship_blocks WHERE scope_id=?",
                (scope_id, scope_id),
            ).fetchall()
        return sorted({(min(str(row[0]), str(row[1])), max(str(row[0]), str(row[1]))) for row in rows})

    def __init__(self, database: Database, entities: EntityRepository) -> None:
        self._database = database
        # ``family_graph_people`` 要按 ``list_entities`` 的口径列人物——那份口径
        # （排除 ``include_in_people = 0`` 的空间、排除 rejected/superseded）
        # 抄进这里就会漂。所以注入实体仓储复用它的规则，而不是复刻那条 WHERE。
        self._entities = entities

    # ---- 校验 ----------------------------------------------------------------

    @staticmethod
    def _family_entity(connection: sqlite3.Connection, scope_id: str, entity_id: str) -> None:
        """两端必须是**同一个记忆空间里的 person 实体**，否则抛 ``ValueError``。

        两句话术逐字照 1.0（``db.py:5620`` 附近）。**两条判据分先后**：先判存在与类型，
        再判空间——因为「不是 person」与「不在同一个空间」是两种不同的用户错误。
        """
        row = connection.execute(
            "SELECT entity_type, scope_id FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None or row["entity_type"] != "person":
            raise ValueError("family graph requires a person entity")
        if (row["scope_id"] or DEFAULT_SCOPE_ID) != (scope_id or DEFAULT_SCOPE_ID):
            raise ValueError("family graph entities must belong to the same memory space")

    @staticmethod
    def _effective_pair(
        connection: sqlite3.Connection, scope_id: str, subject_entity_id: str, object_entity_id: str
    ) -> list[sqlite3.Row]:
        """这一对**两个方向**的全部有效边，按 ``locked DESC, revision DESC, updated_at DESC``。

        排序里 ``locked`` 在第一位：用户写下的边压过模型写的（同一个无序对正反两行都命中，
        所以最多两行）。
        """
        return connection.execute(
            f"SELECT {_RELATIONSHIP_COLUMNS} FROM family_relationships"
            " WHERE scope_id = ? AND state = ? AND ("
            " (subject_entity_id = ? AND object_entity_id = ?)"
            " OR (subject_entity_id = ? AND object_entity_id = ?))"
            " ORDER BY locked DESC, revision DESC, updated_at DESC",
            (
                scope_id,
                STATE_EFFECTIVE,
                subject_entity_id,
                object_entity_id,
                object_entity_id,
                subject_entity_id,
            ),
        ).fetchall()

    @staticmethod
    def _pair_key(subject_entity_id: str, object_entity_id: str) -> tuple[str, str]:
        """无序对的规范键。block 表按它做唯一约束，所以**两端必须走同一个函数**。"""
        return tuple(sorted((subject_entity_id, object_entity_id)))  # type: ignore[return-value]

    def _block(
        self, connection: sqlite3.Connection, scope_id: str, subject_entity_id: str, object_entity_id: str
    ) -> sqlite3.Row | None:
        left_entity_id, right_entity_id = self._pair_key(subject_entity_id, object_entity_id)
        row: sqlite3.Row | None = connection.execute(
            "SELECT id, locked FROM family_relationship_blocks"
            " WHERE scope_id = ? AND left_entity_id = ? AND right_entity_id = ?",
            (scope_id, left_entity_id, right_entity_id),
        ).fetchone()
        return row

    @staticmethod
    def _supersede_pair(
        connection: sqlite3.Connection,
        scope_id: str,
        subject_entity_id: str,
        object_entity_id: str,
        state: str,
        timestamp: str,
    ) -> None:
        """把这一对的双向有效边一次置成 ``state``。``retract`` 用 ``retracted``、
        ``set_relationship`` 用 ``superseded``——**两个不同的值，别合并**。"""
        connection.execute(
            "UPDATE family_relationships SET state = ?, updated_at = ?"
            " WHERE scope_id = ? AND state = ? AND ("
            " (subject_entity_id = ? AND object_entity_id = ?)"
            " OR (subject_entity_id = ? AND object_entity_id = ?))",
            (
                state,
                timestamp,
                scope_id,
                STATE_EFFECTIVE,
                subject_entity_id,
                object_entity_id,
                object_entity_id,
                subject_entity_id,
            ),
        )

    # ---- 读投影 ----------------------------------------------------------------

    _AVATAR_SQL = (
        "SELECT fi.id AS id FROM face_instances fi"
        " JOIN face_clusters fc ON fc.id = fi.cluster_id"
        " WHERE fc.entity_id = ? AND fc.status != 'rejected'"
        " ORDER BY fi.quality DESC, fi.detection_confidence DESC, fi.created_at ASC LIMIT 1"
    )
    _MEDIA_SQL = (
        "SELECT DISTINCT a.id AS id, a.media_type AS media_type, a.file_name AS file_name"
        " FROM face_instances fi"
        " JOIN face_clusters fc ON fc.id = fi.cluster_id"
        " JOIN assets a ON a.id = fi.asset_id"
        " WHERE fc.entity_id = ? AND fc.status != 'rejected'"
        " ORDER BY a.id DESC LIMIT 3"
    )
    _APPEARANCE_SQL = (
        "SELECT COUNT(*) AS count FROM face_instances fi"
        " JOIN face_clusters fc ON fc.id = fi.cluster_id"
        " WHERE fc.entity_id = ? AND fc.status != 'rejected'"
    )

    def family_graph_people(self, scope_ids: Sequence[str]) -> Sequence[FamilyPersonProjection]:
        """1.0 ``db.py:5649-5688``：**逐个 scope、逐个人物**地现场拼。N+1 是照搬。

        三套查询与 ``decorate_entities`` 的差别见端口 docstring。**这里的 N+1 与 1.0
        同形**（那边也是循环里三条子查询），且这条路径只在打开家庭图谱页时走一次。
        """
        people: list[FamilyPersonProjection] = []
        for scope_id in scope_ids:
            for entity in self._entities.list_entities(scope_id=scope_id):
                if entity.entity_type != "person":
                    continue
                # **用实体自己的 scope，不是外层那个查询 scope。**
                # 「全部相册」时外层传的是空串（`graph_scope_ids("")` → `[""]`），而
                # `list_entities(scope_id="")` 返回的是**所有** scope 的实体。拿空串当这些
                # 人的 scope 会同时错两处：`get_effective_membership("")` 查不到行 ⇒
                # 归属恒为 `null`；前端再拿这个空 scope 写回 ⇒ 400。实体自己知道它在哪一册。
                owner_scope = entity.scope_id or scope_id
                with self._database.connect() as connection:
                    avatar = connection.execute(self._AVATAR_SQL, (entity.id,)).fetchone()
                    media = connection.execute(self._MEDIA_SQL, (entity.id,)).fetchall()
                    count = connection.execute(self._APPEARANCE_SQL, (entity.id,)).fetchone()
                    membership = self.get_effective_membership(owner_scope, entity.id)
                    portrait = self.get_active_portrait(owner_scope, entity.id)
                    pending = entity.status == ENTITY_STATUS_PENDING
                    people.append(
                        FamilyPersonProjection(
                            id=entity.id,
                            scope_id=owner_scope,
                            display_name=(
                                FAMILY_PENDING_DISPLAY_NAME
                                if pending or not entity.canonical_name
                                else entity.canonical_name
                            ),
                            membership=membership,
                            portrait=portrait,
                            avatar_face_instance_id=avatar["id"] if avatar else None,
                            appearance_count=int((count or {"count": 0})["count"] or 0),
                            representative_media=tuple(dict(row) for row in media),
                        )
                    )
        return people

    # ---- 成员归属 --------------------------------------------------------------

    def get_effective_membership(self, scope_id: str, entity_id: str) -> FamilyMembershipRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_MEMBERSHIP_COLUMNS} FROM family_person_memberships"
                " WHERE scope_id = ? AND entity_id = ? AND state = ?"
                " ORDER BY locked DESC, revision DESC, updated_at DESC LIMIT 1",
                (scope_id, entity_id, STATE_EFFECTIVE),
            ).fetchone()
        return _row_to_membership(row) if row else None

    def set_membership(
        self,
        scope_id: str,
        entity_id: str,
        membership: str,
        *,
        source: str,
        confidence: float = 0.0,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> FamilyMembershipRecord | None:
        scope_id = scope_id or DEFAULT_SCOPE_ID
        membership = str(membership or "").strip()
        if membership not in FAMILY_MEMBERSHIPS:
            raise ValueError("unsupported family membership")
        if source not in FAMILY_SOURCES:
            raise ValueError("unsupported family membership source")
        with self._database.connect() as connection, connection:
            self._family_entity(connection, scope_id, entity_id)
            current = connection.execute(
                f"SELECT {_MEMBERSHIP_COLUMNS} FROM family_person_memberships"
                " WHERE scope_id = ? AND entity_id = ? AND state = ?"
                " ORDER BY locked DESC, revision DESC, updated_at DESC LIMIT 1",
                (scope_id, entity_id, STATE_EFFECTIVE),
            ).fetchone()
            # **静默跳过**：用户改过的归属，模型不许覆盖。返回的是**当前行**，
            # 与「刚写成功」形状一样。
            if source == SOURCE_MODEL and current is not None and current["locked"]:
                return _row_to_membership(current)
            timestamp = _now()
            if current is not None:
                connection.execute(
                    "UPDATE family_person_memberships SET state = ?, updated_at = ? WHERE id = ?",
                    (STATE_SUPERSEDED, timestamp, current["id"]),
                )
            record_id = _new_id("family_membership")
            connection.execute(
                "INSERT INTO family_person_memberships"
                " (id, scope_id, entity_id, membership, source, confidence, evidence_refs_json,"
                "  inference_run_id, state, locked, revision, supersedes_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    record_id,
                    scope_id,
                    entity_id,
                    membership,
                    source,
                    float(confidence or 0),
                    _dump(evidence_refs),
                    inference_run_id,
                    STATE_EFFECTIVE,
                    1 if source == SOURCE_USER_OVERRIDE else 0,
                    current["id"] if current is not None else None,
                    timestamp,
                    timestamp,
                ),
            )
            written = connection.execute(
                f"SELECT {_MEMBERSHIP_COLUMNS} FROM family_person_memberships WHERE id = ?",
                (record_id,),
            ).fetchone()
        return _row_to_membership(written) if written else None

    # ---- 关系边 ----------------------------------------------------------------

    def list_effective_relationships(
        self, scope_id: str, entity_id: str | None = None
    ) -> Sequence[FamilyRelationshipRecord]:
        with self._database.connect() as connection:
            if entity_id:
                rows = connection.execute(
                    f"SELECT {_RELATIONSHIP_COLUMNS} FROM family_relationships"
                    " WHERE scope_id = ? AND state = ?"
                    " AND (subject_entity_id = ? OR object_entity_id = ?)"
                    " ORDER BY locked DESC, updated_at DESC",
                    (scope_id, STATE_EFFECTIVE, entity_id, entity_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_RELATIONSHIP_COLUMNS} FROM family_relationships"
                    " WHERE scope_id = ? AND state = ? ORDER BY locked DESC, updated_at DESC",
                    (scope_id, STATE_EFFECTIVE),
                ).fetchall()
        return [_row_to_relationship(row) for row in rows]

    def set_relationship(
        self,
        scope_id: str,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        inverse_predicate: str,
        *,
        source: str,
        confidence: float = 0.0,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> Sequence[FamilyRelationshipRecord]:
        scope_id = scope_id or DEFAULT_SCOPE_ID
        if source not in FAMILY_SOURCES:
            raise ValueError("unsupported family relationship source")
        if subject_entity_id == object_entity_id:
            raise ValueError("family relationship requires distinct people")
        predicate = str(predicate or "").strip()
        inverse_predicate = str(inverse_predicate or "").strip()
        if not predicate or not inverse_predicate:
            raise ValueError("family relationship requires both predicates")
        validate_relationship_pair(predicate, inverse_predicate)

        with self._database.connect() as connection, connection:
            self._family_entity(connection, scope_id, subject_entity_id)
            self._family_entity(connection, scope_id, object_entity_id)
            block = self._block(connection, scope_id, subject_entity_id, object_entity_id)
            # 模型遇到用户撤回过的边：**什么都不做**。返回空列表是它唯一的信号，
            # 调用方（FamilyGraphService）据此跳过。
            if source == SOURCE_MODEL and block is not None and block["locked"]:
                return []
            # 用户重新建边 = 解锁：**先删 block**，再查 current——顺序反了会把
            # 刚解锁的那一对还当成「被 block 过」。
            if source == SOURCE_USER_OVERRIDE and block is not None:
                connection.execute("DELETE FROM family_relationship_blocks WHERE id = ?", (block["id"],))
            current = self._effective_pair(connection, scope_id, subject_entity_id, object_entity_id)
            if source == SOURCE_MODEL and any(row["locked"] for row in current):
                return [_row_to_relationship(row) for row in current]
            timestamp = _now()
            if current:
                self._supersede_pair(
                    connection,
                    scope_id,
                    subject_entity_id,
                    object_entity_id,
                    STATE_SUPERSEDED,
                    timestamp,
                )
            refs = _dump(evidence_refs)
            locked = 1 if source == SOURCE_USER_OVERRIDE else 0
            inserted: list[str] = []
            # **一次落两行**（正 + 反）。两行同 timestamp、同 source/confidence/refs，
            # 只是方向与标签对调——查一个人的关系时不用做反向映射。
            for left, relation, right, inverse in (
                (subject_entity_id, predicate, object_entity_id, inverse_predicate),
                (object_entity_id, inverse_predicate, subject_entity_id, predicate),
            ):
                record_id = _new_id("family_rel")
                connection.execute(
                    "INSERT INTO family_relationships"
                    " (id, scope_id, family_graph_id, subject_entity_id, predicate,"
                    "  object_entity_id, inverse_predicate, source, confidence,"
                    "  evidence_refs_json, inference_run_id, state, locked, revision,"
                    "  supersedes_id, created_at, updated_at)"
                    " VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)",
                    (
                        record_id,
                        scope_id,
                        left,
                        relation,
                        right,
                        inverse,
                        source,
                        float(confidence or 0),
                        refs,
                        inference_run_id,
                        STATE_EFFECTIVE,
                        locked,
                        timestamp,
                        timestamp,
                    ),
                )
                inserted.append(record_id)
            # 用一条 IN 一次取回，顺序按传入的 id 顺序排（正行在前、反行在后）。
            rows = connection.execute(
                f"SELECT {_RELATIONSHIP_COLUMNS} FROM family_relationships"
                f" WHERE id IN ({','.join('?' * len(inserted))})",
                inserted,
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
        return [_row_to_relationship(by_id[record_id]) for record_id in inserted]

    def retract_relationship(
        self, scope_id: str, subject_entity_id: str, object_entity_id: str
    ) -> Sequence[FamilyRelationshipRecord]:
        scope_id = scope_id or DEFAULT_SCOPE_ID
        with self._database.connect() as connection, connection:
            self._family_entity(connection, scope_id, subject_entity_id)
            self._family_entity(connection, scope_id, object_entity_id)
            current = self._effective_pair(connection, scope_id, subject_entity_id, object_entity_id)
            timestamp = _now()
            if current:
                self._supersede_pair(
                    connection,
                    scope_id,
                    subject_entity_id,
                    object_entity_id,
                    STATE_RETRACTED,
                    timestamp,
                )
            # **顺手写 block 挡住模型恢复**——撤回不只是改状态，它是一条护栏。
            left_entity_id, right_entity_id = self._pair_key(subject_entity_id, object_entity_id)
            block = self._block(connection, scope_id, subject_entity_id, object_entity_id)
            if block is not None:
                connection.execute(
                    "UPDATE family_relationship_blocks"
                    " SET source = ?, locked = 1, updated_at = ? WHERE id = ?",
                    (SOURCE_USER_OVERRIDE, timestamp, block["id"]),
                )
            else:
                connection.execute(
                    "INSERT INTO family_relationship_blocks"
                    " (id, scope_id, left_entity_id, right_entity_id, source, locked,"
                    "  created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        _new_id("family_rel_block"),
                        scope_id,
                        left_entity_id,
                        right_entity_id,
                        SOURCE_USER_OVERRIDE,
                        timestamp,
                        timestamp,
                    ),
                )
            rows = []
            if current:
                rows = connection.execute(
                    f"SELECT {_RELATIONSHIP_COLUMNS} FROM family_relationships"
                    f" WHERE id IN ({','.join('?' * len(current))})",
                    [row["id"] for row in current],
                ).fetchall()
        return [_row_to_relationship(row) for row in rows]

    # ---- 家庭画像 --------------------------------------------------------------

    def get_active_portrait(self, scope_id: str, entity_id: str) -> FamilyPortraitRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_PORTRAIT_COLUMNS} FROM family_person_portraits"
                " WHERE scope_id = ? AND entity_id = ? AND state = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (scope_id, entity_id, STATE_EFFECTIVE),
            ).fetchone()
        return _row_to_portrait(row) if row else None

    def write_portrait(
        self,
        scope_id: str,
        entity_id: str,
        portrait_text: str,
        *,
        source: str,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> FamilyPortraitRecord | None:
        """**不校验 ``source``**——1.0 这个函数就是只按 ``source == 'model'`` 分支，
        非法值原样落库。对比 ``set_membership`` / ``set_relationship`` 都会校验。
        缺口照搬，别顺手补：补上会让某个 1.0 能跑通的调用在这里抛错。"""
        with self._database.connect() as connection, connection:
            current = connection.execute(
                f"SELECT {_PORTRAIT_COLUMNS} FROM family_person_portraits"
                " WHERE scope_id = ? AND entity_id = ? AND state = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (scope_id, entity_id, STATE_EFFECTIVE),
            ).fetchone()
            if source == SOURCE_MODEL and current is not None and current["locked"]:
                return _row_to_portrait(current)
            timestamp = _now()
            if current is not None:
                connection.execute(
                    "UPDATE family_person_portraits SET state = ?, updated_at = ? WHERE id = ?",
                    (STATE_SUPERSEDED, timestamp, current["id"]),
                )
            portrait_id = _new_id("family_portrait")
            connection.execute(
                "INSERT INTO family_person_portraits"
                " (id, scope_id, entity_id, portrait_text, evidence_refs_json, source,"
                "  inference_run_id, state, locked, revision, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    portrait_id,
                    scope_id,
                    entity_id,
                    str(portrait_text or "").strip(),
                    _dump(evidence_refs),
                    source,
                    inference_run_id,
                    STATE_EFFECTIVE,
                    1 if source == SOURCE_USER_OVERRIDE else 0,
                    timestamp,
                    timestamp,
                ),
            )
            written = connection.execute(
                f"SELECT {_PORTRAIT_COLUMNS} FROM family_person_portraits WHERE id = ?",
                (portrait_id,),
            ).fetchone()
        return _row_to_portrait(written) if written else None

    # ---- 模型边的清场 + 运行 ----------------------------------------------------

    def supersede_model_relationships(self, scope_id: str) -> None:
        """1.0 ``db.py:5782-5790``。**先清场再推断**：这一句在 ``_persist`` 的最前面。

        ``locked = 0`` 那半个条件挡的是「用户写过、但 source 恰好是 model」的行——
        理论上不存在（写侧按 source 定 locked），但 1.0 写了这个条件，照搬。
        """
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE family_relationships SET state = ?, updated_at = ?"
                " WHERE scope_id = ? AND state = ? AND source = ? AND locked = 0",
                (STATE_SUPERSEDED, _now(), scope_id, STATE_EFFECTIVE, SOURCE_MODEL),
            )

    def create_run(self, scope_id: str, config: Mapping[str, object]) -> FamilyAnalysisRunRecord:
        run_id = _new_id("family_run")
        timestamp = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO family_analysis_runs"
                " (id, scope_id, status, current_stage, config_json, stats_json,"
                "  created_at, updated_at) VALUES (?, ?, 'queued', 'queued', ?, '{}', ?, ?)",
                (run_id, scope_id, json.dumps(dict(config), ensure_ascii=False), timestamp, timestamp),
            )
        created = self.get_run(run_id)
        if created is None:  # 刚写完就读不到只会是磁盘坏了
            raise LookupError(f"family analysis run disappeared: {run_id}")
        return created

    def get_run(self, run_id: str) -> FamilyAnalysisRunRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM family_analysis_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def latest_run(self, scope_id: str) -> FamilyAnalysisRunRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM family_analysis_runs WHERE scope_id = ? ORDER BY created_at DESC LIMIT 1",
                (scope_id,),
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        stats: Mapping[str, object] | None = None,
        error: str | None = None,
    ) -> FamilyAnalysisRunRecord | None:
        """**字段名与列名不是一一对应**：``stage`` → ``current_stage``、
        ``stats`` → ``stats_json``（要序列化）。1.0 用字典按顺序拼 SQL，这里照拼——
        列的顺序与值的顺序必须一致，所以用 ``fields`` 的插入序对齐。
        """
        fields: dict[str, object] = {"updated_at": _now()}
        if status is not None:
            fields["status"] = status
        if stage is not None:
            fields["current_stage"] = stage
        if stats is not None:
            fields["stats_json"] = json.dumps(dict(stats), ensure_ascii=False)
        if error is not None:
            fields["error"] = error
        if status == "completed":
            fields["completed_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._database.connect() as connection, connection:
            connection.execute(
                f"UPDATE family_analysis_runs SET {assignments} WHERE id = ?",
                (*fields.values(), run_id),
            )
        return self.get_run(run_id)

    def family_text_evidence(self, scope_id: str) -> Sequence[FamilyEvidenceRow]:
        """1.0 ``family_graph_service.py:174-190`` 那条大查询。

        1.0 选的 ``canonical_json`` 2.0 没有这一列（照抄会 ``no such column``），
        ``detail_json`` 有列但没有写入者。**1.0 装在 canonical 里的那批语义在 2.0
        是平铺列**，所以这里改选它们：``place`` / ``event_type`` / ``ocr_text`` /
        ``objects_json`` / ``clothing_json`` / ``people_json`` / ``facts_json``。

        这是一次**实测逼出来的补选**：只选 ``caption`` + ``activity`` 时，
        ``objects_json``（实测 46/53 行有值）这类数据在喂模型前就被丢掉了，
        而 1.0 是喂的。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT fc.entity_id, fi.id AS face_instance_id,"
                " COALESCE(em.confidence, fi.detection_confidence, 0) AS confidence,"
                " o.id AS observation_id, o.caption, o.activity, o.detail_json,"
                " o.place, o.event_type, o.ocr_text, o.objects_json, o.clothing_json,"
                " o.people_json, o.facts_json"
                " FROM face_instances fi"
                " JOIN face_clusters fc ON fc.id = fi.cluster_id"
                " JOIN entities e ON e.id = fc.entity_id"
                " JOIN observations o ON o.id = fi.observation_id"
                " LEFT JOIN entity_mentions em"
                "   ON em.entity_id = fc.entity_id AND em.face_instance_id = fi.id"
                " WHERE o.scope_id = ? AND e.scope_id = ?"
                " AND e.entity_type = 'person' AND e.status NOT IN ('rejected', 'superseded')"
                " ORDER BY o.created_at ASC",
                (scope_id, scope_id),
            ).fetchall()
        return [
            FamilyEvidenceRow(
                entity_id=row["entity_id"],
                face_instance_id=row["face_instance_id"] or "",
                confidence=float(row["confidence"] or 0),
                observation_id=row["observation_id"],
                caption=row["caption"] or "",
                activity=row["activity"] or "",
                detail_json=row["detail_json"] or "{}",
                place=row["place"] or "",
                event_type=row["event_type"] or "",
                ocr_text=row["ocr_text"] or "",
                objects_json=row["objects_json"] or "[]",
                clothing_json=row["clothing_json"] or "[]",
                people_json=row["people_json"] or "[]",
                facts_json=row["facts_json"] or "[]",
            )
            for row in rows
        ]

    # ---- 跨 scope 的图 ---------------------------------------------------------

    def merge_scopes(self, scope_ids: Sequence[str]) -> tuple[str, Sequence[str]]:
        ordered = list(dict.fromkeys(str(item) for item in scope_ids if item))
        if len(ordered) < 2:
            raise ValueError("at least two scopes are required")
        marks = ",".join("?" * len(ordered))
        with self._database.connect() as connection, connection:
            for scope_id in ordered:
                if (
                    connection.execute("SELECT id FROM memory_spaces WHERE id = ?", (scope_id,)).fetchone()
                    is None
                ):
                    raise ValueError("unknown memory space")
            # **先查已有的 graph_id，再 UPSERT**——顺序反了的话，刚写进去的行也会参与
            # ``ORDER BY updated_at DESC``，于是可能选到一张刚建出来的图。
            existing = connection.execute(
                f"SELECT graph_id FROM family_scope_groups WHERE scope_id IN ({marks})"
                " ORDER BY updated_at DESC LIMIT 1",
                ordered,
            ).fetchone()
            graph_id = existing["graph_id"] if existing else _new_id("family_graph")
            timestamp = _now()
            for scope_id in ordered:
                connection.execute(
                    "INSERT INTO family_scope_groups"
                    " (scope_id, graph_id, source, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(scope_id) DO UPDATE SET"
                    " graph_id = excluded.graph_id, updated_at = excluded.updated_at",
                    (scope_id, graph_id, SOURCE_USER_OVERRIDE, timestamp, timestamp),
                )
            # UPSERT 的 ``DO UPDATE`` **只改 graph_id 与 updated_at，不碰 source**——
            # 1.0 原样。已属于某张图的 scope 并过来时，它原来的 source 留着。
            rows = connection.execute(
                "SELECT scope_id FROM family_scope_groups WHERE graph_id = ? ORDER BY scope_id",
                (graph_id,),
            ).fetchall()
        return graph_id, [row["scope_id"] for row in rows]

    def graph_scope_ids(self, scope_id: str) -> Sequence[str]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT graph_id FROM family_scope_groups WHERE scope_id = ?", (scope_id,)
            ).fetchone()
            if row is None:
                return [scope_id]
            rows = connection.execute(
                "SELECT scope_id FROM family_scope_groups WHERE graph_id = ? ORDER BY scope_id",
                (row["graph_id"],),
            ).fetchall()
        return [item["scope_id"] for item in rows]
