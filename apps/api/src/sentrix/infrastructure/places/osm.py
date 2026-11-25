"""Offline OSM lookup against a read-only regional SQLite spatial index."""

from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from shapely import from_wkb
from shapely.affinity import scale
from shapely.geometry import Point

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlaceDetail,
    PlacePrecision,
    PlaceSource,
    is_road_number,
)
from sentrix.domain.places.ports import AdminResolver

log = logging.getLogger(__name__)
_BOUNDARIES = ("province", "city", "district", "township", "village")
_DEFAULT_NEARBY_RADIUS_M = 1500
_NEARBY_KINDS = ("settlement", "road", "poi_point")
_AREA_KINDS = (*_BOUNDARIES, "poi_area")
_POINT_FALLBACK_AREAS = {"settlement": {"township", "village"}, "poi_point": {"poi_area"}}
_DIRECT_ADMIN_CITIES = {"北京市", "天津市", "上海市", "重庆市"}
ROAD_RADII_M = (30, 60, 100, 1000, 1500, 2000)


class OsmPlaceResolver:
    def __init__(
        self, index: Path, admin_fallback: AdminResolver, *, road_radius_m: int = _DEFAULT_NEARBY_RADIUS_M
    ) -> None:
        if road_radius_m not in ROAD_RADII_M:
            raise ValueError("unsupported road search radius")
        self._uri = index.resolve().as_uri() + "?mode=ro"
        self._fallback = admin_fallback
        self._version = ""
        self._radii_m = dict.fromkeys(_NEARBY_KINDS, _DEFAULT_NEARBY_RADIUS_M)
        self._radii_m["road"] = road_radius_m
        self._cached_resolve = lru_cache(maxsize=4096)(self._resolve)
        try:
            with closing(sqlite3.connect(self._uri, uri=True)) as db:
                schema = db.execute("PRAGMA user_version").fetchone()[0]
                if schema not in {1, 2, 3, 4}:
                    raise ValueError("unsupported index schema")
                row = db.execute("SELECT version FROM metadata").fetchone()
                if not row or not row[0]:
                    raise ValueError("missing map version")
                db.execute("SELECT id FROM spatial LIMIT 1").fetchone()
                self._version = str(row[0])
        except (sqlite3.Error, ValueError) as error:
            log.warning("OSM 地点索引不可用，保留粗行政区解析: %s (%s)", index, error)
        else:
            log.info("OSM 地点索引已启用: %s version=%s", index, self._version)

    def resolve(self, gps: GpsPoint) -> Place | None:
        return self._cached_resolve(gps.latitude, gps.longitude)

    def _resolve(self, lat: float, lon: float) -> Place | None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        gps = GpsPoint(lat, lon)
        matched: dict[str, PlaceDetail] = {}
        if self._version and abs(lat) < 89:
            try:
                matched = self._lookup(lat, lon)
            except sqlite3.Error as error:
                log.warning("OSM 地点查询失败，保留粗行政区解析: %s", error)
        admin_names = {key: matched[key].name for key in _BOUNDARIES[:3] if key in matched}
        province = admin_names.get("province", "")
        if province in _DIRECT_ADMIN_CITIES:
            admin_names.setdefault("city", province)
        osm_admin = AdminArea(**admin_names)
        # Prefer a coherent upper chain. Do not attach lower boundaries to a conflicting district.
        admin = osm_admin
        if not osm_admin.district and province not in {"澳门", "澳門"}:
            fallback = self._fallback.resolve_admin(gps)
            if fallback is not None:
                admin = fallback
                if any(
                    getattr(osm_admin, key)
                    and getattr(fallback, key)
                    and getattr(osm_admin, key) != getattr(fallback, key)
                    for key in ("province", "city")
                ):
                    matched = {}
        details = tuple(
            matched[key] for key in (*_BOUNDARIES[3:], *_NEARBY_KINDS, "poi_area") if key in matched
        )
        if admin.is_empty and not details:
            return None
        source = PlaceSource.OSM if matched else PlaceSource.PYGEOCN
        if admin.district:
            precision = PlacePrecision.DISTRICT
        elif not admin.is_empty:
            precision = PlacePrecision.CITY
        else:
            precision = PlacePrecision.LOCALITY
        return Place(
            admin,
            NearestPlace(),
            precision,
            source,
            gps,
            0.90 if not admin.is_empty else 0.70,
            details=details,
            map_version=self._version if matched else "",
        )

    def _lookup(self, lat: float, lon: float) -> dict[str, PlaceDetail]:
        point = Point(lon, lat)
        metres_x = 111_320 * math.cos(math.radians(lat))
        metres_y = 110_900.0
        containing_areas: set[str] = set()
        result: dict[str, PlaceDetail] = {}
        with closing(sqlite3.connect(self._uri, uri=True)) as db:
            db.create_function("is_road_number", 1, is_road_number, deterministic=True)
            # Read the version from the same connection as the geometry snapshot.
            if db.execute("SELECT version FROM metadata").fetchone() != (self._version,):
                raise sqlite3.DatabaseError("OSM index version changed; restart the service")
            for kind in (*_AREA_KINDS, *_NEARBY_KINDS):
                if containing_areas.intersection(_POINT_FALLBACK_AREAS.get(kind, ())):
                    continue
                radius = self._radii_m.get(kind, 0)
                rows = db.execute(
                    "SELECT f.name, f.osm_id, f.wkb "
                    "FROM spatial s JOIN feature f ON f.id=s.id "
                    "WHERE s.min_x<=? AND s.max_x>=? AND s.min_y<=? AND s.max_y>=? "
                    "AND f.kind=? AND (f.kind!='road' OR NOT is_road_number(f.name))",
                    (
                        lon + radius / metres_x,
                        lon - radius / metres_x,
                        lat + radius / metres_y,
                        lat - radius / metres_y,
                        kind,
                    ),
                )
                if radius:
                    closest = None
                    for name, osm_id, raw in rows:
                        metric = scale(from_wkb(raw), xfact=metres_x, yfact=metres_y, origin=point)
                        distance = float(metric.distance(point))
                        if distance <= radius and (closest is None or (distance, osm_id) < closest[:2]):
                            closest = (distance, osm_id, name)
                    if closest is not None:
                        distance, osm_id, name = closest
                        result[kind] = PlaceDetail(kind, name, "nearby", osm_id, round(distance, 1))
                    continue
                bounded = rows.fetchmany(257)
                if len(bounded) > 256:
                    containing_areas.add(kind)
                    continue
                by_name: dict[str, PlaceDetail] = {}
                for name, osm_id, raw in bounded:
                    geometry = from_wkb(raw)
                    if not geometry.contains(point):
                        continue
                    containing_areas.add(kind)
                    if (
                        kind in _BOUNDARIES
                        and geometry.boundary.distance(point) * min(metres_x, metres_y) < 10
                    ):
                        continue
                    # Multiple pieces with the same displayed name are one candidate.
                    old = by_name.get(name)
                    if old is None or osm_id < old.osm_id:
                        by_name[name] = PlaceDetail(kind, name, "contains", osm_id)
                if len(by_name) == 1:
                    result[kind] = next(iter(by_name.values()))
        return result
