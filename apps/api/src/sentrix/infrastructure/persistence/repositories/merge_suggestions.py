"""相似人物簇建议的候选特征与负向决策。

只供人工比较：没有自动合并，也不发布「系统判断同人」。候选规模有界（500 簇），
超过上限时由调用方显式记录被截断，不做全库两两比较。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import MergeCandidateCluster, merge_pair_key
from sentrix.infrastructure.persistence.database import Database

MAX_CANDIDATES = 500


def _new_id() -> str:
    return f"msd_{uuid.uuid4().hex[:16]}"


def _embedding(representative: object, prototype: object) -> tuple[float, ...]:
    for raw in (representative, prototype):
        try:
            values = json.loads(str(raw or "[]"))
        except json.JSONDecodeError:
            continue
        if isinstance(values, list) and values:
            try:
                return tuple(float(value) for value in values)
            except (TypeError, ValueError):
                continue
    return ()


class SqlitePersonMergeSuggestionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def features(self, scope_id: str, *, limit: int = MAX_CANDIDATES) -> Sequence[MergeCandidateCluster]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id AS cluster_id, c.entity_id, c.member_count, c.revision,
                       c.representative_embedding_json,
                       e.canonical_name, e.status AS entity_status, e.name_state,
                       (
                           SELECT fi.id FROM face_instances fi
                           WHERE fi.cluster_id = c.id
                           ORDER BY fi.quality DESC, fi.detection_confidence DESC, fi.id
                           LIMIT 1
                       ) AS avatar_face_instance_id,
                       COALESCE((
                           SELECT p.model_name FROM face_prototypes p
                           WHERE p.cluster_id = c.id ORDER BY p.quality DESC, p.id LIMIT 1
                       ), 'unknown') AS model_name,
                       COALESCE((
                           SELECT p.model_version FROM face_prototypes p
                           WHERE p.cluster_id = c.id ORDER BY p.quality DESC, p.id LIMIT 1
                       ), 'unknown') AS model_version,
                       (
                           SELECT p.embedding_json FROM face_prototypes p
                           WHERE p.cluster_id = c.id ORDER BY p.quality DESC, p.id LIMIT 1
                       ) AS prototype_embedding_json
                FROM face_clusters c
                LEFT JOIN entities e ON e.id = c.entity_id
                WHERE c.scope_id = ? AND c.status != 'rejected'
                ORDER BY c.id
                LIMIT ?
                """,
                (scope_id, max(1, int(limit))),
            ).fetchall()
        candidates: list[MergeCandidateCluster] = []
        for row in rows:
            embedding = _embedding(row["representative_embedding_json"], row["prototype_embedding_json"])
            if not embedding:
                continue
            person_id = str(row["entity_id"] or "")
            verified_name = (
                str(row["entity_status"] or "") == "confirmed" and str(row["name_state"] or "") == "confirmed"
            )
            label = (
                str(row["canonical_name"] or "").strip()
                if verified_name
                else f"待确认人物…{(person_id or str(row['cluster_id']))[-6:]}"
            )
            candidates.append(
                MergeCandidateCluster(
                    cluster_id=str(row["cluster_id"]),
                    person_id=person_id,
                    display_label=label,
                    member_count=int(row["member_count"] or 0),
                    avatar_face_instance_id=str(row["avatar_face_instance_id"] or ""),
                    embedding=embedding,
                    revision=int(row["revision"] or 0),
                    model_name=str(row["model_name"] or "unknown"),
                    model_version=str(row["model_version"] or "unknown"),
                )
            )
        return tuple(candidates)

    def decisions(self, scope_id: str) -> Mapping[str, tuple[str, str]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT left_cluster_id, right_cluster_id, decision, feature_version"
                " FROM person_merge_suggestion_decisions WHERE scope_id = ?",
                (scope_id,),
            ).fetchall()
        return {
            merge_pair_key(str(row["left_cluster_id"]), str(row["right_cluster_id"])): (
                str(row["decision"]),
                str(row["feature_version"]),
            )
            for row in rows
        }

    def record_decision(
        self,
        *,
        scope_id: str,
        left_cluster_id: str,
        right_cluster_id: str,
        decision: str,
        feature_version: str,
    ) -> None:
        if decision not in {"rejected", "accepted"}:
            raise ValueError(f"未知的合并建议决策: {decision}")
        first, second = sorted((left_cluster_id, right_cluster_id))
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO person_merge_suggestion_decisions("
                " id, scope_id, left_cluster_id, right_cluster_id, decision,"
                " feature_version, created_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(scope_id, left_cluster_id, right_cluster_id) DO UPDATE SET"
                " decision = excluded.decision, feature_version = excluded.feature_version,"
                " created_at = excluded.created_at",
                (
                    _new_id(),
                    scope_id,
                    first,
                    second,
                    decision,
                    feature_version,
                    datetime.now(UTC).isoformat(),
                ),
            )
