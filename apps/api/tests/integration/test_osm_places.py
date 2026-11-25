from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from runpy import run_path

from shapely.geometry import LineString, Point, box

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import AdminArea, PlaceSource
from sentrix.infrastructure.places.osm import OsmPlaceResolver

normalize_reference = run_path(
    str(Path(__file__).resolve().parents[4] / "scripts" / "validate_osm_places.py")
)["normalize_reference"]


class Fallback:
    available = True

    def resolve_admin(self, gps: GpsPoint) -> AdminArea | None:
        return AdminArea(province="备用省")


def test_osm_maps_containing_boundaries_and_nearby_features(tmp_path):
    path = tmp_path / "osm-places.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            PRAGMA user_version=4;
            CREATE TABLE metadata(version TEXT);
            INSERT INTO metadata VALUES ('test-map');
            CREATE TABLE feature(
                id INTEGER PRIMARY KEY, kind TEXT, name TEXT, osm_id TEXT, wkb BLOB, aliases TEXT
            );
            CREATE VIRTUAL TABLE spatial USING rtree(id,min_x,max_x,min_y,max_y);
        """)
        features = [
            ("district", "示例县", box(113, 38, 115, 40)),
            ("township", "示例镇", box(113.5, 38.5, 114.5, 39.5)),
            ("village", "示例村", box(113.9, 38.9, 114.1, 39.1)),
            ("settlement", "临近村", Point(114, 39)),
            ("road", "示例路", LineString([(113.99, 39.0001), (114.01, 39.0001)])),
            ("road", "稍远路", LineString([(113.99, 39.00015), (114.01, 39.00015)])),
            ("road", "S287", LineString([(113.99, 39), (114.01, 39)])),
            ("road", "远处大道", LineString([(113.99, 39.012), (114.01, 39.012)])),
            ("poi_area", "示例公园", box(113.999, 38.999, 114.001, 39.001)),
            ("poi_point", "附近咖啡馆", Point(114, 39)),
            ("settlement", "示例居民点", Point(115, 39.014)),
            ("settlement", "稍远居民点", Point(115, 39.01404)),
            ("poi_point", "示例景点", Point(115, 39.014)),
            ("poi_point", "稍远景点", Point(115, 39.01404)),
            ("province", "澳门", box(113.5, 22, 114, 23)),
        ]
        features.extend(
            ("road", f"密集路段{i}号", LineString([(114.99, 39.012), (115.01, 39.012)])) for i in range(257)
        )
        for i, (kind, name, geometry) in enumerate(features, start=1):
            x1, y1, x2, y2 = geometry.bounds
            aliases = '["G309"]' if kind == "road" else "[]"
            db.execute(
                "INSERT INTO feature VALUES(?,?,?,?,?,?)", (i, kind, name, str(i), geometry.wkb, aliases)
            )
            db.execute("INSERT INTO spatial VALUES(?,?,?,?,?)", (i, x1, x2, y1, y2))
        db.commit()
    resolver = OsmPlaceResolver(path, Fallback())
    place = resolver.resolve(GpsPoint(39, 114))
    assert place is not None
    assert place.source is PlaceSource.OSM
    assert place.admin.district == "示例县"
    assert place.map_version == "test-map"
    assert place.display() == "示例县 · 示例镇 · 示例村 · 示例路附近 · 示例公园"
    assert "示例路" in place.searchable_text()
    assert "G309" not in place.searchable_text()
    assert "S287" not in place.display()
    assert not next(item for item in place.details if item.kind == "road").aliases
    distant = GpsPoint(39.0005, 114)
    narrow = OsmPlaceResolver(path, Fallback(), road_radius_m=30).resolve(distant)
    wide = OsmPlaceResolver(path, Fallback(), road_radius_m=60).resolve(distant)
    assert narrow is not None and not any(item.kind == "road" for item in narrow.details)
    assert wide is not None and any(item.kind == "road" for item in wide.details)
    production = resolver.resolve(GpsPoint(39.002, 115))
    assert production is not None
    assert next(item for item in production.details if item.kind == "road").distance_m == 1109.0
    assert {item.name for item in production.details} >= {"示例居民点", "示例景点"}
    outside = resolver.resolve(GpsPoint(39, 115))
    assert outside is not None
    assert not any(item.kind in {"settlement", "poi_point"} for item in outside.details)
    fallback = resolver.resolve(GpsPoint(30, 105))
    assert fallback is not None
    assert fallback.source is PlaceSource.PYGEOCN
    assert not fallback.details
    special_region = resolver.resolve(GpsPoint(22.5, 113.75))
    assert special_region is not None
    assert special_region.admin == AdminArea(province="澳门")


def test_missing_osm_index_uses_only_coarse_fallback(tmp_path):
    place = OsmPlaceResolver(tmp_path / "missing.sqlite", Fallback()).resolve(GpsPoint(39, 114))
    assert place is not None
    assert place.display() == "备用省"
    assert place.nearest.is_empty


def test_reference_recovers_parent_only_when_address_and_admin_codes_agree():
    names = {
        "province": {"示例省": {"120000"}},
        "city": {"上级市": {"120100"}, "其他市": {"120200"}},
        "district": {"同名县": {"120101", "120201"}},
    }
    raw = {
        "country_code": "cn",
        "province_state": "",
        "city": "同名县",
        "display_name": "示例路, 同名县, 上级市, 示例省, 中国",
    }
    result = normalize_reference(raw, names)
    assert result.fields["province_state"] == "示例省"
    assert result.fields["city"] == "上级市"
    assert result.fields["district"] == "同名县"
    assert result.raw["city"] == "同名县"
    ambiguous = normalize_reference({**raw, "display_name": "同名县, 上级市, 其他市, 示例省"}, names)
    assert ambiguous.normalization == "city_needs_review"
    assert ambiguous.fields["city"] == ""


def test_reference_excludes_macau_from_city_denominator():
    result = normalize_reference(
        {"province_state": "澳門", "city": "某區", "display_name": "某區, 澳門, 中国"}, {}
    )
    assert result.normalization == "macau_city_not_applicable"
    assert result.fields["province_state"] == "澳门"
    assert result.fields["city"] == ""
    assert result.fields["district"] == "某区"
