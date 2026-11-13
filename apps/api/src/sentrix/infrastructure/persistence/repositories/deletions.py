"""Durable isolation requests. No worker, resource removal, or memory rebuild."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion
from sentrix.infrastructure.persistence.database import Database


class SqliteDeletionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def request(
        self, asset_id: str, scope_id: str, key: str | None = None, *, library: bool = False
    ) -> dict[str, object]:
        with self._database.connect() as connection, connection:
            # Serializes overlapping requests and child inserts across processes.
            connection.execute("BEGIN IMMEDIATE")
            root = connection.execute(
                "SELECT * FROM assets WHERE id=? AND scope_id=?", (asset_id, scope_id)
            ).fetchone()
            if root is None:
                previous = connection.execute(
                    "SELECT * FROM media_deletions WHERE root_asset_id=? AND scope_id=?",
                    (asset_id, scope_id),
                ).fetchone()
                if previous is not None:
                    if key:
                        other = connection.execute(
                            "SELECT root_asset_id FROM media_deletions "
                            "WHERE scope_id=? AND idempotency_key=?",
                            (scope_id, key),
                        ).fetchone()
                        if other and other[0] != asset_id:
                            raise InvalidDeletion("idempotency key belongs to another asset")
                    return self._accept(connection, previous, library)
                raise DeletionNotFound("asset not found in this scope")
            if root["parent_asset_id"] is not None or root["derived_kind"] is not None:
                raise InvalidDeletion("delete the source video, not an individual derived asset")
            if root["media_type"] not in {"image", "video"}:
                raise InvalidDeletion("only images and videos support isolation")
            if key:
                previous = connection.execute(
                    "SELECT * FROM media_deletions WHERE scope_id=? AND idempotency_key=?",
                    (scope_id, key),
                ).fetchone()
                if previous is not None and previous["root_asset_id"] != asset_id:
                    raise InvalidDeletion("idempotency key already belongs to another asset")
            previous = connection.execute(
                "SELECT * FROM media_deletions WHERE root_asset_id=?", (asset_id,)
            ).fetchone()
            if previous is not None:
                return self._accept(connection, previous, library)
            if root["media_type"] == "video":
                members = connection.execute(
                    "WITH RECURSIVE descendants(id) AS (SELECT id FROM assets WHERE id=? "
                    "UNION SELECT a.id FROM assets a JOIN descendants d ON a.parent_asset_id=d.id) "
                    "SELECT a.id, a.scope_id, a.deletion_generation FROM assets a "
                    "JOIN descendants d ON d.id=a.id",
                    (asset_id,),
                ).fetchall()
            else:
                members = [root]
            if any(row["scope_id"] != scope_id for row in members):
                raise InvalidDeletion("cross-scope asset lineage must be repaired before deletion")
            deletion_id = f"deletion_{uuid.uuid4().hex}"
            now = datetime.now(UTC).isoformat()
            generation = int(root["deletion_generation"]) + 1
            connection.execute(
                "INSERT INTO media_deletions (deletion_id,scope_id,root_asset_id,status,"
                "created_at,updated_at,generation,idempotency_key) VALUES (?,?,?,'isolated',?,?,?,?)",
                (deletion_id, scope_id, asset_id, now, now, generation, key),
            )
            for row in members:
                next_generation = int(row["deletion_generation"]) + 1
                connection.execute(
                    "UPDATE assets SET deletion_state='isolated', deletion_generation=? WHERE id=?",
                    (next_generation, row["id"]),
                )
                connection.execute(
                    "INSERT INTO media_deletion_members VALUES (?,?,?)",
                    (deletion_id, row["id"], next_generation),
                )
            # Fence/cancel existing workers in the same transaction as isolation.
            connection.execute(
                "UPDATE import_jobs SET status='cancelled', stage='cancelled', cancel_requested=1, "
                "fence=fence+1, lease_expires_at=NULL, updated_at=? WHERE asset_id IN "
                "(SELECT asset_id FROM media_deletion_members WHERE deletion_id=?)",
                (now, deletion_id),
            )
            connection.execute(
                "UPDATE video_jobs SET status='CANCELLED', worker_id=NULL, updated_at=? "
                "WHERE asset_id IN (SELECT asset_id FROM media_deletion_members WHERE deletion_id=?)",
                (now, deletion_id),
            )
            result = connection.execute(
                "SELECT * FROM media_deletions WHERE deletion_id=?", (deletion_id,)
            ).fetchone()
            return self._accept(connection, result, library)

    def _accept(self, connection: sqlite3.Connection, row: sqlite3.Row, library: bool) -> dict[str, object]:
        if library and row["library_state"] is None:
            connection.execute(
                "UPDATE media_deletions SET library_state='pending' WHERE deletion_id=?",
                (row["deletion_id"],),
            )
            row = connection.execute(
                "SELECT * FROM media_deletions WHERE deletion_id=?", (row["deletion_id"],)
            ).fetchone()
        return self._payload(connection, row)

    def list_library(self, scope_id: str) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT d.deletion_id,d.scope_id,d.root_asset_id,d.library_state,"
                    "l.state AS lifecycle_state "
                    "FROM media_deletions d JOIN media_deletion_lifecycle l USING(deletion_id) "
                    "WHERE (?='' OR d.scope_id=?) AND d.library_state IS NOT NULL "
                    "ORDER BY (l.state='completed'),d.created_at DESC LIMIT 50",
                    (scope_id, scope_id),
                )
            ]

    def retry_library(self, deletion_id: str, scope_id: str) -> None:
        with self._database.write_transaction() as connection:
            connection.execute(
                "UPDATE media_deletions SET library_state='pending' "
                "WHERE deletion_id=? AND scope_id=? AND library_state IN ('failed','blocked')",
                (deletion_id, scope_id),
            )

    def get(self, deletion_id: str, scope_id: str) -> dict[str, object]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?",
                (deletion_id, scope_id),
            ).fetchone()
            if row is None:
                raise DeletionNotFound("deletion task not found in this scope")
            return self._payload(connection, row)

    @staticmethod
    def _payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='media_deletion_lifecycle'").fetchone():
            lifecycle = connection.execute(
                "SELECT state FROM media_deletion_lifecycle WHERE deletion_id=?", (row["deletion_id"],)
            ).fetchone()
            result["lifecycle_state"] = lifecycle[0] if lifecycle else "isolated"
        result["asset_ids"] = [
            r[0]
            for r in connection.execute(
                "SELECT asset_id FROM media_deletion_members WHERE deletion_id=? ORDER BY asset_id",
                (row["deletion_id"],),
            )
        ]
        return result
