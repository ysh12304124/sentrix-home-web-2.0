"""``PersonMergeRepository`` 的 SQLite 实现：一次事务把来源人物的数据改挂到目标人物。

端口 docstring 写了「为什么必须是一个事务」；这里写「每张表怎么搬」。规则只有三类：

1. **改挂**（``UPDATE OR IGNORE`` + 删除剩余）：提及、观察关联、事件参与者/实体、
   外观证据、人物瞬间。这些表带唯一约束，目标已有同一条时来源那条就是重复，删掉不丢信息。
2. **取舍**：关系（通用表与家庭图谱两套）、家庭归属、家庭画像。两边都有时只能留一份，
   取舍规则见各函数。
3. **退休**：来源的人物画像修订置 ``superseded``，来源实体置 ``superseded`` 并写审计；
   行都留着——外键与审计都指向它们，物理删除会让历史解释不通。

``semantic_profiles`` / 来源自己的 ``semantic_claims`` 不搬：那是对「来源这个人」的
派生总结，合并后要由目标的下一次画像/洞察运行重新得出，搬过去就是把两个人的总结硬拼。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.family import STATE_EFFECTIVE, STATE_SUPERSEDED
from sentrix.domain.identity.models import (
    CLUSTER_STATUS_CONFIRMED,
    CLUSTER_STATUS_PENDING,
    CLUSTER_STATUS_REJECTED,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_SUPERSEDED,
    NAME_STATE_CONFIRMED,
    PORTRAIT_STATUS_SUPERSEDED,
    RELATIONSHIP_STATUS_RETRACTED,
)
from sentrix.domain.identity.person_merge import (
    REVISION_PERSON_ABSORBED,
    REVISION_PERSON_MERGED_INTO,
    SOURCE_USER_PERSON_MERGE,
    PersonMergeOutcome,
    PersonMergePlan,
)
from sentrix.infrastructure.persistence.database import Database

# 带唯一约束、只需改挂的子表：(表, 指向人物的列)。冲突即重复，冲突行删除。
_REPOINT_TABLES: tuple[tuple[str, str], ...] = (
    ("entity_mentions", "entity_id"),
    ("entity_observations", "entity_id"),
    ("event_participants", "person_id"),
    ("event_entities", "entity_id"),
    ("person_appearance_evidence", "person_id"),
    ("person_moments", "person_id"),
)

_ACTIVE_CLUSTER_STATUSES = (CLUSTER_STATUS_PENDING, CLUSTER_STATUS_CONFIRMED)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _marks(values: Sequence[str]) -> str:
    return ",".join("?" * len(values))


class SqlitePersonMergeRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def merged_into(self, entity_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT new_value FROM entity_revisions"
                " WHERE entity_id = ? AND field_name = ? ORDER BY created_at DESC LIMIT 1",
                (entity_id, REVISION_PERSON_MERGED_INTO),
            ).fetchone()
        return str(row["new_value"]) if row and row["new_value"] else None

    def merge_people(self, plan: PersonMergePlan) -> PersonMergeOutcome:
        target = plan.target_id
        sources = list(plan.source_ids)
        with self._database.connect() as connection, connection:
            now = _now()
            target_cluster, moved_faces = self._merge_clusters(connection, plan, now)
            for table, column in _REPOINT_TABLES:
                self._repoint(connection, table, column, target, sources)
            connection.execute(
                f"UPDATE semantic_claims SET value_entity_id = ?, updated_at = ?"
                f" WHERE value_entity_id IN ({_marks(sources)})",
                (target, now, *sources),
            )
            connection.execute(
                f"UPDATE person_portrait_revisions SET status = ?, updated_at = ?"
                f" WHERE person_id IN ({_marks(sources)}) AND status = 'active'",
                (PORTRAIT_STATUS_SUPERSEDED, now, *sources),
            )
            moved_generic, dropped_generic = self._merge_generic_relationships(connection, plan, now)
            moved_family, dropped_family = self._merge_family_relationships(connection, plan, now)
            self._merge_blocks(connection, plan, now)
            self._merge_single_effective(connection, "family_person_memberships", plan, now)
            self._merge_single_effective(connection, "family_person_portraits", plan, now)
            self._update_entities(connection, plan, now)
            self._rewrite_people_snapshots(connection, plan)
            observation_ids = self._observation_ids(connection, target, target_cluster)
        return PersonMergeOutcome(
            target_cluster_id=target_cluster,
            moved_face_count=moved_faces,
            moved_relationship_count=moved_generic + moved_family,
            dropped_relationship_count=dropped_generic + dropped_family,
            observation_ids=observation_ids,
        )

    # ---- 人脸簇 ------------------------------------------------------------------

    @staticmethod
    def _merge_clusters(
        connection: sqlite3.Connection, plan: PersonMergePlan, now: str
    ) -> tuple[str | None, int]:
        """来源的有效簇实例并进目标的主簇；目标没有有效簇时，第一个来源簇直接改挂成主簇。

        退休的来源簇归属改成目标（与同名并入同一做法），历史上「这些脸曾是一簇」仍可追溯。
        主簇优先取已确认的、样本多的——它就是名录里这个人的头像和出现次数的来源。
        """
        row = connection.execute(
            "SELECT id FROM face_clusters WHERE entity_id = ? AND status IN (?, ?)"
            " ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, member_count DESC, updated_at DESC"
            " LIMIT 1",
            (plan.target_id, *_ACTIVE_CLUSTER_STATUSES, CLUSTER_STATUS_CONFIRMED),
        ).fetchone()
        target_cluster: str | None = row["id"] if row else None
        moved = 0
        for source in plan.source_ids:
            clusters = connection.execute(
                "SELECT id, status FROM face_clusters WHERE entity_id = ? ORDER BY created_at ASC, id ASC",
                (source,),
            ).fetchall()
            for cluster in clusters:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS n FROM face_instances WHERE cluster_id = ?", (cluster["id"],)
                    ).fetchone()["n"]
                )
                if cluster["status"] in _ACTIVE_CLUSTER_STATUSES and target_cluster is None:
                    connection.execute(
                        "UPDATE face_clusters SET entity_id = ?, updated_at = ?, revision = revision + 1"
                        " WHERE id = ?",
                        (plan.target_id, now, cluster["id"]),
                    )
                    target_cluster = cluster["id"]
                    moved += count
                    continue
                if cluster["status"] in _ACTIVE_CLUSTER_STATUSES:
                    connection.execute(
                        "UPDATE face_instances SET cluster_id = ? WHERE cluster_id = ?",
                        (target_cluster, cluster["id"]),
                    )
                    connection.execute("DELETE FROM face_prototypes WHERE cluster_id = ?", (cluster["id"],))
                    moved += count
                connection.execute(
                    "UPDATE face_clusters SET status = ?, entity_id = ?, member_count = 0, updated_at = ?,"
                    " revision = revision + 1 WHERE id = ?",
                    (CLUSTER_STATUS_REJECTED, plan.target_id, now, cluster["id"]),
                )
        if target_cluster is not None:
            next_status = (
                CLUSTER_STATUS_CONFIRMED if plan.status == ENTITY_STATUS_CONFIRMED else CLUSTER_STATUS_PENDING
            )
            connection.execute(
                "UPDATE face_clusters SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, now, target_cluster),
            )
        return target_cluster, moved

    # ---- 改挂 --------------------------------------------------------------------

    @staticmethod
    def _repoint(
        connection: sqlite3.Connection, table: str, column: str, target: str, sources: Sequence[str]
    ) -> None:
        marks = _marks(sources)
        connection.execute(
            f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} IN ({marks})",
            (target, *sources),
        )
        connection.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", tuple(sources))

    # ---- 关系 --------------------------------------------------------------------

    @staticmethod
    def _merge_generic_relationships(
        connection: sqlite3.Connection, plan: PersonMergePlan, now: str
    ) -> tuple[int, int]:
        """通用 ``relationships`` 表：端点换成目标；自环与目标已有的同一条关系作废。"""
        sources = set(plan.source_ids)
        marks = _marks(plan.source_ids)
        rows = connection.execute(
            "SELECT id, subject_entity_id, predicate, object_entity_id FROM relationships"
            f" WHERE (subject_entity_id IN ({marks}) OR object_entity_id IN ({marks}))"
            " AND status != ? ORDER BY created_at ASC, id ASC",
            (*plan.source_ids, *plan.source_ids, RELATIONSHIP_STATUS_RETRACTED),
        ).fetchall()
        moved = dropped = 0
        for row in rows:
            subject = plan.target_id if row["subject_entity_id"] in sources else row["subject_entity_id"]
            obj = plan.target_id if row["object_entity_id"] in sources else row["object_entity_id"]
            duplicate = connection.execute(
                "SELECT 1 FROM relationships WHERE subject_entity_id = ? AND predicate = ?"
                " AND object_entity_id = ? AND status != ? AND id != ? LIMIT 1",
                (subject, row["predicate"], obj, RELATIONSHIP_STATUS_RETRACTED, row["id"]),
            ).fetchone()
            if subject == obj or duplicate is not None:
                connection.execute(
                    "UPDATE relationships SET status = ?, updated_at = ?, revision = revision + 1"
                    " WHERE id = ?",
                    (RELATIONSHIP_STATUS_RETRACTED, now, row["id"]),
                )
                dropped += 1
                continue
            connection.execute(
                "UPDATE relationships SET subject_entity_id = ?, object_entity_id = ?, updated_at = ?,"
                " revision = revision + 1 WHERE id = ?",
                (subject, obj, now, row["id"]),
            )
            moved += 1
        return moved, dropped

    @staticmethod
    def _merge_family_relationships(
        connection: sqlite3.Connection, plan: PersonMergePlan, now: str
    ) -> tuple[int, int]:
        """家庭图谱的有效边（一条边两行）。按「对方是谁」分组取舍。

        - 两端都落到目标 → 自环，作废；
        - 同一个对方，目标与若干来源各有一组边 → 只留一组：有锁定行的组优先（目标先看），
          都没有锁定时目标的组优先，目标没有时取计划里排在前面的来源；其余组作废。
        - 留下的若是来源的组，端点换成目标。

        返回的计数是**边**数（两行算一条），与界面上看到的一致。
        """
        participants = [plan.target_id, *plan.source_ids]
        member = set(participants)
        marks = _marks(participants)
        rows = connection.execute(
            "SELECT id, subject_entity_id, object_entity_id, locked FROM family_relationships"
            f" WHERE state = ? AND (subject_entity_id IN ({marks}) OR object_entity_id IN ({marks}))",
            (STATE_EFFECTIVE, *participants, *participants),
        ).fetchall()

        def supersede(row_ids: Sequence[str]) -> None:
            if row_ids:
                connection.execute(
                    "UPDATE family_relationships SET state = ?, updated_at = ?"
                    f" WHERE id IN ({_marks(row_ids)})",
                    (STATE_SUPERSEDED, now, *row_ids),
                )

        self_loops: list[str] = []
        groups: dict[str, dict[str, list[sqlite3.Row]]] = {}
        for row in rows:
            subject, obj = row["subject_entity_id"], row["object_entity_id"]
            if subject in member and obj in member:
                # 目标自己与第三人无关的行不会进来；两端都是参与者的只可能是参与者之间的边。
                self_loops.append(row["id"])
                continue
            owner, other = (subject, obj) if subject in member else (obj, subject)
            groups.setdefault(other, {}).setdefault(owner, []).append(row)

        moved_rows = 0
        dropped_rows = len(self_loops)
        supersede(self_loops)
        for owners in groups.values():
            ordered = [owner for owner in participants if owner in owners]
            winner = next(
                (owner for owner in ordered if any(row["locked"] for row in owners[owner])),
                ordered[0],
            )
            for owner in ordered:
                if owner == winner:
                    continue
                supersede([row["id"] for row in owners[owner]])
                dropped_rows += len(owners[owner])
            if winner == plan.target_id:
                continue
            for row in owners[winner]:
                if row["subject_entity_id"] == winner:
                    connection.execute(
                        "UPDATE family_relationships SET subject_entity_id = ?, updated_at = ? WHERE id = ?",
                        (plan.target_id, now, row["id"]),
                    )
                else:
                    connection.execute(
                        "UPDATE family_relationships SET object_entity_id = ?, updated_at = ? WHERE id = ?",
                        (plan.target_id, now, row["id"]),
                    )
                moved_rows += 1
        return (moved_rows + 1) // 2, (dropped_rows + 1) // 2

    @staticmethod
    def _merge_blocks(connection: sqlite3.Connection, plan: PersonMergePlan, now: str) -> None:
        """撤回边留下的 block 行。无序对按排序键唯一，所以换端点后要重新排序。"""
        sources = set(plan.source_ids)
        marks = _marks(plan.source_ids)
        rows = connection.execute(
            "SELECT id, scope_id, left_entity_id, right_entity_id FROM family_relationship_blocks"
            f" WHERE left_entity_id IN ({marks}) OR right_entity_id IN ({marks})",
            (*plan.source_ids, *plan.source_ids),
        ).fetchall()
        for row in rows:
            left = plan.target_id if row["left_entity_id"] in sources else row["left_entity_id"]
            right = plan.target_id if row["right_entity_id"] in sources else row["right_entity_id"]
            if left == right:
                connection.execute("DELETE FROM family_relationship_blocks WHERE id = ?", (row["id"],))
                continue
            left, right = sorted((left, right))
            exists = connection.execute(
                "SELECT 1 FROM family_relationship_blocks WHERE scope_id = ? AND left_entity_id = ?"
                " AND right_entity_id = ? AND id != ?",
                (row["scope_id"], left, right, row["id"]),
            ).fetchone()
            if exists is not None:
                connection.execute("DELETE FROM family_relationship_blocks WHERE id = ?", (row["id"],))
                continue
            connection.execute(
                "UPDATE family_relationship_blocks SET left_entity_id = ?, right_entity_id = ?,"
                " updated_at = ? WHERE id = ?",
                (left, right, now, row["id"]),
            )

    @staticmethod
    def _merge_single_effective(
        connection: sqlite3.Connection, table: str, plan: PersonMergePlan, now: str
    ) -> None:
        """家庭归属 / 家庭画像：每人只该有一行有效值。

        目标没有有效行时，接过第一个有有效行的来源；目标的有效行未锁定而某个来源的已锁定
        （用户亲手写的）时，用户的那一行胜出。其余来源的有效行全部置 ``superseded``。
        """
        participants = [plan.target_id, *plan.source_ids]
        marks = _marks(participants)
        rows = connection.execute(
            f"SELECT id, entity_id, locked FROM {table}"
            f" WHERE state = ? AND entity_id IN ({marks})"
            " ORDER BY locked DESC, revision DESC, updated_at DESC",
            (STATE_EFFECTIVE, *participants),
        ).fetchall()
        if not rows:
            return
        by_owner: dict[str, sqlite3.Row] = {}
        for row in rows:
            by_owner.setdefault(row["entity_id"], row)
        ordered = [by_owner[owner] for owner in participants if owner in by_owner]
        winner = next((row for row in ordered if row["locked"]), ordered[0])
        losers = [row["id"] for row in rows if row["id"] != winner["id"]]
        if losers:
            connection.execute(
                f"UPDATE {table} SET state = ?, updated_at = ? WHERE id IN ({_marks(losers)})",
                (STATE_SUPERSEDED, now, *losers),
            )
        if winner["entity_id"] != plan.target_id:
            connection.execute(
                f"UPDATE {table} SET entity_id = ?, updated_at = ? WHERE id = ?",
                (plan.target_id, now, winner["id"]),
            )

    # ---- 实体与快照 --------------------------------------------------------------

    @staticmethod
    def _update_entities(connection: sqlite3.Connection, plan: PersonMergePlan, now: str) -> None:
        before = connection.execute(
            "SELECT canonical_name, status, name_state FROM entities WHERE id = ?", (plan.target_id,)
        ).fetchone()
        name_state = NAME_STATE_CONFIRMED if plan.name_confirmed else before["name_state"]
        confidence_floor = 1.0 if plan.status == ENTITY_STATUS_CONFIRMED else 0.0
        connection.execute(
            "UPDATE entities SET canonical_name = ?, status = ?, family_role = ?, name_state = ?,"
            " confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
            (
                plan.canonical_name,
                plan.status,
                plan.family_role,
                name_state,
                confidence_floor,
                now,
                plan.target_id,
            ),
        )
        connection.execute(
            f"UPDATE entities SET status = ?, updated_at = ? WHERE id IN ({_marks(plan.source_ids)})",
            (ENTITY_STATUS_SUPERSEDED, now, *plan.source_ids),
        )
        evidence = json.dumps(list(plan.source_ids), ensure_ascii=False)
        for source in plan.source_ids:
            _insert_revision(connection, source, REVISION_PERSON_MERGED_INTO, None, plan.target_id, "[]", now)
            _insert_revision(
                connection, plan.target_id, REVISION_PERSON_ABSORBED, None, source, evidence, now
            )
        if before["canonical_name"] != plan.canonical_name:
            _insert_revision(
                connection,
                plan.target_id,
                "canonical_name",
                before["canonical_name"],
                plan.canonical_name,
                evidence,
                now,
            )

    @staticmethod
    def _rewrite_people_snapshots(connection: sqlite3.Connection, plan: PersonMergePlan) -> None:
        """``observations.people_json`` 是身份层在观察上的快照（已确认人物的名字）。

        来源的条目换成目标；目标自己的条目也重写一遍——名字可能刚换过。只碰包含参与者
        id 的那些观察（``instr`` 过滤），不扫全表。
        """
        participants = [plan.target_id, *plan.source_ids]
        clause = " OR ".join("instr(people_json, ?) > 0" for _ in participants)
        rows = connection.execute(
            f"SELECT id, people_json FROM observations WHERE {clause}", tuple(participants)
        ).fetchall()
        member = set(participants)
        for row in rows:
            try:
                people = json.loads(row["people_json"] or "[]")
            except json.JSONDecodeError:
                continue
            if not isinstance(people, list):
                continue
            kept = [
                item for item in people if not (isinstance(item, dict) and item.get("entity_id") in member)
            ]
            if len(kept) == len(people):
                continue
            if plan.status == ENTITY_STATUS_CONFIRMED:
                kept.append({"entity_id": plan.target_id, "name": plan.canonical_name, "status": plan.status})
            connection.execute(
                "UPDATE observations SET people_json = ? WHERE id = ?",
                (json.dumps(kept, ensure_ascii=False), row["id"]),
            )

    @staticmethod
    def _observation_ids(
        connection: sqlite3.Connection, target: str, target_cluster: str | None
    ) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT DISTINCT observation_id FROM face_instances WHERE cluster_id = ?"
            " UNION SELECT DISTINCT observation_id FROM entity_mentions WHERE entity_id = ?",
            (target_cluster, target),
        ).fetchall()
        return tuple(sorted(str(row["observation_id"]) for row in rows if row["observation_id"]))


def _insert_revision(
    connection: sqlite3.Connection,
    entity_id: str,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    evidence_json: str,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO entity_revisions"
        " (id, entity_id, field_name, old_value, new_value, source, evidence_ids_json, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            _new_id("entity_revision"),
            entity_id,
            field_name,
            old_value,
            new_value,
            SOURCE_USER_PERSON_MERGE,
            evidence_json,
            now,
        ),
    )
