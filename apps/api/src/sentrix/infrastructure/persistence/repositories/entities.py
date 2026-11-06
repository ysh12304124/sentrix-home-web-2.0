"""``entities`` / ``entity_revisions`` / ``entity_properties`` / ``entity_mentions`` 仓储。

对应 1.0 ``backend/db.py`` 的 ``find_confirmed_person_by_name`` :4407、
``person_aliases`` :4399、``set_person_aliases`` :4428、``rename_person`` :4436、
``entity_event_ids`` :4553、``_record_entity_revision`` :4644、实体属性层 :4666-4750、
``_link_confirmed_entity_mention`` :4912 与 ``_add_entity_to_observation`` :5435。

**没收进来的两样，以及为什么。** ``maintain_entity_property_values``（多值合并，
``db.py:4731``）与 ``list_relationships``（``db.py:5590``）都不在这里：
前者的调用点全在语义实体抽取（``db.py:3840-4032`` 的 atmosphere / semantic_details），
那是 S7 的链路；后者的调用点在人物详情与家庭图谱（S7）。本切片没有调用者，
写进来就是零调用的能力——S4 为此栽过两次。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    ENTITY_STATUS_REJECTED,
    ENTITY_TYPE_PERSON,
    PROPERTY_SOURCE_USER,
    PROPERTY_STATUS_ACTIVE,
    PROPERTY_STATUS_PENDING,
    RETIRED_CANDIDATE_SUMMARY,
    REVISION_SOURCE_USER_RENAME,
    ClusterState,
    EntityDecoration,
    EntityMentionRecord,
    EntityPropertyRecord,
    EntityRecord,
    EntityRevisionRecord,
    EntitySpec,
    PersonNameMatch,
)
from sentrix.infrastructure.persistence.database import Database

_ENTITY_COLUMNS = (
    "id, scope_id, entity_type, canonical_name, status, family_role, summary, confidence,"
    " identity_state, role_state, name_state, created_at, updated_at"
)
_PROPERTY_COLUMNS = (
    "id, entity_id, property_key, value_json, source, confidence, status, evidence_ids_json,"
    " supersedes_property_id, revision, created_at, updated_at"
)
_MENTION_COLUMNS = "id, entity_id, observation_id, face_instance_id, confidence, created_at"

ALIAS_PROPERTY_KEY = "aliases"


def _new_id(prefix: str) -> str:
    """1.0 ``make_id``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _encode(value: object) -> str:
    """1.0 ``json_value``：``None`` 也编码成 ``"null"``，不落 SQL NULL。

    这个选择有下游后果：属性比较是同值判断，比的是**编码后的串**。把 ``None`` 写成 SQL
    NULL 会让 ``value_json == encoded`` 永远为假，于是每次派生都新起一行 pending。
    """
    return json.dumps(value, ensure_ascii=False)


def _decode(text: str | None, fallback: object = None) -> object:
    try:
        return json.loads(text or "null")
    except json.JSONDecodeError:
        return fallback


def _decode_list(text: str | None) -> list[object]:
    value = _decode(text, [])
    return value if isinstance(value, list) else []


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    """1.0 到处在用的 ``list(dict.fromkeys(...))``：保序去重。"""
    return tuple(dict.fromkeys(str(item) for item in values if str(item)))


