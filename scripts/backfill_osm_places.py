"""Refresh asset and GPS-derived timeline places from saved GPS."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

from sentrix.domain.places.models import Place, PlaceSource
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.places.osm import OsmPlaceResolver
from sentrix.infrastructure.places.pygeocn import PyGeoCNAdminResolver


def refresh_event_places(
    connection: sqlite3.Connection,
    resolved: dict[str, Place],
    previous: dict[str, Place],
    *,
    apply: bool,
) -> int:
    events = connection.execute(
        "SELECT e.id, e.place, e.admin_district, e.scope_id, e.source_asset_id, o.asset_id "
        "FROM events e LEFT JOIN event_observations eo ON eo.event_id=e.id "
        "LEFT JOIN observations o ON o.id=eo.observation_id AND o.scope_id=e.scope_id "
        "WHERE e.status='active' AND NOT EXISTS (SELECT 1 FROM memory_corrections c "
        "WHERE c.scope_id=e.scope_id AND c.target_kind='event' AND c.target_id=e.id) "
        "ORDER BY e.id"
    )
    changes = []
    for event_id, rows in groupby(events, key=lambda row: row["id"]):
        evidence = list(rows)
        event = evidence[0]
        asset_ids = {
            asset_id for row in evidence for asset_id in (row["asset_id"], row["source_asset_id"]) if asset_id
        }
        if not asset_ids or not asset_ids <= resolved.keys():
            continue
        labels = {(resolved[key].display(), resolved[key].admin.district) for key in asset_ids}
        if len(labels) != 1:
            continue
        label, district = labels.pop()
        old_label = (event["place"], event["admin_district"])
        if old_label == (label, district):
            continue
        # Keep places from visual descriptions or edits. Refresh only labels that
        # match a saved GPS place or its administrative-only representation.
        old_places = [previous[key] for key in asset_ids if key in previous]
        if old_label[0] and any(
            old_label
            in ((place.display(), place.admin.district), (place.admin.label(), place.admin.district))
            for place in (*old_places, *(resolved[key] for key in asset_ids))
        ):
            changes.append((label, district, datetime.now(UTC).isoformat(), event_id, *old_label))
    if apply:
        return connection.executemany(
            "UPDATE events SET place=?, admin_district=?, updated_at=?, revision=revision+1 "
            "WHERE id=? AND place=? AND admin_district=? AND status='active' "
            "AND NOT EXISTS (SELECT 1 FROM memory_corrections c WHERE c.scope_id=events.scope_id "
            "AND c.target_kind='event' AND c.target_id=events.id)",
            changes,
        ).rowcount
    return len(changes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--scope")
    selection.add_argument("--all", action="store_true", help="Refresh all scopes in the selected database")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--changed-only", action="store_true", help="Skip map-version-only changes")
    parser.add_argument("--backup", type=Path, help="New SQLite backup path required with --apply")
    args = parser.parse_args()
    if not args.db.is_file() or not args.index.is_file():
        parser.error("database and compiled OSM index must exist")
    if args.apply and (args.backup is None or args.backup.exists()):
        parser.error("--apply requires a new --backup path")
    database = Database(args.db, Path())
    assets = SqliteAssetRepository(database)
    places = SqliteAssetPlaceRepository(database)
    observations = SqliteObservationRepository(database)
    lexical = SqliteLexicalIndex(database, observations, assets=assets, places=places)
    resolver = OsmPlaceResolver(args.index, PyGeoCNAdminResolver())
    with database.connect() as conn:
        query = "SELECT id FROM assets WHERE media_type IN ('image','video')"
        if args.all:
            ids = [row[0] for row in conn.execute(query + " ORDER BY id")]
        else:
            ids = [row[0] for row in conn.execute(query + " AND scope_id=? ORDER BY id", (args.scope,))]
        if not ids:
            raise ValueError("scope contains no image or video assets")
        if args.apply:
            args.backup.parent.mkdir(parents=True, exist_ok=True)
            args.backup.touch(exist_ok=False)
            with closing(sqlite3.connect(args.backup)) as backup:
                conn.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("backup failed SQLite integrity check")
    counts: Counter[str] = Counter()
    resolved: dict[str, Place] = {}
    previous: dict[str, Place] = {}
    for asset_id in ids:
        asset = assets.get(asset_id)
        if asset is None or asset.captured_location is None:
            counts["missing_gps"] += 1
            continue
        place = resolver.resolve(asset.captured_location)
        if place is None or place.source is not PlaceSource.OSM:
            counts["no_osm_match"] += 1
            continue
        counts["osm"] += 1
        resolved[asset_id] = place
        old_place = places.get(asset_id)
        if old_place is not None:
            previous[asset_id] = old_place
            if (
                args.changed_only
                and old_place != place
                and old_place == replace(place, map_version=old_place.map_version)
            ):
                counts["version_only"] += 1
                continue
        if old_place == place:
            counts["unchanged"] += 1
        else:
            counts["changed"] += 1
        if args.apply:
            if old_place != place:
                places.save(
                    asset_id=asset.id, scope_id=asset.scope_id, gps=asset.captured_location, place=place
                )
                counts["updated"] += 1
            for observation in observations.list_by_asset(asset.id):
                lexical.refresh_from_observation(observation)
    with database.connect() as conn, conn:
        counts["event_places"] = refresh_event_places(conn, resolved, previous, apply=args.apply)
    print(json.dumps({"scope": args.scope or "all", "assets": len(ids), "counts": counts}))


if __name__ == "__main__":
    main()
