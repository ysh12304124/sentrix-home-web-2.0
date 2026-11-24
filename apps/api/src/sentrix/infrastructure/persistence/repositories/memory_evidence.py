"""字段依据的 SQLite 实现。

``memory_evidence`` 的 owner 是多态的：SQLite 无法给 ``(owner_kind, owner_id)`` 建
外键。所以这里**显式**校验 owner 的 scope 与 build 归属；校验失败就拒绝写入，而不是
把一条跨 scope 的证据留在库里。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.media.models import MemoryEvidence
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, owner_kind, owner_id, field_path, role, value_json, value_hash,"
    " source_kind, source_modality, source_channel, locator_json, observation_revision,"
    " build_id, correction_id, status, created_at, updated_at"
)
_OWNER_SCOPE_SQL = {
    "asset": "SELECT scope_id FROM assets WHERE id = ?",
    "observation": "SELECT scope_id FROM observations WHERE id = ?",
    "event": "SELECT scope_id FROM events WHERE id = ?",
    "entity": "SELECT scope_id FROM entities WHERE id = ?",
    "face_instance": (
        "SELECT a.scope_id FROM face_instances f JOIN assets a ON a.id = f.asset_id WHERE f.id = ?"
    ),
}
_OWNER_ASSET_SQL = {
    "asset": "SELECT id AS asset_id FROM assets WHERE id = ?",
    "observation": "SELECT asset_id FROM observations WHERE id = ?",
    "face_instance": "SELECT asset_id FROM face_instances WHERE id = ?",
}


def _new_id() -> str:
    return f"ev_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _load_json(value: object, default: object) -> object:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _row_to_evidence(row: sqlite3.Row) -> MemoryEvidence:
    locator = _load_json(row["locator_json"], {})
    return MemoryEvidence(
        id=str(row["id"]),
        scope_id=str(row["scope_id"]),
        owner_kind=str(row["owner_kind"]),
        owner_id=str(row["owner_id"]),
        field_path=str(row["field_path"]),
        role=str(row["role"]),
        value=_load_json(row["value_json"], None),
        value_hash=str(row["value_hash"]),
        source_kind=str(row["source_kind"]),
        source_modality=str(row["source_modality"]),
        source_channel=str(row["source_channel"]),
        locator=locator if isinstance(locator, dict) else {},
        observation_revision=int(row["observation_revision"]),
        build_id=str(row["build_id"]) if row["build_id"] else "",
        correction_id=str(row["correction_id"]),
        status=str(row["status"]),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


class SqliteMemoryEvidenceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, evidence: MemoryEvidence) -> MemoryEvidence:
        now = _now()
        with self._database.connect() as connection, connection:
            scope_row = connection.execute(
                _OWNER_SCOPE_SQL[evidence.owner_kind], (evidence.owner_id,)
            ).fetchone()
            if scope_row is None:
                raise ValueError("evidence owner not found")
            if str(scope_row["scope_id"]) != evidence.scope_id:
                raise ValueError("evidence owner scope mismatch")

            build_asset_id: str | None = None
            if evidence.build_id:
                build_row = connection.execute(
                    "SELECT asset_id, scope_id FROM observation_builds WHERE id = ?",
                    (evidence.build_id,),
                ).fetchone()
                if build_row is None:
                    raise ValueError("evidence build not found")
                if str(build_row["scope_id"]) != evidence.scope_id:
                    raise ValueError("evidence build scope mismatch")
                build_asset_id = str(build_row["asset_id"])
                owner_asset_sql = _OWNER_ASSET_SQL.get(evidence.owner_kind)
                if owner_asset_sql is not None:
                    owner_asset = connection.execute(owner_asset_sql, (evidence.owner_id,)).fetchone()
                    if owner_asset is None or str(owner_asset["asset_id"]) != build_asset_id:
                        raise ValueError("evidence build belongs to a different asset")

            evidence_id = evidence.id or _new_id()
            connection.execute(
                "INSERT INTO memory_evidence("
                " id, scope_id, owner_kind, owner_id, field_path, role, value_json, value_hash,"
                " source_kind, source_modality, source_channel, locator_json, observation_revision,"
                " build_id, correction_id, status, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    evidence.scope_id,
                    evidence.owner_kind,
                    evidence.owner_id,
                    evidence.field_path,
                    evidence.role,
                    json.dumps(evidence.value, ensure_ascii=False, sort_keys=True),
                    evidence.value_hash,
                    evidence.source_kind,
                    evidence.source_modality,
                    evidence.source_channel,
                    json.dumps(evidence.locator, ensure_ascii=False, sort_keys=True),
                    evidence.observation_revision,
                    evidence.build_id or None,
                    evidence.correction_id,
                    evidence.status,
                    evidence.created_at.isoformat() if evidence.created_at else now,
                    now,
                ),
            )
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        assert row is not None, "刚写入的字段依据读不回来"
        return _row_to_evidence(row)

    def list_for_owner(
        self,
        *,
        scope_id: str,
        owner_kind: str,
        owner_id: str,
        field_path: str = "",
        statuses: Sequence[str] = (),
    ) -> Sequence[MemoryEvidence]:
        clauses = ["scope_id = ?", "owner_kind = ?", "owner_id = ?"]
        params: list[object] = [scope_id, owner_kind, owner_id]
        if field_path:
            clauses.append("field_path = ?")
            params.append(field_path)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_evidence WHERE {' AND '.join(clauses)}"
                " ORDER BY created_at, id",
                tuple(params),
            ).fetchall()
        return tuple(_row_to_evidence(row) for row in rows)

    def list_by_build(self, build_id: str) -> Sequence[MemoryEvidence]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_evidence WHERE build_id = ? ORDER BY created_at, id",
                (build_id,),
            ).fetchall()
        return tuple(_row_to_evidence(row) for row in rows)
