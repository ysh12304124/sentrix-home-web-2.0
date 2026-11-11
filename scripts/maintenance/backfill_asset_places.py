"""给已有 GPS 但还没有 asset_places 行的资产补解析地点。

GeoNames 数据在 ``/home/asus/sentrix-geodata``（CN.zip + cities15000.zip），各用户
``data/geo`` 已放软链；这里只读原始 GPS，结果落该用户的 asset_places。默认 dry-run，
``--apply`` 才写，并在写前用 SQLite backup 备份整库。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.places.composite import OfflinePlaceResolver
from sentrix.infrastructure.places.geonames import GeoNamesNearestResolver
from sentrix.infrastructure.places.pygeocn import PyGeoCNAdminResolver

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"
GEO_DIR = Path("/home/asus/sentrix-geodata")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.all and args.database is None:
        parser.error("需要 --all 或 --database")
    paths = sorted(DEMO_ROOT.glob("*/data/sentrix.db")) if args.all else [args.database.resolve()]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"place-backfill-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)

    resolver = OfflinePlaceResolver(
        admin=PyGeoCNAdminResolver(),
        nearest=GeoNamesNearestResolver(GEO_DIR),
    )
    total = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        database = Database(path, path.parent)
        places = SqliteAssetPlaceRepository(database)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT a.id, a.scope_id, a.gps_latitude, a.gps_longitude"
            " FROM assets a LEFT JOIN asset_places p ON p.asset_id = a.id"
            " WHERE p.asset_id IS NULL AND a.gps_latitude IS NOT NULL"
            " AND a.gps_longitude IS NOT NULL"
        ).fetchall()
        if args.apply and rows:
            destination = sqlite3.connect(backup_dir / f"{path.parent.parent.name}.db")
            connection.backup(destination)
            destination.close()
        resolved = 0
        for row in rows:
            gps = GpsPoint(float(row["gps_latitude"]), float(row["gps_longitude"]))
            place = resolver.resolve(gps)
            if place is None:
                continue
            resolved += 1
            if args.apply:
                places.save(asset_id=str(row["id"]), scope_id=str(row["scope_id"]), gps=gps, place=place)
        connection.close()
        total += resolved
        print(f"{path.parent.parent.name}: candidates={len(rows)} resolved={resolved}")
    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: resolved={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
