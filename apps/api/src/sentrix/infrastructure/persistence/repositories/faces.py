"""人脸簇 / 实例 / 原型仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py`` 的 ``list_face_clusters`` :5133、``_refresh_face_prototypes``
:4922、``get_face_instance`` :4467、``list_face_prototypes`` :4547。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sentrix.domain.identity.models import (
    CLUSTER_STATUS_CONFIRMED,
    CLUSTER_STATUS_REJECTED,
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    FACE_VALIDITY_UNCERTAIN,
    FACE_VALIDITY_VERIFIED,
    NAME_STATE_CONFIRMED,
    PENDING_CLUSTER_NAME,
    FaceClusterRecord,
    FaceInstanceRecord,
    FaceManifestRecord,
    FacePrototypeRecord,
    PhotoIdentityRecord,
)
from sentrix.infrastructure.persistence.database import Database

_CLUSTER_COLUMNS = (
    "fc.id, fc.scope_id, fc.status, fc.entity_id, fc.representative_embedding_json,"
    " fc.member_count, fc.confidence, fc.revision, fc.created_at, fc.updated_at,"
    " e.canonical_name, e.family_role, e.status AS entity_status, e.name_state"
)
_INSTANCE_COLUMNS = (
    "fi.id, fi.asset_id, fi.observation_id, fi.cluster_id, fi.bbox_json, fi.embedding_json,"
    " fi.detection_confidence, fi.quality, fi.pose_json, fi.pose_bucket, fi.embedding_model,"
    " fi.embedding_version, fi.embedding_quality_signal, fi.validity, fi.area_ratio,"
    " fi.sharpness, fi.created_at, a.path AS asset_path, a.file_name, a.mime_type,"
    " a.media_type, a.scope_id AS asset_scope_id"
)
_PROTOTYPE_COLUMNS = (
    "id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality, model_name, model_version"
)


def _new_id(prefix: str) -> str:
    """1.0 ``make_id``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _vector(value: str | None) -> tuple[float, ...]:
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    return tuple(float(item) for item in items) if isinstance(items, list) else ()


def _row_to_cluster(row: sqlite3.Row) -> FaceClusterRecord:
    # 未确认的簇不许把内部名字与家庭角色漏出去（1.0 ``app.py:1976`` 在路由层做这件事，
    # 2.0 落在出口处）。判据是**实体**状态而不是簇状态：簇可以已经 confirmed 而实体还在
    # pending，那种情况下名字同样还没被用户认可。
    # 改名只转 name_state，不改 status；用户已经给过名字的簇要显示真名。
    pending = row["entity_status"] == ENTITY_STATUS_PENDING and row["name_state"] != NAME_STATE_CONFIRMED
    return FaceClusterRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        status=row["status"],
        entity_id=row["entity_id"],
        representative_embedding=_vector(row["representative_embedding_json"]),
        member_count=row["member_count"],
        confidence=row["confidence"],
        revision=row["revision"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        canonical_name=PENDING_CLUSTER_NAME if pending else row["canonical_name"],
        family_role=None if pending else row["family_role"],
        entity_status=row["entity_status"],
    )


def _row_to_instance(row: sqlite3.Row) -> FaceInstanceRecord:
    return FaceInstanceRecord(
        id=row["id"],
        asset_id=row["asset_id"],
        observation_id=row["observation_id"],
        cluster_id=row["cluster_id"],
        bbox=_vector(row["bbox_json"]),
        embedding=_vector(row["embedding_json"]),
        detection_confidence=row["detection_confidence"],
        quality=row["quality"],
        pose=_vector(row["pose_json"]),
        pose_bucket=row["pose_bucket"],
        embedding_model=row["embedding_model"],
        embedding_version=row["embedding_version"],
        embedding_quality_signal=row["embedding_quality_signal"],
        validity=row["validity"],
        area_ratio=row["area_ratio"],
        sharpness=row["sharpness"],
        created_at=_parse_datetime(row["created_at"]),
        # 1.0 ``get_face_instance`` 从 asset 取 scope，缺省 ``home-default``。
        scope_id=row["asset_scope_id"] or DEFAULT_SCOPE_ID,
        asset_path=row["asset_path"],
        file_name=row["file_name"],
        mime_type=row["mime_type"] or "",
        media_type=row["media_type"],
    )


