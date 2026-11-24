"""导出快照仓储。快照只存范围、文件摘要和冲突，不复制素材正文。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.media.models import ExportFile, MemoryExportSnapshot
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, status, schema_version, compat_mode, selection_json, manifest_json,"
    " directory, files_json, conflicts_json, counts_json, created_at, updated_at"
)


def _new_id() -> str:
    return f"mexp_{uuid.uuid4().hex[:16]}"


def _load(value: object, default: object) -> object:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _row(row: sqlite3.Row) -> MemoryExportSnapshot:
    selection = _load(row["selection_json"], {})
    manifest = _load(row["manifest_json"], {})
    files = _load(row["files_json"], [])
    conflicts = _load(row["conflicts_json"], [])
    counts = _load(row["counts_json"], {})
    return MemoryExportSnapshot(
        id=str(row["id"]),
        scope_id=str(row["scope_id"]),
        status=str(row["status"]),
        schema_version=str(row["schema_version"]),
        compat_mode=str(row["compat_mode"]),
        selection=selection if isinstance(selection, Mapping) else {},
        manifest=manifest if isinstance(manifest, Mapping) else {},
        directory=str(row["directory"]),
        files=tuple(
            ExportFile(
                name=str(item.get("name") or ""),
                sha256=str(item.get("sha256") or ""),
                size_bytes=int(item.get("size_bytes") or 0),
            )
            for item in (files if isinstance(files, list) else [])
            if isinstance(item, Mapping)
        ),
        conflicts=tuple(str(item) for item in conflicts) if isinstance(conflicts, list) else (),
        counts=counts if isinstance(counts, Mapping) else {},
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


class SqliteMemoryExportRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, snapshot: MemoryExportSnapshot) -> MemoryExportSnapshot:
        now = datetime.now(UTC).isoformat()
        snapshot_id = snapshot.id or _new_id()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO memory_export_snapshots("
                " id, scope_id, status, schema_version, compat_mode, selection_json,"
                " manifest_json, directory, files_json, conflicts_json, counts_json,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    snapshot.scope_id,
                    snapshot.status,
                    snapshot.schema_version,
                    snapshot.compat_mode,
                    json.dumps(dict(snapshot.selection), ensure_ascii=False),
                    json.dumps(dict(snapshot.manifest), ensure_ascii=False),
                    snapshot.directory,
                    json.dumps(
                        [
                            {
                                "name": item.name,
                                "sha256": item.sha256,
                                "size_bytes": item.size_bytes,
                            }
                            for item in snapshot.files
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(list(snapshot.conflicts), ensure_ascii=False),
                    json.dumps(dict(snapshot.counts), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        created = self.get(snapshot_id)
        assert created is not None
        return created

    def get(self, snapshot_id: str) -> MemoryExportSnapshot | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_export_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return _row(row) if row else None

    def list_for_scope(self, scope_id: str, *, limit: int = 50) -> Sequence[MemoryExportSnapshot]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_export_snapshots WHERE scope_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (scope_id, max(1, int(limit))),
            ).fetchall()
        return tuple(_row(row) for row in rows)
