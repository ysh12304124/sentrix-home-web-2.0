"""``asset_places`` 仓储。SQL 只出现在这一层。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "asset_id, admin_province, admin_city, admin_district, nearest_township,"
    " nearest_locality, nearest_distance_m, precision, source, confidence,"
    " gps_latitude, gps_longitude, nearest_evidence_json"
)


def _row_to_place(row: sqlite3.Row) -> Place:
    latitude, longitude = row["gps_latitude"], row["gps_longitude"]
    return Place(
        admin=AdminArea(
            province=row["admin_province"], city=row["admin_city"], district=row["admin_district"]
        ),
        nearest=NearestPlace(
            township=row["nearest_township"],
            locality=row["nearest_locality"],
            distance_m=row["nearest_distance_m"],
            **_nearest_evidence(row["nearest_evidence_json"]),
        ),
        precision=PlacePrecision(row["precision"]),
        source=PlaceSource(row["source"]),
        gps=(GpsPoint(latitude, longitude) if latitude is not None and longitude is not None else None),
        confidence=row["confidence"],
    )


class SqliteAssetPlaceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, *, asset_id: str, scope_id: str, gps: GpsPoint | None, place: Place) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO asset_places (asset_id, scope_id, admin_province, admin_city,"
                " admin_district, nearest_township, nearest_locality, nearest_distance_m,"
                " precision, source, confidence, gps_latitude, gps_longitude,"
                " resolved_at, nearest_evidence_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(asset_id) DO UPDATE SET"
                " scope_id = excluded.scope_id,"
                " admin_province = excluded.admin_province,"
                " admin_city = excluded.admin_city,"
                " admin_district = excluded.admin_district,"
                " nearest_township = excluded.nearest_township,"
                " nearest_locality = excluded.nearest_locality,"
                " nearest_distance_m = excluded.nearest_distance_m,"
                " precision = excluded.precision,"
                " source = excluded.source,"
                " confidence = excluded.confidence,"
                " gps_latitude = excluded.gps_latitude,"
                " gps_longitude = excluded.gps_longitude,"
                " resolved_at = excluded.resolved_at,"
                " nearest_evidence_json = excluded.nearest_evidence_json",
                (
                    asset_id,
                    scope_id,
                    place.admin.province,
                    place.admin.city,
                    place.admin.district,
                    place.nearest.township,
                    place.nearest.locality,
                    place.nearest.distance_m,
                    place.precision.value,
                    place.source.value,
                    place.confidence,
                    gps.latitude if gps else None,
                    gps.longitude if gps else None,
                    datetime.now(UTC).isoformat(),
                    json.dumps(
                        {
                            k: v
                            for k, v in asdict(place.nearest).items()
                            if k not in {"township", "locality", "distance_m"}
                        }
                    ),
                ),
            )

    def get(self, asset_id: str) -> Place | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM asset_places WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return _row_to_place(row) if row else None

    def get_many(self, asset_ids: Sequence[str]) -> dict[str, Place]:
        if not asset_ids:
            return {}
        placeholders = ",".join("?" for _ in asset_ids)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT asset_id, {_COLUMNS} FROM asset_places WHERE asset_id IN ({placeholders})",
                tuple(asset_ids),
            ).fetchall()
        return {row["asset_id"]: _row_to_place(row) for row in rows}

    def list_scope(self, scope_id: str, *, limit: int) -> dict[str, Place]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT asset_id, {_COLUMNS} FROM asset_places WHERE scope_id = ? LIMIT ?",
                (scope_id, limit),
            ).fetchall()
        return {row["asset_id"]: _row_to_place(row) for row in rows}

    def count(self, scope_id: str | None = None) -> int:
        with self._database.connect() as connection:
            if scope_id:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM asset_places WHERE scope_id = ?", (scope_id,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS n FROM asset_places").fetchone()
        return int(row["n"]) if row else 0


def _nearest_evidence(text: str) -> dict[str, Any]:
    data = json.loads(text)
    for key in ("township_admin_codes", "locality_admin_codes"):
        if key in data:
            data[key] = tuple(data[key])
    return cast(dict[str, Any], data)