def _row_to_entity(row: sqlite3.Row) -> EntityRecord:
    return EntityRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        entity_type=row["entity_type"],
        canonical_name=row["canonical_name"],
        status=row["status"],
        family_role=row["family_role"],
        summary=row["summary"],
        confidence=row["confidence"],
        identity_state=row["identity_state"],
        role_state=row["role_state"],
        name_state=row["name_state"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _row_to_property(row: sqlite3.Row) -> EntityPropertyRecord:
    evidence = _decode_list(row["evidence_ids_json"])
    return EntityPropertyRecord(
        id=row["id"],
        entity_id=row["entity_id"],
        property_key=row["property_key"],
        value=_decode(row["value_json"]),
        source=row["source"],
        confidence=row["confidence"],
        status=row["status"],
        evidence_ids=tuple(str(item) for item in evidence),
        supersedes_property_id=row["supersedes_property_id"],
        revision=row["revision"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _row_to_revision(row: sqlite3.Row) -> EntityRevisionRecord:
    evidence = _decode_list(row["evidence_ids_json"])
    return EntityRevisionRecord(
        id=row["id"],
        entity_id=row["entity_id"],
        field_name=row["field_name"],
        old_value=row["old_value"],
        new_value=row["new_value"],
        source=row["source"],
        evidence_ids=tuple(str(item) for item in evidence),
        created_at=_parse_datetime(row["created_at"]),
    )


def _row_to_mention(row: sqlite3.Row) -> EntityMentionRecord:
    return EntityMentionRecord(
        id=row["id"],
        entity_id=row["entity_id"],
        observation_id=row["observation_id"],
        face_instance_id=row["face_instance_id"],
        confidence=row["confidence"],
        created_at=_parse_datetime(row["created_at"]),
    )


class SqliteEntityRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- 实体 --------------------------------------------------------------------

    def get(self, entity_id: str) -> EntityRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
        return _row_to_entity(row) if row else None

    def list_entities(
        self, *, status: str | None = None, scope_id: str | None = None
    ) -> Sequence[EntityRecord]:
        """1.0 ``db.py:4041-4054`` 的 ``list_entities``，三段逐条对齐。

        1. **``status`` 给了就精确匹配，没给就排除 ``rejected`` / ``superseded``**
           （1.0 的 ``WHERE status = ?`` 与 ``WHERE status NOT IN (...)`` 两支）；
        2. **``scope_id`` 排除 ``include_in_people = 0`` 的空间**——恒有，不受参数影响；
        3. ``scope_id`` 传了再加一条 ``scope_id = ?``。

        没有 ``entity_type`` 过滤，也没有 LIMIT（1.0 同样是全量 + ``ORDER BY updated_at DESC``）。

        1.0 这个方法在返回前还有两段（``db.py:4054-4055``）：``_decorate_entities`` 补派生
        字段、``public_entity`` 做私密地点投影。2.0 把它们拆到别处——派生字段走
        ``decorate_entities``，投影是 domain 的纯函数——**读侧拼装在路由层完成**，
        与 ``routes/identity.py`` 既有的簇列表是同一条路。
        """
        excluded = "(SELECT id FROM memory_spaces WHERE include_in_people = 0)"
        clause = "status = ?" if status else "status NOT IN ('rejected', 'superseded')"
        params: list[object] = [status] if status else []
        if scope_id:
            clause += " AND scope_id = ?"
            params.append(scope_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities"
                f" WHERE {clause} AND scope_id NOT IN {excluded}"
                " ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [_row_to_entity(row) for row in rows]

    def recent_observation_ids(self, entity_id: str, *, limit: int = 100) -> Sequence[str]:
        """1.0 ``db.py:4590-4594`` 的 ``entity_observations ⋈ observations``，只取 id。

        **不选 ``o.*``。** 1.0 那条 SQL 选的是整行，但拿到之后**只用了 ``row["id"]``**，
        紧接着对每一行再 ``get_observation`` 一次——那个投影是多余的，2.0 不复刻它。
        下面那两层 N+1（逐条取观察、逐条取 asset）倒是照搬，因为它们的**结果**要进响应。

        跨offset按绝对时刻排序；相同拍摄时间以id稳定排序。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT o.id FROM entity_observations eob"
                " JOIN observations o ON o.id = eob.observation_id"
                " WHERE eob.entity_id = ? ORDER BY capture_sort_key(o.captured_at) DESC, o.id DESC LIMIT ?",
                (entity_id, limit),
            ).fetchall()
        return [row["id"] for row in rows]

    def decorate_entities(self, entity_ids: Sequence[str]) -> Mapping[str, EntityDecoration]:
        """1.0 ``db.py:4056-4141`` 的 ``_decorate_entities``，七条批量聚合逐条对齐。

        **每条都是一条 ``IN (...)``**，不是在实体循环里逐个查——1.0 的 docstring 专为
        这件事写过（"with batched queries instead of N+1 lookups"）。

        三处取舍，都不是省略：

        - **``relationship_count`` 是两条查询相加**：1.0 按 ``subject_entity_id`` 与
          ``object_entity_id`` 各 ``COUNT(*)`` 一次再相加（``db.py:4087-4098``），所以
          **一条关系给两端各记 1**。它唯一的过滤是 ``status != 'retracted'``——
          ``pending`` / ``superseded`` 都算，与 ``cluster_count`` 同口径。
        - **1.0 的两条簇查询合成了一条**：``cluster_count`` 的 ``COUNT(*)`` 与
          ``cluster_rows`` 的 ``member_count``/``status`` 用的是同一个 ``WHERE``
          （都只有 ``entity_id IN (...)``，都没有状态过滤），所以 ``len(rows)`` 就是那个
          count。**哪天其中一条加了过滤，这个等式立刻不成立**——合成的前提是过滤条件相同。
        - **avatar 的排序键照抄** 1.0 的 ``ORDER BY fi.detection_confidence DESC,
          fi.created_at ASC``。``created_at`` 是 ISO 串，字典序即时间序，不必解成时间
          （与 ``list_photos`` 那类查询是同一条理由）。

        ``member_count`` 可能是 NULL，1.0 用 ``int(row.get("member_count", 0) or 0)``
        兜底，这里同样 ``or 0``。
        """
        if not entity_ids:
            return {}
        ids = list(entity_ids)
        marks = ",".join("?" * len(ids))
        with self._database.connect() as connection:
            clusters: dict[str, list[ClusterState]] = {}
            for row in connection.execute(
                f"SELECT entity_id, status, member_count FROM face_clusters WHERE entity_id IN ({marks})",
                ids,
            ).fetchall():
                clusters.setdefault(row["entity_id"], []).append(
                    ClusterState(status=row["status"], member_count=int(row["member_count"] or 0))
                )
            photo_counts = {
                row["entity_id"]: int(row["c"])
                for row in connection.execute(
                    "SELECT fc.entity_id, COUNT(DISTINCT a.id) AS c FROM face_instances fi"
                    " JOIN face_clusters fc ON fc.id = fi.cluster_id"
                    " JOIN assets a ON a.id = fi.asset_id"
                    f" WHERE fc.entity_id IN ({marks}) AND fc.status != 'rejected'"
                    " GROUP BY fc.entity_id",
                    ids,
                ).fetchall()
            }
            mention_counts = {
                row["entity_id"]: int(row["c"])
                for row in connection.execute(
                    "SELECT entity_id, COUNT(*) AS c FROM entity_mentions"
                    f" WHERE entity_id IN ({marks}) GROUP BY entity_id",
                    ids,
                ).fetchall()
            }
            evidence_counts = {
                row["entity_id"]: int(row["c"])
                for row in connection.execute(
                    "SELECT entity_id, COUNT(*) AS c FROM entity_observations"
                    f" WHERE entity_id IN ({marks}) GROUP BY entity_id",
                    ids,
                ).fetchall()
            }
            avatars = {
                row["entity_id"]: row["id"]
                for row in connection.execute(
                    "SELECT entity_id, id FROM ("
                    " SELECT fc.entity_id, fi.id,"
                    " ROW_NUMBER() OVER (PARTITION BY fc.entity_id"
                    "  ORDER BY fi.detection_confidence DESC, fi.created_at ASC) AS rn"
                    " FROM face_instances fi JOIN face_clusters fc ON fc.id = fi.cluster_id"
                    f" WHERE fc.entity_id IN ({marks}) AND fc.status != 'rejected'"
                    ") WHERE rn = 1",
                    ids,
                ).fetchall()
            }
            as_subject = {
                row["eid"]: int(row["c"])
                for row in connection.execute(
                    "SELECT subject_entity_id AS eid, COUNT(*) AS c FROM relationships"
                    f" WHERE subject_entity_id IN ({marks}) AND status != 'retracted'"
                    " GROUP BY subject_entity_id",
                    ids,
                ).fetchall()
            }
            as_object = {
                row["eid"]: int(row["c"])
                for row in connection.execute(
                    "SELECT object_entity_id AS eid, COUNT(*) AS c FROM relationships"
                    f" WHERE object_entity_id IN ({marks}) AND status != 'retracted'"
                    " GROUP BY object_entity_id",
                    ids,
                ).fetchall()
            }
            previews = {
                row["entity_id"]: row
                for row in connection.execute(
                    "SELECT entity_id, asset_id, file_name, media_type FROM ("
                    " SELECT eob.entity_id, a.id AS asset_id, a.file_name, a.media_type,"
                    " ROW_NUMBER() OVER (PARTITION BY eob.entity_id"
                    "  ORDER BY capture_sort_key(o.captured_at) DESC, o.id DESC) AS rn"
                    " FROM entity_observations eob"
                    " JOIN observations o ON o.id = eob.observation_id"
                    " JOIN assets a ON a.id = o.asset_id"
                    f" WHERE eob.entity_id IN ({marks})"
                    ") WHERE rn = 1",
                    ids,
                ).fetchall()
            }
        decorated: dict[str, EntityDecoration] = {}
        for entity_id in ids:
            preview = previews.get(entity_id)
            decorated[entity_id] = EntityDecoration(
                cluster_count=len(clusters.get(entity_id, ())),
                photo_count=photo_counts.get(entity_id, 0),
                mention_count=mention_counts.get(entity_id, 0),
                evidence_count=evidence_counts.get(entity_id, 0),
                relationship_count=as_subject.get(entity_id, 0) + as_object.get(entity_id, 0),
                clusters=tuple(clusters.get(entity_id, ())),
                avatar_face_instance_id=avatars.get(entity_id),
                preview_asset_id=preview["asset_id"] if preview else None,
                preview_file_name=preview["file_name"] if preview else None,
                preview_media_type=preview["media_type"] if preview else None,
            )
        return decorated

    def find_or_create_entities(
        self,
        specs: Sequence[EntitySpec],
        *,
        scope_id: str = DEFAULT_SCOPE_ID,
        confidence: float = 0.0,
    ) -> Mapping[tuple[str, str], EntityRecord]:
        """1.0 ``db.py:3708-3749`` 的 ``_find_or_create_entities_batch``，四步逐条对齐。

        **按类型分组查而不是逐个查**：一张照片常有几十个物体，逐个 ``get`` 就是几十次
        往返。查询里的 ``status != 'rejected'`` 是必要条件——被否掉的实体不该被复用。
        排序 ``active → confirmed → 其余`` 取每个名字的**第一行**：同名有多个时优先那个
        已经在用的（1.0 的 ``ORDER BY CASE status ...``，它只取一行）。
        """
        unique: list[EntitySpec] = []
        seen: set[tuple[str, str]] = set()
        for spec in specs:
            key = (spec.entity_type, str(spec.name or "").strip())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            unique.append(EntitySpec(key[0], key[1], spec.summary))
        if not unique:
            return {}

        grouped: dict[str, list[str]] = {}
        for spec in unique:
            grouped.setdefault(spec.entity_type, []).append(spec.name)

        scope = scope_id or DEFAULT_SCOPE_ID
        now = _now()
        found: dict[tuple[str, str], EntityRecord] = {}
        with self._database.connect() as connection, connection:
            for entity_type, names in grouped.items():
                placeholders = ",".join("?" for _ in names)
                rows = connection.execute(
                    f"SELECT {_ENTITY_COLUMNS} FROM entities"
                    " WHERE scope_id = ? AND entity_type = ? AND status != ?"
                    f" AND canonical_name IN ({placeholders})"
                    " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'confirmed' THEN 1 ELSE 2 END",
                    (scope, entity_type, ENTITY_STATUS_REJECTED, *names),
                ).fetchall()
                taken: set[str] = set()
                ids: list[str] = []
                for row in rows:
                    if row["canonical_name"] in taken:
                        continue
                    taken.add(row["canonical_name"])
                    ids.append(row["id"])
                    found[(entity_type, row["canonical_name"])] = _row_to_entity(row)
                if ids:
                    marks = ",".join("?" for _ in ids)
                    connection.execute(
                        "UPDATE entities SET confidence = MAX(confidence, ?), updated_at = ?"
                        f" WHERE id IN ({marks})",
                        (float(confidence or 0), now, *ids),
                    )
            for spec in unique:
                if (spec.entity_type, spec.name) in found:
                    continue
                entity_id = _new_id("entity")
                connection.execute(
                    "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                    " family_role, summary, confidence, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        entity_id,
                        scope,
                        spec.entity_type,
                        spec.name,
                        ENTITY_STATUS_PENDING,
                        None,
                        spec.summary,
                        float(confidence or 0),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id = ?", (entity_id,)
                ).fetchone()
                assert row is not None
                found[(spec.entity_type, spec.name)] = _row_to_entity(row)
        return found

    def link_observation(
        self,
        entity_id: str,
        observation_id: str,
        *,
        confidence: float = 0.0,
        source: str = "observation_extraction",
    ) -> None:
        """1.0 ``db.py:3831-3837``。``ON CONFLICT`` 抬置信度而**不重复插入**——
        重跑一次导入不会在这张表里留第二行，那是「重跑导入幂等」的一半。"""
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO entity_observations"
                " (entity_id, observation_id, confidence, source, created_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(entity_id, observation_id)"
                " DO UPDATE SET confidence = MAX(confidence, excluded.confidence)",
                (entity_id, observation_id, float(confidence or 0), source, _now()),
            )

    def create_person(
        self,
        name: str,
        *,
        status: str = ENTITY_STATUS_PENDING,
        family_role: str | None = None,
        confidence: float = 0.0,
        summary: str = "",
        scope_id: str = DEFAULT_SCOPE_ID,
    ) -> EntityRecord:
        entity_id = _new_id("entity")
        now = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO entities"
                " (id, scope_id, entity_type, canonical_name, status, family_role, summary,"
                "  confidence, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    entity_id,
                    scope_id or DEFAULT_SCOPE_ID,
                    ENTITY_TYPE_PERSON,
                    name,
                    status,
                    family_role,
                    summary,
                    float(confidence or 0),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
        assert row is not None
        return _row_to_entity(row)

    def confirm_person(
        self, entity_id: str, *, name: str, family_role: str | None, summary: str
    ) -> EntityRecord:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE entities SET canonical_name = ?, status = ?, family_role = ?,"
                " confidence = MAX(confidence, 1), summary = ?, updated_at = ? WHERE id = ?",
                (name, ENTITY_STATUS_CONFIRMED, family_role, summary, _now(), entity_id),
            )
            row = connection.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
        assert row is not None
        return _row_to_entity(row)

    def delete_candidate(self, entity_id: str) -> bool:
        entity = self.get(entity_id)
        if entity is None or entity.entity_type != ENTITY_TYPE_PERSON:
            return False
        if entity.status == ENTITY_STATUS_CONFIRMED:
            raise ValueError("confirmed person entities cannot be deleted")
        with self._database.connect() as connection, connection:
            cluster_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM face_clusters WHERE entity_id = ?", (entity_id,)
                ).fetchall()
            ]
            for cluster_id in cluster_ids:
                # 人脸实例保留：它们是证据，只是不再属于任何簇。原型跟着簇走——
                # 簇没了，原型就没有可归属的行（外键指向 face_clusters）。
                connection.execute(
                    "UPDATE face_instances SET cluster_id = NULL WHERE cluster_id = ?",
                    (cluster_id,),
                )
                connection.execute("DELETE FROM face_prototypes WHERE cluster_id = ?", (cluster_id,))
                connection.execute("DELETE FROM face_clusters WHERE id = ?", (cluster_id,))
            # 1.0 只删 entity_mentions / entity_properties / entity_revisions 与几张
            # S5c 的表，其余子行留着不管——它没开外键，留着不报错。2.0 每次连接都
            # ``PRAGMA foreign_keys = ON``，所以**所有**指向 entities(id) 的子行都要走，
            # 否则这条 DELETE 会在某一天因为一行残留而整体失败。多出来的三张：
            #   entity_observations / event_participants / event_entities
            # 1.0 靠 ``_refresh_event_participants`` 把后两张清干净（Task 81 会接上投影
            # 刷新，但这一句仍要留着——它是删除的完整性，不是投影）。
            for table, column in (
                ("entity_mentions", "entity_id"),
                ("entity_observations", "entity_id"),
                ("entity_properties", "entity_id"),
                ("entity_revisions", "entity_id"),
                ("event_participants", "person_id"),
                ("event_entities", "entity_id"),
                ("person_appearance_evidence", "person_id"),
                ("person_moments", "person_id"),
            ):
                connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (entity_id,))
            connection.execute(
                "DELETE FROM relationships WHERE subject_entity_id = ? OR object_entity_id = ?",
                (entity_id, entity_id),
            )
            # 事实挂在**名字**上，不挂在 id 上——实体没了，用这个名字写的事实就没有主语了。
            connection.execute(
                "DELETE FROM facts WHERE subject = ? OR object = ?",
                (entity.canonical_name, entity.canonical_name),
            )
            connection.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        return True

    def find_confirmed_person_by_name(self, scope_id: str, name: str) -> PersonNameMatch | None:
        target = str(name or "").strip()
        if not target:
            return None
        with self._database.connect() as connection:
            exact = connection.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities"
                " WHERE scope_id = ? AND entity_type = ? AND status = ? AND canonical_name = ?"
                " ORDER BY updated_at DESC LIMIT 1",
                (scope_id, ENTITY_TYPE_PERSON, ENTITY_STATUS_CONFIRMED, target),
            ).fetchone()
            if exact is not None:
                return PersonNameMatch(entity_id=exact["id"], via_alias=False)
            # 精确名没有才轮到别名。顺序不能反：反了的话，A 的别名会抢走名字真的叫这个的 B。
            candidates = connection.execute(
                "SELECT id FROM entities WHERE scope_id = ? AND entity_type = ? AND status = ?",
                (scope_id, ENTITY_TYPE_PERSON, ENTITY_STATUS_CONFIRMED),
            ).fetchall()
        for candidate in candidates:
            if target in self.person_aliases(candidate["id"]):
                return PersonNameMatch(entity_id=candidate["id"], via_alias=True)
        return None

    def record_revision(
        self,
        entity_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        *,
        source: str,
        evidence_ids: Sequence[str] = (),
    ) -> EntityRevisionRecord:
        with self._database.connect() as connection, connection:
            return self._insert_revision(
                connection, entity_id, field_name, old_value, new_value, source, evidence_ids
            )

    @staticmethod
    def _insert_revision(
        connection: sqlite3.Connection,
        entity_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        source: str,
        evidence_ids: Sequence[str],
    ) -> EntityRevisionRecord:
        """审计行与它描述的变更必须同一个事务——分成两次提交会留下「改了但没留痕」的窗口。

        所以公开的 ``record_revision`` 与 ``set_property`` / ``rename_person`` 共用这一个
        函数，区别只在谁持有连接。
        """
        revision_id = _new_id("entity_revision")
        now = _now()
        connection.execute(
            "INSERT INTO entity_revisions"
            " (id, entity_id, field_name, old_value, new_value, source, evidence_ids_json, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                revision_id,
                entity_id,
                field_name,
                old_value,
                new_value,
                source,
                _encode(list(_dedupe(evidence_ids))),
                now,
            ),
        )
        return EntityRevisionRecord(
            id=revision_id,
            entity_id=entity_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            source=source,
            evidence_ids=_dedupe(evidence_ids),
            created_at=_parse_datetime(now),
        )

    # ---- 属性 --------------------------------------------------------------------

    def list_properties(
        self, entity_id: str, *, include_history: bool = False
    ) -> Sequence[EntityPropertyRecord]:
        clause = "" if include_history else " AND status IN ('active', 'pending')"
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_PROPERTY_COLUMNS} FROM entity_properties WHERE entity_id = ?{clause}"
                " ORDER BY property_key, revision DESC, updated_at DESC",
                (entity_id,),
            ).fetchall()
        return [_row_to_property(row) for row in rows]

    def maintain_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = "derived",
    ) -> EntityPropertyRecord:
        self._require_entity(entity_id)
        key = self._clean_key(property_key)
        evidence = _dedupe(evidence_ids)
        encoded = _encode(value)
        with self._database.connect() as connection, connection:
            current = self._current_property(connection, entity_id, key)
            if current is not None and current["source"] == PROPERTY_SOURCE_USER:
                # 用户的输入是权威。这里连「新起一条 pending」都不做——那同样会让界面上
                # 冒出第二个别名，只是把覆盖换成了并列。
                return _row_to_property(current)
            if current is not None and current["value_json"] == encoded:
                merged = _dedupe(
                    [str(item) for item in _decode_list(current["evidence_ids_json"])] + list(evidence)
                )
                connection.execute(
                    "UPDATE entity_properties SET confidence = MAX(confidence, ?),"
                    " evidence_ids_json = ?, updated_at = ? WHERE id = ?",
                    (float(confidence or 0), _encode(list(merged)), _now(), current["id"]),
                )
                return self._property_by_id(connection, current["id"])
            return self._insert_property(
                connection,
                entity_id=entity_id,
                property_key=key,
                encoded=encoded,
                source=source,
                confidence=confidence,
                evidence=evidence,
                # 没有当前值就是首次写入 → active；有当前值而值不同 → 改口，等人确认。
                status=PROPERTY_STATUS_ACTIVE if current is None else PROPERTY_STATUS_PENDING,
                supersedes=current["id"] if current is not None else None,
                revision=int(current["revision"] or 0) + 1 if current is not None else 1,
            )

    def maintain_property_values(
        self,
        entity_id: str,
        property_key: str,
        values: Sequence[object],
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = "derived",
    ) -> EntityPropertyRecord:
        """1.0 ``db.py:4728-4744``：多值属性**合并**而不是改口。

        三处与 ``maintain_property`` 相反的口径，都是 1.0 的：

        1. **用户值优先**——当前值是 ``user`` 就原样返回、不写；
        2. **已有值时就地 UPDATE，不新起 ``pending`` 行**（多值属性是累积的证据，
           每张照片起一条待确认的「改口」会让界面堆满没人确认的修订）；
        3. **空列表也照写**（首次），但合并时丢掉空项——``_dedupe`` 那句
           ``if str(item)`` 就是 1.0 的 ``if item``。

        首次写入走 ``_insert_property``（与单值属性同一条路，``active``），
        之后的合并走上面第 2 条。
        """
        self._require_entity(entity_id)
        key = self._clean_key(property_key)
        evidence = _dedupe(evidence_ids)
        with self._database.connect() as connection, connection:
            current = self._current_property(connection, entity_id, key)
            if current is not None and current["source"] == PROPERTY_SOURCE_USER:
                return _row_to_property(current)
            existing = _decode_list(current["value_json"]) if current is not None else []
            # 1.0 那一行是 ``dict.fromkeys([item for item in existing + list(values or [])
            # if item])``——**按原始值判真假、且不做字符串化**。
            # 写成先 ``str()`` 再判会留一个真实的坑：``str(None)`` 是 ``"None"``（真值），
            # 于是 ``None`` 会以字符串形态进列表。那是第一版的错，用例抓到了它。
            merged = list(dict.fromkeys([item for item in [*existing, *values] if item]))

            if current is None:
                return self._insert_property(
                    connection,
                    entity_id=entity_id,
                    property_key=key,
                    encoded=_encode(merged),
                    source=source,
                    confidence=confidence,
                    evidence=evidence,
                    status=PROPERTY_STATUS_ACTIVE,
                    supersedes=None,
                    revision=1,
                )
            connection.execute(
                "UPDATE entity_properties SET value_json = ?, confidence = MAX(confidence, ?),"
                " evidence_ids_json = ?, updated_at = ? WHERE id = ?",
                (
                    _encode(merged),
                    float(confidence or 0),
                    _encode(
                        list(
                            _dedupe(
                                [*[str(i) for i in _decode_list(current["evidence_ids_json"])], *evidence]
                            )
                        )
                    ),
                    _now(),
                    current["id"],
                ),
            )
            return self._property_by_id(connection, current["id"])

    def set_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        evidence_ids: Sequence[str] = (),
    ) -> EntityPropertyRecord:
        self._require_entity(entity_id)
        key = self._clean_key(property_key)
        evidence = _dedupe(evidence_ids)
        encoded = _encode(value)
        now = _now()
        with self._database.connect() as connection, connection:
            current = self._current_property(connection, entity_id, key)
            if current is not None:
                connection.execute(
                    "UPDATE entity_properties SET status = 'superseded', updated_at = ?"
                    " WHERE entity_id = ? AND property_key = ? AND status IN ('active', 'pending')",
                    (now, entity_id, key),
                )
            written = self._insert_property(
                connection,
                entity_id=entity_id,
                property_key=key,
                encoded=encoded,
                source=PROPERTY_SOURCE_USER,
                confidence=1.0,
                evidence=evidence,
                status=PROPERTY_STATUS_ACTIVE,
                supersedes=current["id"] if current is not None else None,
                revision=int(current["revision"] or 0) + 1 if current is not None else 1,
            )
            # 修订的旧值存**编码后的串**，与 1.0 一致：属性值的类型不固定（串 / 列表 / 字典），
            # 审计行统一成文本才可比。
            self._insert_revision(
                connection,
                entity_id,
                f"property:{key}",
                current["value_json"] if current is not None else None,
                encoded,
                PROPERTY_SOURCE_USER,
                evidence,
            )
        return written

    @staticmethod
    def _insert_property(
        connection: sqlite3.Connection,
        *,
        entity_id: str,
        property_key: str,
        encoded: str,
        source: str,
        confidence: float,
        evidence: tuple[str, ...],
        status: str,
        supersedes: str | None,
        revision: int,
    ) -> EntityPropertyRecord:
        property_id = _new_id("entity_property")
        now = _now()
        connection.execute(
            "INSERT INTO entity_properties"
            " (id, entity_id, property_key, value_json, source, confidence, status,"
            "  evidence_ids_json, supersedes_property_id, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                property_id,
                entity_id,
                property_key,
                encoded,
                source,
                float(confidence or 0),
                status,
                _encode(list(evidence)),
                supersedes,
                revision,
                now,
                now,
            ),
        )
        row = connection.execute(
            f"SELECT {_PROPERTY_COLUMNS} FROM entity_properties WHERE id = ?", (property_id,)
        ).fetchone()
        assert row is not None
        return _row_to_property(row)

    @staticmethod
    def _property_by_id(connection: sqlite3.Connection, property_id: str) -> EntityPropertyRecord:
        row = connection.execute(
            f"SELECT {_PROPERTY_COLUMNS} FROM entity_properties WHERE id = ?", (property_id,)
        ).fetchone()
        assert row is not None
        return _row_to_property(row)

    @staticmethod
    def _current_property(
        connection: sqlite3.Connection, entity_id: str, property_key: str
    ) -> sqlite3.Row | None:
        """当前生效的一行：``active`` 优先于 ``pending``，再按 ``revision`` 降序。

        ``active`` 优先是这条 SQL 的全部要点：派生写出的新值转 ``pending`` 之后，
        「当前值」仍然是那条已确认的旧值，直到有人确认新值为止。
        """
        row = connection.execute(
            f"SELECT {_PROPERTY_COLUMNS} FROM entity_properties"
            " WHERE entity_id = ? AND property_key = ? AND status IN ('active', 'pending')"
            " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, revision DESC,"
            " updated_at DESC LIMIT 1",
            (entity_id, property_key),
        ).fetchone()
        return row if row is not None else None

    def _require_entity(self, entity_id: str) -> None:
        """1.0 的属性写入对不存在的实体抛 ``KeyError``，不是静默返回——属性挂在实体上，
        没有实体就没有可挂的地方，静默会让调用方以为写成功了。"""
        if self.get(entity_id) is None:
            raise KeyError(entity_id)

    @staticmethod
    def _clean_key(property_key: str) -> str:
        key = str(property_key or "").strip()
        if not key:
            raise ValueError("property_key is required")
        return key

    # ---- 别名与改名 --------------------------------------------------------------

    def person_aliases(self, entity_id: str) -> tuple[str, ...]:
        with self._database.connect() as connection:
            row = self._current_property(connection, entity_id, ALIAS_PROPERTY_KEY)
        if row is None:
            return ()
        value = _decode(row["value_json"])
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        return (str(value),) if value else ()

    def set_person_aliases(self, entity_id: str, aliases: Sequence[str]) -> tuple[str, ...]:
        cleaned = _dedupe([str(item or "").strip() for item in aliases if str(item or "").strip()])
        if self.get(entity_id) is None:
            return ()
        self.set_property(entity_id, ALIAS_PROPERTY_KEY, list(cleaned))
        return self.person_aliases(entity_id)

    def rename_person(self, entity_id: str, new_name: str) -> EntityRecord | None:
        entity = self.get(entity_id)
        if entity is None or entity.entity_type != ENTITY_TYPE_PERSON:
            return None
        name = str(new_name or "").strip()
        if not name:
            raise ValueError("person name is required")
        old_name = entity.canonical_name
        if old_name != name:
            if old_name:
                aliases = list(self.person_aliases(entity_id))
                if old_name not in aliases:
                    aliases.append(old_name)
                self.set_person_aliases(entity_id, aliases)
            with self._database.connect() as connection, connection:
                now = _now()
                connection.execute(
                    "UPDATE entities SET canonical_name = ?, name_state = 'confirmed', updated_at = ?"
                    " WHERE id = ?",
                    (name, now, entity_id),
                )
                self._insert_revision(
                    connection,
                    entity_id,
                    "canonical_name",
                    old_name,
                    name,
                    REVISION_SOURCE_USER_RENAME,
                    (),
                )
        return self.get(entity_id)

    # ---- 提及桥 ------------------------------------------------------------------

    def link_confirmed_mention(
        self,
        entity_id: str,
        observation_id: str,
        *,
        face_instance_id: str | None = None,
        confidence: float = 0.0,
    ) -> EntityMentionRecord:
        entity = self.get(entity_id)
        if entity is None:
            raise KeyError(entity_id)
        if entity.status != ENTITY_STATUS_CONFIRMED:
            # 1.0 是在调用点用 ``if entity.get("status") == "confirmed"`` 挡的。2.0 挡在
            # 这里：这条规则一旦漏掉，观察的人物快照里就会混进一个聚类猜出来的候选人，
            # 而它是 ``person_score`` 与 ``person_bridge`` 的输入——一处漏判，后面全是事实。
            raise ValueError(f"只有已确认的实体才写提及，{entity_id} 当前是 {entity.status}")
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO entity_mentions"
                " (id, entity_id, observation_id, face_instance_id, confidence, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    _new_id("mention"),
                    entity_id,
                    observation_id,
                    face_instance_id,
                    float(confidence or 0),
                    _now(),
                ),
            )
            row = connection.execute(
                f"SELECT {_MENTION_COLUMNS} FROM entity_mentions"
                " WHERE entity_id = ? AND observation_id = ? AND face_instance_id IS ?",
                (entity_id, observation_id, face_instance_id),
            ).fetchone()
            assert row is not None
            self._append_person_snapshot(connection, observation_id, entity)
        return _row_to_mention(row)

    @staticmethod
    def _append_person_snapshot(
        connection: sqlite3.Connection, observation_id: str, entity: EntityRecord
    ) -> None:
        """把这个人追加进 ``observations.people_json``（1.0 ``_add_entity_to_observation``）。

        写的是**另一个聚合的表**，这一点是有意的：``people_json`` 是身份层在观察上的投影，
        它没有自己的写入用例，跟着提及一起写才不会出现「有提及、快照里没人」。已存在就不
        重复追加，所以重复调用幂等。
        """
        row = connection.execute(
            "SELECT people_json FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if row is None:
            return
        people = _decode_list(row["people_json"])
        if not any(isinstance(item, dict) and item.get("entity_id") == entity.id for item in people):
            people.append({"entity_id": entity.id, "name": entity.canonical_name, "status": entity.status})
        connection.execute(
            "UPDATE observations SET people_json = ? WHERE id = ?",
            (_encode(people), observation_id),
        )

    # ---- 事件关联 ----------------------------------------------------------------

    def confirmed_person_names(self, scope_id: str) -> frozenset[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT canonical_name FROM entities"
                " WHERE scope_id = ? AND entity_type = ? AND status = ?",
                (scope_id, ENTITY_TYPE_PERSON, ENTITY_STATUS_CONFIRMED),
            ).fetchall()
        return frozenset(str(row["canonical_name"]) for row in rows)

    def confirmed_people_for_observation(self, observation_id: str) -> frozenset[str]:
        if not observation_id:
            return frozenset()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT em.entity_id FROM entity_mentions em"
                " JOIN entities e ON e.id = em.entity_id"
                " WHERE em.observation_id = ? AND e.entity_type = ? AND e.status = ?",
                (observation_id, ENTITY_TYPE_PERSON, ENTITY_STATUS_CONFIRMED),
            ).fetchall()
        return frozenset(str(row["entity_id"]) for row in rows)

    def confirmed_people_for_event(self, event_id: str) -> frozenset[str]:
        if not event_id:
            return frozenset()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT em.entity_id FROM entity_mentions em"
                " JOIN entities e ON e.id = em.entity_id"
                " JOIN event_observations eo ON eo.observation_id = em.observation_id"
                " WHERE eo.event_id = ? AND e.entity_type = ? AND e.status = ?",
                (event_id, ENTITY_TYPE_PERSON, ENTITY_STATUS_CONFIRMED),
            ).fetchall()
        return frozenset(str(row["entity_id"]) for row in rows)

    def retire_unclustered_candidates(self) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE entities SET status = ?, summary = ?, updated_at = ?"
                " WHERE entity_type = ? AND status != ?"
                " AND NOT EXISTS (SELECT 1 FROM face_clusters fc WHERE fc.entity_id = entities.id"
                " AND fc.status IN ('pending', 'confirmed') AND fc.member_count > 0)",
                (
                    ENTITY_STATUS_REJECTED,
                    RETIRED_CANDIDATE_SUMMARY,
                    _now(),
                    ENTITY_TYPE_PERSON,
                    ENTITY_STATUS_CONFIRMED,
                ),
            )
            return int(cursor.rowcount)

    def event_ids(self, entity_id: str) -> Sequence[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT event_id FROM (
                    SELECT eo.event_id FROM entity_mentions em
                    JOIN event_observations eo ON eo.observation_id = em.observation_id
                    WHERE em.entity_id = ?
                    UNION
                    SELECT event_id FROM event_participants WHERE person_id = ?
                    UNION
                    SELECT eo.event_id FROM entity_observations eob
                    JOIN event_observations eo ON eo.observation_id = eob.observation_id
                    WHERE eob.entity_id = ?
                )""",
                (entity_id, entity_id, entity_id),
            ).fetchall()
        return [row["event_id"] for row in rows]
