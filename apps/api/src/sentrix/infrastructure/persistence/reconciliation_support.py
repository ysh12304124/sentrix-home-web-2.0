"""Small transaction-bound projection helpers, shared with healthy face refreshes."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from sentrix.application.media.withdraw_evidence import encode_key


def mark_state(
    connection: sqlite3.Connection,
    table: str,
    key: dict[str, Any],
    scope: str | None,
    deletion_id: str,
    validity: str,
    reason: str,
    *,
    summary_valid: bool = False,
    metrics: dict[str, Any] | None = None,
    structural_restore: bool = False,
) -> None:
    object_key = encode_key(key)
    old = connection.execute(
        "SELECT * FROM media_object_states WHERE object_table=? AND object_key=?", (table, object_key)
    ).fetchone()
    if old is not None:
        summary_valid = summary_valid and bool(old["summary_valid"])
        rank = {"valid": 0, "needs_review": 1, "invalid": 2, "revoked": 3}
        if not structural_restore and rank[old["validity"]] > rank[validity]:
            validity = old["validity"]
    payload = json.dumps(metrics or {}, sort_keys=True, ensure_ascii=False)
    if old and (
        old["validity"],
        old["summary_valid"],
        old["reason"],
        old["metrics_json"],
        old["last_deletion_id"],
    ) == (validity, int(summary_valid), reason, payload, deletion_id):
        return
    connection.execute(
        "INSERT INTO media_object_states VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(object_table,object_key) DO UPDATE SET "
        "scope_id=excluded.scope_id,validity=excluded.validity,"
        "summary_valid=excluded.summary_valid,generation=media_object_states.generati"
        "on+1,reason=excluded.reason,"
        "metrics_json=excluded.metrics_json,last_deletion_id=excluded.last_deletion_i"
        "d,updated_at=excluded.updated_at",
        (
            table,
            object_key,
            scope,
            validity,
            int(summary_valid),
            1,
            reason,
            payload,
            deletion_id,
            datetime.now(UTC).isoformat(),
        ),
    )


def rebuild_cluster(connection: sqlite3.Connection, cluster_id: str, deletion_id: str) -> dict[str, Any]:
    cluster = connection.execute("SELECT * FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    if cluster is None:
        return {"missing_cluster": cluster_id}
    rows = connection.execute(
        (
            "SELECT * FROM readable_face_instances WHERE cluster_id=? ORDER BY quality "
            "DESC,created_at ASC,id ASC"
        ),
        (cluster_id,),
    ).fetchall()
    best: dict[str, sqlite3.Row] = {}
    for row in rows:
        best.setdefault(row["pose_bucket"] or "unknown", row)
    now = datetime.now(UTC).isoformat()
    for bucket, row in best.items():
        existing = connection.execute(
            "SELECT id FROM face_prototypes WHERE cluster_id=? AND pose_bucket=?", (cluster_id, bucket)
        ).fetchone()
        proto_id = (
            existing["id"]
            if existing
            else f"prototype_{uuid.uuid5(uuid.NAMESPACE_URL, cluster_id + ':' + bucket).hex}"
        )
        connection.execute(
            "INSERT INTO "
            "face_prototypes(id,cluster_id,face_instance_id,pose_bucket,embedding_json,qu"
            "ality,model_name,model_version,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cluster_id,pose_bucket) DO UPDATE SET "
            "face_instance_id=excluded.face_instance_id,embedding_json=excluded.embedding"
            "_json,quality=excluded.quality,"
            "model_name=excluded.model_name,model_version=excluded.model_version,updated_"
            "at=excluded.updated_at",
            (
                proto_id,
                cluster_id,
                row["id"],
                bucket,
                row["embedding_json"],
                row["quality"],
                row["embedding_model"],
                row["embedding_version"],
                now,
                now,
            ),
        )
        mark_state(
            connection,
            "face_prototypes",
            {"id": proto_id},
            cluster["scope_id"],
            deletion_id,
            "valid",
            "current_active_face",
            summary_valid=True,
            structural_restore=True,
        )
    for proto in connection.execute(
        "SELECT * FROM face_prototypes WHERE cluster_id=?", (cluster_id,)
    ).fetchall():
        if proto["pose_bucket"] not in best:
            mark_state(
                connection,
                "face_prototypes",
                {"id": proto["id"]},
                cluster["scope_id"],
                deletion_id,
                "invalid",
                "no_active_face_for_pose",
            )
    representative = rows[0] if rows else None
    connection.execute(
        (
            "UPDATE face_clusters SET "
            "member_count=?,representative_embedding_json=?,confidence=?,revision=revisio"
            "n+1,updated_at=? WHERE id=?"
        ),
        (
            len(rows),
            representative["embedding_json"] if representative else "[]",
            float(representative["quality"]) if representative else 0.0,
            now,
            cluster_id,
        ),
    )
    metrics = {
        "member_count": len(rows),
        "representative_face_id": representative["id"] if representative else None,
        "prototype_count": len(best),
        "confidence_source": "remaining_face_quality_max",
    }
    mark_state(
        connection,
        "face_clusters",
        {"id": cluster_id},
        cluster["scope_id"],
        deletion_id,
        "valid",
        "active_faces_recounted",
        summary_valid=True,
        metrics=metrics,
        structural_restore=True,
    )
    return metrics