def _row_to_prototype(row: sqlite3.Row) -> FacePrototypeRecord:
    return FacePrototypeRecord(
        id=row["id"],
        cluster_id=row["cluster_id"],
        face_instance_id=row["face_instance_id"],
        pose_bucket=row["pose_bucket"],
        embedding=_vector(row["embedding_json"]),
        quality=row["quality"],
        model_name=row["model_name"],
        model_version=row["model_version"],
    )


class SqliteFaceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_clusters(self, *, status: str | None = None) -> Sequence[FaceClusterRecord]:
        clause = "fc.status = ?" if status else "fc.status != 'rejected'"
        params: list[object] = [status] if status else []
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                # INNER JOIN 有意为之：没有记忆空间行的 scope 查不到任何簇。1.0 就是这样，
                # 而「默认空间没建过行」在 1.0 是真实发生过的——那时列表是空的，不是全量。
                " JOIN memory_spaces ms ON ms.id = fc.scope_id"
                f" WHERE {clause} AND ms.include_in_people = 1"
                " ORDER BY fc.updated_at DESC",
                params,
            ).fetchall()
        return [_row_to_cluster(row) for row in rows]

    def list_clusters_for_entity(self, entity_id: str) -> Sequence[FaceClusterRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                " WHERE fc.entity_id = ? ORDER BY fc.updated_at DESC",
                (entity_id,),
            ).fetchall()
        return [_row_to_cluster(row) for row in rows]

    def list_candidates(self, scope_id: str) -> Sequence[FaceClusterRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                " WHERE fc.scope_id = ? AND fc.status IN ('pending', 'confirmed')"
                " ORDER BY fc.updated_at DESC",
                (scope_id,),
            ).fetchall()
        return [_row_to_cluster(row) for row in rows]

    def get_cluster(self, cluster_id: str) -> FaceClusterRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id WHERE fc.id = ?",
                (cluster_id,),
            ).fetchone()
        return _row_to_cluster(row) if row else None

    def create_cluster(
        self, embedding: Sequence[float], *, confidence: float, scope_id: str
    ) -> FaceClusterRecord:
        cluster_id = _new_id("cluster")
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO face_clusters"
                " (id, scope_id, representative_embedding_json, member_count, confidence,"
                "  created_at, updated_at)"
                " VALUES (?, ?, ?, 0, ?, ?, ?)",
                (
                    cluster_id,
                    scope_id or DEFAULT_SCOPE_ID,
                    json.dumps([float(value) for value in embedding]),
                    float(confidence or 0),
                    now,
                    now,
                ),
            )
        cluster = self.get_cluster(cluster_id)
        assert cluster is not None
        return cluster

    def link_entity(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE face_clusters SET entity_id = ?, updated_at = ? WHERE id = ?",
                (entity_id, now, cluster_id),
            )
        cluster = self.get_cluster(cluster_id)
        assert cluster is not None
        return cluster

    def confirm_cluster(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE face_clusters SET status = ?, entity_id = ?, updated_at = ?,"
                " revision = revision + 1 WHERE id = ?",
                (CLUSTER_STATUS_CONFIRMED, entity_id, now, cluster_id),
            )
        cluster = self.get_cluster(cluster_id)
        assert cluster is not None
        return cluster

    def retire_cluster(self, cluster_id: str, *, entity_id: str | None = None) -> FaceClusterRecord:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            if entity_id is None:
                connection.execute(
                    "UPDATE face_clusters SET status = ?, member_count = 0, updated_at = ?,"
                    " revision = revision + 1 WHERE id = ?",
                    (CLUSTER_STATUS_REJECTED, now, cluster_id),
                )
            else:
                connection.execute(
                    "UPDATE face_clusters SET status = ?, entity_id = ?, member_count = 0,"
                    " updated_at = ?, revision = revision + 1 WHERE id = ?",
                    (CLUSTER_STATUS_REJECTED, entity_id, now, cluster_id),
                )
        cluster = self.get_cluster(cluster_id)
        assert cluster is not None
        return cluster

    def move_instances(self, source_cluster_id: str, target_cluster_id: str) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE face_instances SET cluster_id = ? WHERE cluster_id = ?",
                (target_cluster_id, source_cluster_id),
            )
            return int(cursor.rowcount)

    def move_instance(self, instance_id: str, target_cluster_id: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE face_instances SET cluster_id = ? WHERE id = ?",
                (target_cluster_id, instance_id),
            )

    def active_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                " WHERE fc.entity_id = ? AND fc.status IN ('pending', 'confirmed')"
                " AND fc.member_count > 0"
                " ORDER BY CASE fc.status WHEN 'pending' THEN 0 ELSE 1 END, fc.updated_at DESC"
                " LIMIT 1",
                (entity_id,),
            ).fetchone()
        return _row_to_cluster(row) if row else None

    def confirmed_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                " WHERE fc.entity_id = ? AND fc.status = 'confirmed'"
                " ORDER BY fc.member_count DESC, fc.updated_at DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        return _row_to_cluster(row) if row else None

    def add_instance(
        self,
        *,
        asset_id: str,
        observation_id: str,
        cluster_id: str | None,
        bbox: Sequence[float],
        embedding: Sequence[float],
        detection_confidence: float,
        quality: float,
        pose: Sequence[float],
        pose_bucket_name: str,
        embedding_model: str,
        embedding_version: str,
        embedding_quality_signal: float,
        validity: str,
        area_ratio: float,
        sharpness: float,
    ) -> FaceInstanceRecord:
        instance_id = _new_id("face")
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO face_instances"
                " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
                "  detection_confidence, quality, pose_json, area_ratio, sharpness,"
                "  pose_bucket, embedding_model, embedding_version, embedding_quality_signal,"
                "  validity, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    instance_id,
                    asset_id,
                    observation_id,
                    cluster_id,
                    json.dumps([float(value) for value in bbox]),
                    json.dumps([float(value) for value in embedding]),
                    float(detection_confidence or 0),
                    float(quality or 0),
                    json.dumps([float(value) for value in pose]),
                    float(area_ratio or 0),
                    float(sharpness or 0),
                    str(pose_bucket_name or "unknown"),
                    embedding_model,
                    embedding_version,
                    float(embedding_quality_signal or 0),
                    validity,
                    datetime.now(UTC).isoformat(),
                ),
            )
        instance = self.get_instance(instance_id)
        assert instance is not None
        return instance

    def list_instances(self, cluster_id: str) -> Sequence[FaceInstanceRecord]:
        return self._instances_where("fi.cluster_id = ?", (cluster_id,))

    def list_face_manifest(self, asset_id: str) -> Sequence[FaceManifestRecord]:
        """1.0 ``tools.py:2328-2357`` 那条 SQL，逐字。

        **两个 LEFT JOIN 与 ``list_photo_identities`` 的两个 INNER JOIN 是相反的口径**：
        这条要的是「图里有几张脸」（含没绑簇的、簇没绑实体的），那条要的是「图里有谁」。
        两条都保留，因为两个问题在 1.0 里都要答。

        ``face_id`` 由这里的行序分配（``face_1``…）——排序在 SQL 里，编号跟着它走；
        放到调用方去数会让「两次调用给同一张脸不同编号」有可乘之机，而模型要靠这个编号
        回指某个人。1.0 的第三键 ``fi.id`` 正是为此存在的。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT fi.id AS face_instance_id, fi.asset_id, fi.bbox_json,
                       fi.detection_confidence, fi.quality,
                       fc.id AS cluster_id, fc.status AS cluster_status,
                       e.canonical_name, e.family_role, e.status AS entity_status
                FROM readable_face_instances fi
                LEFT JOIN readable_face_clusters fc ON fc.id = fi.cluster_id
                LEFT JOIN readable_entities e ON e.id = fc.entity_id
                WHERE fi.asset_id = ?
                ORDER BY fi.quality DESC, fi.detection_confidence DESC, fi.id
                """,
                (asset_id,),
            ).fetchall()
        manifest: list[FaceManifestRecord] = []
        for index, row in enumerate(rows, 1):
            # 1.0 ``:2343-2347`` 的三重合取——「未确认者不得猜姓名」就落在这一行。
            confirmed = bool(
                row["cluster_status"] == CLUSTER_STATUS_CONFIRMED
                and row["entity_status"] == ENTITY_STATUS_CONFIRMED
                and row["canonical_name"]
            )
            manifest.append(
                FaceManifestRecord(
                    face_id=f"face_{index}",
                    face_instance_id=str(row["face_instance_id"] or ""),
                    asset_id=str(row["asset_id"] or asset_id),
                    bbox=_vector(row["bbox_json"]),
                    identity_status="confirmed" if confirmed else "unconfirmed",
                    person_name=str(row["canonical_name"] or "") if confirmed else "",
                    family_role=str(row["family_role"] or "") if confirmed else "",
                    detection_confidence=row["detection_confidence"],
                    quality=row["quality"],
                )
            )
        return manifest

    def list_photo_identities(self, asset_id: str) -> Sequence[PhotoIdentityRecord]:
        """1.0 ``tools.py:2281-2300`` 那条 SQL，逐字。

        **两个 INNER JOIN 是判据本身**，不是写法：没有簇的脸、簇没绑实体的脸都不出现。
        ``LEFT JOIN readable_entity_mentions`` 则相反——没有那条提及也要出这一行，只是
        ``mention_confidence`` 是 ``None``。

        **一个状态过滤都没有**（``e.entity_type = 'person'`` 是唯一那条）。
        端口方法的 docstring 写了这件事为什么照搬不修。

        ``ORDER BY`` 只有两个键，也是 1.0 的：同质量同置信度时取到哪一行由 SQLite 定。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT fi.id AS face_instance_id, fi.asset_id, fi.cluster_id, fi.bbox_json,
                       fi.detection_confidence, fi.quality,
                       fc.entity_id, e.canonical_name, e.family_role,
                       em.confidence AS mention_confidence
                FROM readable_face_instances fi
                JOIN readable_face_clusters fc ON fc.id = fi.cluster_id
                JOIN readable_entities e ON e.id = fc.entity_id
                LEFT JOIN readable_entity_mentions em
                  ON em.face_instance_id = fi.id
                 AND em.entity_id = fc.entity_id
                WHERE fi.asset_id = ?
                  AND e.entity_type = 'person'
                ORDER BY fi.quality DESC, fi.detection_confidence DESC
                """,
                (asset_id,),
            ).fetchall()
        return [
            PhotoIdentityRecord(
                face_instance_id=str(row["face_instance_id"]),
                asset_id=str(row["asset_id"]),
                cluster_id=str(row["cluster_id"] or ""),
                entity_id=str(row["entity_id"] or ""),
                person_name=str(row["canonical_name"] or ""),
                family_role=str(row["family_role"] or ""),
                bbox=_vector(row["bbox_json"]),
                detection_confidence=row["detection_confidence"],
                quality=row["quality"],
                mention_confidence=row["mention_confidence"],
            )
            for row in rows
        ]

    def find_instance(self, cluster_id: str, instance_id: str) -> FaceInstanceRecord | None:
        rows = self._instances_where("fi.cluster_id = ? AND fi.id = ?", (cluster_id, instance_id))
        return rows[0] if rows else None

    def _instances_where(self, clause: str, params: tuple[object, ...]) -> Sequence[FaceInstanceRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM readable_face_instances fi"
                " JOIN readable_assets a ON a.id = fi.asset_id"
                f" WHERE {clause} ORDER BY fi.quality DESC, fi.created_at ASC",
                params,
            ).fetchall()
        return [_row_to_instance(row) for row in rows]

    def person_candidate_features(self, scope_id: str) -> Sequence[dict[str, Any]]:
        """1.0 ``db.py:6612-6647``，逐字。"""
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    c.id AS cluster_id,
                    COALESCE(c.entity_id, c.id) AS person_id,
                    c.member_count AS member_count,
                    COUNT(DISTINCT substr(o.captured_at, 1, 10)) AS date_count,
                    COUNT(DISTINCT eo.event_id) AS event_count,
                    (
                        SELECT COUNT(DISTINCT ep.person_id)
                        FROM readable_event_participants ep
                        WHERE ep.event_id IN (
                            SELECT DISTINCT eo2.event_id
                            FROM readable_event_observations eo2
                            JOIN readable_face_instances fi2 ON fi2.observation_id = eo2.observation_id
                            WHERE fi2.cluster_id = c.id
                        )
                    ) AS co_person_count,
                    COUNT(DISTINCT COALESCE(e.place, '') || '|' || COALESCE(e.activity, ''))
                        AS scene_count,
                    (
                        SELECT AVG(quality) FROM (
                            SELECT fi3.quality AS quality
                            FROM readable_face_instances fi3
                            WHERE fi3.cluster_id = c.id
                            ORDER BY fi3.quality DESC
                            LIMIT 3
                        )
                    ) AS quality,
                    (
                        SELECT fi_best.id
                        FROM readable_face_instances fi_best
                        WHERE fi_best.cluster_id = c.id
                        ORDER BY fi_best.quality DESC,
                                 fi_best.detection_confidence DESC,
                                 fi_best.created_at ASC
                        LIMIT 1
                    ) AS avatar_face_instance_id,
                    CASE WHEN ent.status = 'confirmed' THEN 1 ELSE 0 END AS confirmed
                FROM readable_face_clusters c
                LEFT JOIN readable_face_instances fi ON fi.cluster_id = c.id
                LEFT JOIN readable_observations o ON o.id = fi.observation_id
                LEFT JOIN readable_event_observations eo ON eo.observation_id = o.id
                LEFT JOIN readable_events e ON e.id = eo.event_id
                LEFT JOIN readable_entities ent ON ent.id = c.entity_id
                WHERE c.scope_id = ? AND c.status != 'rejected'
                GROUP BY c.id""",
                (scope_id,),
            ).fetchall()
        return [
            {
                "person_id": row["person_id"],
                "cluster_id": row["cluster_id"],
                "date_count": int(row["date_count"] or 0),
                "event_count": int(row["event_count"] or 0),
                "member_count": int(row["member_count"] or 0),
                "co_person_count": int(row["co_person_count"] or 0),
                "scene_count": int(row["scene_count"] or 0),
                "quality": float(row["quality"] or 0),
                "avatar_face_instance_id": row["avatar_face_instance_id"],
                "confirmed": bool(row["confirmed"]),
            }
            for row in rows
        ]

    def representative_candidates(self, person_id: str) -> Sequence[dict[str, Any]]:
        """1.0 ``person_insights.py:262-270``，逐字。"""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT fi.id AS face_instance_id, fi.asset_id, fi.quality,
                    COALESCE(fi.area_ratio, 0) AS body_visibility,
                    o.captured_at, eo.event_id, fi.cluster_id, c.entity_id AS person_id
                FROM readable_face_instances fi
                JOIN readable_face_clusters c ON c.id = fi.cluster_id
                JOIN readable_observations o ON o.id = fi.observation_id
                LEFT JOIN readable_event_observations eo ON eo.observation_id = o.id
                WHERE c.entity_id = ? AND c.status != 'rejected'""",
                (person_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def best_instance_for_person_in_event(self, person_id: str, event_id: str) -> FaceInstanceRecord | None:
        """1.0 ``app.py:2026-2028``。两个 JOIN 都省不掉：``entity_mentions`` 决定
        「这张脸算不算他的」，``event_observations`` 决定「这张脸在不在这个事件里」。
        少了后者，每个事件取到的都会是全局最好的那张——一个人只会在自己最常出现的
        那个事件里有外观证据。
        """
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM readable_face_instances fi"
                " JOIN readable_assets a ON a.id = fi.asset_id"
                " JOIN readable_entity_mentions em ON em.face_instance_id = fi.id"
                " JOIN readable_event_observations eo ON eo.observation_id = fi.observation_id"
                " WHERE em.entity_id = ? AND eo.event_id = ?"
                " ORDER BY fi.quality DESC, fi.detection_confidence DESC LIMIT 1",
                (person_id, event_id),
            ).fetchone()
        return _row_to_instance(row) if row else None

    def get_instance(self, instance_id: str) -> FaceInstanceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM readable_face_instances fi"
                " JOIN readable_assets a ON a.id = fi.asset_id WHERE fi.id = ?",
                (instance_id,),
            ).fetchone()
        return _row_to_instance(row) if row else None

    # ---- 全局重聚类（Task 84）要的三组读写 ----------------------------------------

    def list_verified_instances(
        self, *, scope_id: str | None, minimum_quality: float
    ) -> Sequence[FaceInstanceRecord]:
        clause = (
            "fi.embedding_json != '[]' AND fi.cluster_id IS NOT NULL AND fi.quality >= ? AND fi.validity = ?"
        )
        params: list[object] = [float(minimum_quality), FACE_VALIDITY_VERIFIED]
        if scope_id:
            clause += " AND a.scope_id = ?"
            params.append(scope_id)
        return self._instances_where(clause, tuple(params))

    def assign_instances(self, instance_ids: Sequence[str], cluster_id: str) -> int:
        ids = list(dict.fromkeys(instance_ids))
        if not ids:
            return 0
        with self._database.connect() as connection, connection:
            connection.executemany(
                "UPDATE face_instances SET cluster_id = ? WHERE id = ?",
                [(cluster_id, instance_id) for instance_id in ids],
            )
        return len(ids)

    def set_cluster_state(self, cluster_id: str, *, status: str, member_count: int) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE face_clusters SET member_count = ?, status = ?, revision = revision + 1,"
                " updated_at = ? WHERE id = ?",
                (int(member_count), status, datetime.now(UTC).isoformat(), cluster_id),
            )

    def list_uncertain_unclustered(self, *, scope_id: str | None) -> Sequence[FaceInstanceRecord]:
        clause = "fi.validity = ? AND fi.embedding_json != '[]' AND fi.cluster_id IS NULL"
        params: list[object] = [FACE_VALIDITY_UNCERTAIN]
        if scope_id:
            clause += " AND a.scope_id = ?"
            params.append(scope_id)
        return self._instances_where(clause, tuple(params))

    def list_confirmed_clusters(self, scope_id: str, *, minimum_members: int) -> Sequence[FaceClusterRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM readable_face_clusters fc"
                " LEFT JOIN readable_entities e ON e.id = fc.entity_id"
                " WHERE fc.scope_id = ? AND fc.status = ? AND fc.member_count >= ?"
                " ORDER BY fc.member_count DESC, fc.updated_at DESC",
                (scope_id, CLUSTER_STATUS_CONFIRMED, int(minimum_members)),
            ).fetchall()
        return [_row_to_cluster(row) for row in rows]

    def list_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_PROTOTYPE_COLUMNS} FROM readable_face_prototypes WHERE cluster_id = ?"
                " ORDER BY quality DESC, pose_bucket ASC",
                (cluster_id,),
            ).fetchall()
        return [_row_to_prototype(row) for row in rows]

    def refresh_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        # After a deletion, only a fresh transactional projection may replace
        # reconciled prototypes/counts. Never accept an old captured embedding.
        with self._database.connect() as check:
            state = check.execute(
                "SELECT last_deletion_id FROM media_object_states WHERE object_table='face_clusters' "
                "AND object_key=json_object('id',?)",
                (cluster_id,),
            ).fetchone()
        if state is not None:
            from sentrix.infrastructure.persistence.reconciliation_support import rebuild_cluster

            with self._database.write_transaction() as connection, self._database.reconciliation(connection):
                rebuild_cluster(connection, cluster_id, state["last_deletion_id"])
            return self.list_prototypes(cluster_id)
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            # ``quality DESC, created_at ASC`` 两个键都在用：第一个选最清晰的，第二个在
            # 同质量时选先到的那张——少第二个键，桶内容会随插入顺序漂移。
            rows = connection.execute(
                "SELECT id, pose_bucket, embedding_json, quality, embedding_model, embedding_version"
                " FROM readable_face_instances WHERE cluster_id = ? ORDER BY quality DESC, created_at ASC",
                (cluster_id,),
            ).fetchall()
            best: dict[str, sqlite3.Row] = {}
            for row in rows:
                best.setdefault(row["pose_bucket"] or "unknown", row)
            connection.execute("DELETE FROM face_prototypes WHERE cluster_id = ?", (cluster_id,))
            for bucket, row in best.items():
                connection.execute(
                    "INSERT INTO face_prototypes"
                    " (id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality,"
                    "  model_name, model_version, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        _new_id("prototype"),
                        cluster_id,
                        row["id"],
                        bucket,
                        row["embedding_json"],
                        float(row["quality"] or 0),
                        row["embedding_model"] or "unknown",
                        row["embedding_version"] or "unknown",
                        now,
                        now,
                    ),
                )
            representative = max(best.values(), key=lambda item: float(item["quality"] or 0), default=None)
            connection.execute(
                "UPDATE face_clusters SET representative_embedding_json = ?, member_count = ?,"
                " confidence = MAX(confidence, ?), revision = revision + 1, updated_at = ?"
                " WHERE id = ?",
                (
                    representative["embedding_json"] if representative else "[]",
                    len(rows),
                    float(representative["quality"] or 0) if representative else 0.0,
                    now,
                    cluster_id,
                ),
            )
        return self.list_prototypes(cluster_id)
