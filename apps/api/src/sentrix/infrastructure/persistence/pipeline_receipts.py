"""Persist the actual pipeline response before the HTTP connection returns."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sentrix.infrastructure.persistence.database import Database


class SqlitePipelineReceipts:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_receipts WHERE operation_id=?", (operation_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["asset_ids"] = json.loads(result.pop("asset_ids_json"))
        value = result.pop("result_json")
        result["result"] = json.loads(value) if value is not None else None
        return result

    def create(self, operation_id: str, scope_id: str, asset_ids: list[str], kind: str) -> None:
        try:
            with self._database.connect() as connection:
                connection.execute(
                    "INSERT INTO pipeline_receipts "
                    "(operation_id,scope_id,asset_ids_json,kind,status,started_at) VALUES (?,?,?,?,?,?)",
                    (
                        operation_id,
                        scope_id,
                        json.dumps(asset_ids),
                        kind,
                        "running",
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("pipeline operation already registered; query its receipt") from error

    def finish(self, operation_id: str, result: Mapping[str, Any] | None, error: str | None) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE pipeline_receipts SET status=?,result_json=?,error=?,finished_at=? "
                "WHERE operation_id=?",
                (
                    "failed" if error else "completed",
                    json.dumps(result) if result is not None else None,
                    error,
                    datetime.now(UTC).isoformat(),
                    operation_id,
                ),
            )
            connection.commit()
