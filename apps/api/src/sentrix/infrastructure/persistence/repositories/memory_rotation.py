"""回忆序列与已看记录的 SQLite 仓储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sentrix.domain.memories.rotation import RotationSequence
from sentrix.infrastructure.persistence.database import Database


class SqliteRotationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def sequence_for(self, scope_id: str, day: str) -> RotationSequence | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM readable_memory_rotation_sequences WHERE scope_id = ? AND day = ?",
                (scope_id, day),
            ).fetchone()
        return _sequence(row) if row is not None else None

    def create_sequence(
        self, scope_id: str, day: str, event_ids: tuple[str, ...], *, now: datetime
    ) -> RotationSequence:
        sequence = RotationSequence(
            sequence_id=f"rot_{uuid.uuid4().hex[:16]}",
            scope_id=scope_id,
            day=day,
            event_ids=event_ids,
            position=0,
        )
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_rotation_sequences"
                " (sequence_id, scope_id, day, event_ids_json, position, created_at, updated_at)"
                " VALUES (?,?,?,?,0,?,?)",
                (
                    sequence.sequence_id,
                    scope_id,
                    day,
                    json.dumps(list(event_ids), ensure_ascii=False),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        stored = self.sequence_for(scope_id, day)
        return stored if stored is not None else sequence

    def advance(self, sequence_id: str, position: int, *, now: datetime) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE memory_rotation_sequences SET position = ?, updated_at = ? WHERE sequence_id = ?",
                (max(0, position), now.isoformat(), sequence_id),
            )

    def replace_sequence(self, sequence_id: str, event_ids: tuple[str, ...], *, now: datetime) -> None:
        """同一天的新一轮：保留 sequence_id，换候选并回到第一页。"""
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE memory_rotation_sequences SET event_ids_json = ?, position = 0,"
                " updated_at = ? WHERE sequence_id = ?",
                (json.dumps(list(event_ids), ensure_ascii=False), now.isoformat(), sequence_id),
            )

    def recently_viewed(self, scope_id: str, *, limit: int) -> frozenset[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT v.event_id FROM memory_rotation_viewed v"
                " JOIN readable_memory_rotation_sequences s ON s.sequence_id = v.sequence_id"
                " WHERE s.scope_id = ? ORDER BY v.viewed_at DESC LIMIT ?",
                (scope_id, max(0, limit)),
            ).fetchall()
        return frozenset(str(row["event_id"]) for row in rows)

    def mark_viewed(self, sequence_id: str, event_id: str, *, now: datetime) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_rotation_viewed"
                " (sequence_id, event_id, viewed_at) VALUES (?,?,?)",
                (sequence_id, event_id, now.isoformat()),
            )


def _sequence(row) -> RotationSequence:
    return RotationSequence(
        sequence_id=str(row["sequence_id"]),
        scope_id=str(row["scope_id"]),
        day=str(row["day"]),
        event_ids=tuple(json.loads(row["event_ids_json"] or "[]")),
        position=int(row["position"]),
    )
