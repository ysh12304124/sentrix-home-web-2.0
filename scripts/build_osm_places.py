"""Compile a regional OSM PBF into the read-only Sentrix places index."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

import osmium
import shapely
from osmium.osm import Area, Node, TagList, Way

_ROADS = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "pedestrian",
    "service",
    "track",
}
_SETTLEMENTS = {"town", "village", "hamlet", "suburb", "neighbourhood", "quarter"}
_ADMIN = {4: "province", 5: "city", 6: "district", 8: "township", 10: "village"}
_VENUES = {"cinema", "cafe", "school", "college", "university"}
_RESORTS = {"resort", "holiday_resort", "hotel", "guest_house", "chalet"}
_MAP_SCHEMA = 4


_Object = TypeVar("_Object", Node, Way, Area)


def poi(tags: TagList) -> bool:
    return (
        tags.get("tourism") in {"attraction", "museum", "theme_park", "zoo", "gallery", "viewpoint"}
        or tags.get("leisure") in {"park", "nature_reserve"}
        or tags.get("historic") not in {None, "no", "yes"}
        or tags.get("boundary") in {"national_park", "protected_area"}
        or tags.get("amenity") in _VENUES
        or tags.get("tourism") in _RESORTS
        or tags.get("leisure") == "resort"
        or tags.get("landuse") == "education"
    )


class Collector(osmium.SimpleHandler):
    def __init__(self, db: sqlite3.Connection) -> None:
        super().__init__()
        self.db = db
        self.geometry = osmium.geom.WKBFactory()
        self.incomplete = 0

    def save(self, obj: _Object, kind: str, oid: str, make_geometry: Callable[[_Object], str]) -> None:
        name = (obj.tags.get("name:zh-Hans") or obj.tags.get("name:zh") or obj.tags.get("name") or "").strip()
        refs = (
            tuple(
                dict.fromkeys(
                    "".join(ref.split()).upper()
                    for ref in (obj.tags.get("ref") or "").split(";")
                    if ref.strip()
                )
            )
            if kind == "road"
            else ()
        )
        if not name:
            return
        aliases = json.dumps([ref for ref in refs if ref != name], ensure_ascii=False)
        try:
            geometry = shapely.from_wkb(bytes.fromhex(make_geometry(obj)))
        except (RuntimeError, ValueError, shapely.errors.GEOSException):
            self.incomplete += 1
            return
        if geometry.is_empty or not geometry.is_valid:
            self.incomplete += 1
            return
        # Index each polygon part separately to avoid huge boxes around detached parts.
        for part in shapely.get_parts(geometry):
            min_x, min_y, max_x, max_y = part.bounds
            row = self.db.execute(
                "INSERT INTO feature(kind,name,osm_id,wkb,aliases) VALUES(?,?,?,?,?)",
                (kind, name, oid, part.wkb, aliases),
            )
            self.db.execute(
                "INSERT INTO spatial VALUES(?,?,?,?,?)", (row.lastrowid, min_x, max_x, min_y, max_y)
            )

    def node(self, node: Node) -> None:
        if node.tags.get("place") in _SETTLEMENTS:
            self.save(node, "settlement", f"n{node.id}", self.geometry.create_point)
        if poi(node.tags):
            self.save(node, "poi_point", f"n{node.id}", self.geometry.create_point)

    def way(self, way: Way) -> None:
        if way.tags.get("highway") in _ROADS and way.tags.get("area") != "yes":
            self.save(way, "road", f"w{way.id}", self.geometry.create_linestring)

    def area(self, area: Area) -> None:
        tags = area.tags
        oid = f"{'w' if area.from_way() else 'r'}{area.orig_id()}"
        if tags.get("boundary") == "administrative":
            level = tags.get("admin_level") or ""
            kind = _ADMIN.get(int(level)) if level.isascii() and level.isdigit() else None
            if level == "3" and tags.get("ISO3166-2") == "CN-MO":
                kind = "province"
            if kind:
                self.save(area, kind, oid, self.geometry.create_multipolygon)
        elif poi(tags):
            self.save(area, "poi_area", oid, self.geometry.create_multipolygon)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.pbf.open("rb") as source:
        version = f"v{_MAP_SCHEMA}:" + hashlib.file_digest(source, "sha256").hexdigest()
    with TemporaryDirectory(dir=args.output.parent) as directory:
        temporary = Path(directory) / "index.sqlite"
        db = sqlite3.connect(temporary)
        try:
            db.executescript(
                "PRAGMA cache_size=-131072;"
                f"PRAGMA user_version={_MAP_SCHEMA};"
                "CREATE TABLE metadata(version TEXT NOT NULL, source TEXT NOT NULL);"
                "CREATE TABLE feature(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,"
                " osm_id TEXT NOT NULL, wkb BLOB NOT NULL, aliases TEXT NOT NULL);"
                "CREATE VIRTUAL TABLE spatial USING rtree(id,min_x,max_x,min_y,max_y);"
            )
            db.execute("INSERT INTO metadata VALUES(?,?)", (version, args.pbf.name))
            collector = Collector(db)
            # libosmium still resolves geometry members before filtering the Python callbacks.
            names_or_refs = osmium.filter.KeyFilter("name", "name:zh", "name:zh-Hans", "ref")
            collector.apply_file(str(args.pbf), locations=True, filters=[names_or_refs])
            counts = dict(db.execute("SELECT kind,COUNT(*) FROM feature GROUP BY kind"))
            if not counts:
                raise ValueError("OSM index contains no usable features")
            db.commit()
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("OSM index failed SQLite integrity check")
        finally:
            db.close()
        temporary.replace(args.output)
    print(json.dumps({"version": version, "features": counts, "incomplete_geometries": collector.incomplete}))


if __name__ == "__main__":
    main()
