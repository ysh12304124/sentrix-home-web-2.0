"""Re-resolve saved media GPS and compare with supplied album location annotations."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.places.osm import ROAD_RADII_M, OsmPlaceResolver
from sentrix.infrastructure.places.pygeocn import PyGeoCNAdminResolver

FIELDS = {
    "province_state": ("province",),
    "city": ("city",),
    "district": ("district",),
    "town_suburb": ("township", "village", "settlement"),
    "road": ("road",),
    "poi": ("poi_area", "poi_point"),
}


@dataclass(frozen=True)
class LocationReference:
    fields: dict[str, str]
    raw: dict[str, str]
    normalization: str = "original"


def admin_names() -> dict[str, dict[str, set[str]]]:
    """Read administrative names and codes independently of GPS and the OSM index."""
    package = files("PyGeoCN")
    names: dict[str, dict[str, set[str]]] = {}
    for level in ("province", "city", "district"):
        codes: dict[str, set[str]] = defaultdict(set)
        path = package.joinpath(f"china_{level}.geojson")
        for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
            name, code = feature["properties"]["name"], str(feature["properties"]["gb"])
            if len(code) == 9 and code.startswith("156") and code.isascii() and code.isdigit():
                codes[name].add(code[3:])
        names[level] = dict(codes)
    return names


def normalize_reference(details: dict[str, str], names: dict[str, dict[str, set[str]]]) -> LocationReference:
    raw = {key: str(details.get(key) or "").strip() for key in FIELDS}
    parts = {part.strip() for part in str(details.get("display_name") or "").split(",")}
    province, city, district = (raw[key] for key in ("province_state", "city", "district"))
    if province in {"澳门", "澳門", "澳门特别行政区", "澳門特別行政區"}:
        fields = {**raw, "province_state": "澳门", "city": ""}
        lower = district or city
        if lower in parts and lower not in {province, "澳门", "澳門"} and lower.endswith(("区", "區")):
            fields["district"] = lower.removesuffix("區") + "区" if lower.endswith("區") else lower
        return LocationReference(fields, raw, "macau_city_not_applicable")

    if str(details.get("country_code") or "").lower() != "cn" or not city:
        return LocationReference(raw, raw)
    if not province:
        provinces = parts & names["province"].keys()
        if len(provinces) == 1:
            province = provinces.pop()
    province_prefixes = {code[:2] for code in names["province"].get(province, ())}
    district_codes = {code for code in names["district"].get(city, ()) if code[:2] in province_prefixes}
    if (
        not district_codes
        or city == province
        or any(code[:2] in province_prefixes for code in names["city"].get(city, ()))
    ):
        return LocationReference(raw, raw)
    parents = {
        parent
        for parent in parts
        for parent_code in names["city"].get(parent, ())
        for code in district_codes
        if parent_code[:4] == code[:4] or (parent == province and parent_code[:2] == code[:2])
    }
    if len(parents) != 1 or city not in parts or district not in {"", city}:
        return LocationReference({**raw, "city": ""}, raw, "city_needs_review")
    return LocationReference(
        {**raw, "province_state": province, "city": parents.pop(), "district": city},
        raw,
        "moved_county_to_district",
    )


def common_fields(sources: list[dict[str, str]]) -> dict[str, str]:
    return {
        key: value
        for key in FIELDS
        if len(values := {source.get(key) for source in sources}) == 1 and (value := values.pop())
    }


def references(metadata: Path) -> tuple[dict[str, LocationReference], dict[str, LocationReference]]:
    images: dict[str, LocationReference] = {}
    videos: dict[str, LocationReference] = {}
    image_file = metadata / "image_metadata.jsonl"
    if image_file.is_file():
        names = admin_names()
        for line in image_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                name = row["filename"]
                if name in images:
                    raise ValueError(f"duplicate image reference: {name}")
                images[name] = normalize_reference(row.get("readable_location_details") or {}, names)
    video_file = metadata / "video_source_images.json"
    if video_file.is_file():
        for record in json.loads(video_file.read_text(encoding="utf-8"))["videos"]:
            empty = LocationReference({}, {})
            sources = [images.get(item["filename"], empty) for item in record["images"]]
            normalized = common_fields([source.fields for source in sources])
            raw = common_fields([source.raw for source in sources])
            provenance = "normalized_source_consensus" if normalized != raw else "original"
            videos[record["video_id"]] = LocationReference(normalized, raw, provenance)
    return images, videos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--road-radius", type=int, choices=ROAD_RADII_M, default=30)
    args = parser.parse_args()
    images, videos = references(args.metadata)
    resolver = OsmPlaceResolver(args.index, PyGeoCNAdminResolver(), road_radius_m=args.road_radius)
    uri = args.db.resolve().as_uri() + "?mode=ro"
    counts: dict[str, Counter[str]] = {key: Counter() for key in FIELDS}
    coverage: Counter[str] = Counter()
    reference_counts: Counter[str] = Counter()
    report = []
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute(
            "SELECT a.id,a.media_type,a.file_name,a.gps_latitude,a.gps_longitude,p.source "
            "FROM assets a LEFT JOIN asset_places p ON p.asset_id=a.id WHERE a.scope_id=? "
            "AND a.parent_asset_id IS NULL AND a.media_type IN ('image','video') ORDER BY a.id",
            (args.scope,),
        ).fetchall()
    if not rows:
        raise ValueError("scope contains no source pictures or videos")
    for asset_id, kind, file_name, lat, lon, legacy_source in rows:
        reference = images.get(file_name) if kind == "image" else videos.get(Path(file_name).stem)
        reference = reference or LocationReference({}, {})
        gt = reference.fields
        reference_counts[reference.normalization] += 1
        has_gps = lat is not None and lon is not None
        place = resolver.resolve(GpsPoint(lat, lon)) if has_gps else None
        actual = {}
        aliases: dict[str, tuple[str, ...]] = {}
        if place is not None:
            actual.update({key: getattr(place.admin, key) for key in ("province", "city", "district")})
            actual.update({item.kind: item.name for item in place.details})
            aliases.update({item.kind: item.aliases for item in place.details})
        source = place.source.value if place else "no_resolution" if has_gps else "no_gps"
        coverage[f"{kind}:{source}"] += 1
        comparisons = {}
        for key, candidate_kinds in FIELDS.items():
            expected = str(gt.get(key) or "").strip()
            found = [actual[name] for name in candidate_kinds if actual.get(name)]
            names = [*found, *(alias for name in candidate_kinds for alias in aliases.get(name, ()))]
            name_matches = expected.casefold() in {value.casefold() for value in names}
            if key == "road" and not name_matches:
                name_matches = "".join(expected.split()).casefold() in {
                    "".join(value.split()).casefold() for value in names
                }
            if key == "city" and reference.normalization == "city_needs_review":
                status = "reference_review"
            elif key == "city" and reference.normalization == "macau_city_not_applicable":
                status = "not_applicable"
            elif not expected:
                status = "no_gt"
            elif not found:
                status = "missing"
            elif name_matches:
                status = "match"
            elif key == "city" and expected.casefold() == actual.get("district", "").casefold():
                status = "different_level"
            else:
                status = "different"
            counts[key][status] += 1
            comparisons[key] = {"expected": expected, "actual": names, "status": status}
        report.append(
            {
                "asset_id": asset_id,
                "type": kind,
                "file_name": file_name,
                "source": source,
                "old_source": legacy_source,
                "place": place.display() if place else None,
                "map_version": place.map_version if place else None,
                "reference_normalization": reference.normalization,
                "raw_reference": reference.raw,
                "fields": comparisons,
            }
        )
    summary = {
        "scope": args.scope,
        "road_radius_m": args.road_radius,
        "map_index": args.index.name,
        "assets": len(report),
        "coverage": coverage,
        "fields": counts,
        "reference_normalization": reference_counts,
        "reference_limit": (
            "Image reference is the supplied capture metadata; OSM/Nominatim labels are not independent "
            "ground truth. Video references are common labels from all mapped source photos only."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({**summary, "assets_detail": report}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
