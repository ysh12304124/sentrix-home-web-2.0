"""Cross-store write serialization against the authoritative deletion transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from sentrix.domain.media.deletion import MediaIsolated
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database


def require_active(connection: sqlite3.Connection, asset_id: str, generation: int = 0) -> None:
    if connection.execute(
        "SELECT 1 FROM media_tombstones WHERE kind='asset' AND object_id=?", (asset_id,)
    ).fetchone():
        raise MediaIsolated("asset identity is permanently tombstoned")
    row = connection.execute(
        "SELECT deletion_state, deletion_generation FROM assets WHERE id=?", (asset_id,)
    ).fetchone()
    if row is None or row["deletion_state"] != "active":
        raise MediaIsolated("asset is missing or isolated")
    if row["deletion_generation"] != generation:
        raise MediaIsolated("stale asset generation")


@contextmanager
def vector_write_barrier(database: Database, records: Sequence[VectorRecord]) -> Iterator[None]:
    # BEGIN IMMEDIATE also excludes deletion in other processes until Qdrant upsert
    # has finished. This is not a distributed transaction: pre-existing points stay.
    with database.connect() as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        for record in records:
            if record.source_type == "asset":
                asset_id = record.source_id
            elif record.source_type in {"observation", "face_instance"}:
                table = "observations" if record.source_type == "observation" else "face_instances"
                row = connection.execute(
                    f"SELECT asset_id FROM {table} WHERE id=?", (record.source_id,)
                ).fetchone()
                if row is None:
                    raise MediaIsolated("vector source no longer exists")
                asset_id = row[0]
            else:
                raise MediaIsolated("unsupported external vector source")
            require_active(connection, asset_id)
        yield


def publish_media_file(
    database: Database, asset_id: str, temporary: Path, target: Path, *, allow_unregistered: bool = False
) -> None:
    """Publish a prepared artifact only while deletion is excluded.

    Expensive inference/ffmpeg writes to a non-delivered temporary path. Rejected
    temporary artifacts are retained for later cleanup; originals are untouched.
    """
    import os

    with database.connect() as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone()
        tombstoned = connection.execute(
            "SELECT 1 FROM media_tombstones WHERE kind='asset' AND object_id=?", (asset_id,)
        ).fetchone()
        if tombstoned or exists or not allow_unregistered:
            require_active(connection, asset_id)
        os.replace(temporary, target)
