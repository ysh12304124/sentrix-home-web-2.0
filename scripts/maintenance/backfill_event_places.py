"""用成员资产已解析的地点补齐事件卡片上的地点。

只填占位值（空 / 其他或不确定 / 未知 / 未知地点），不覆盖已有事件结论。
"""

from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"
PLACEHOLDERS = ("", "其他或不确定", "未知", "未知地点")


def _display(row: sqlite3.Row) -> tuple[str, str]:
    admin = "".join(
        dict.fromkeys(
            value
            for value in (row["admin_province"], row["admin_city"], row["admin_district"])
            if value
        )
    )
    nearest = " / ".join(
        dict.fromkeys(value for value in (row["nearest_township"], row["nearest_locality"]) if value)
    )
    if nearest:
        nearest += "附近"
    place = " · ".join(part for part in (admin, nearest) if part)
    district = row["admin_district"] or row["nearest_locality"] or row["admin_city"] or ""
    return place, district


def update_database(path: Path, *, apply: bool, backup: Path) -> tuple[int, int]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    if apply:
        destination = sqlite3.connect(backup)
        connection.backup(destination)
        destination.close()
    events = connection.execute("SELECT id, place FROM events").fetchall()
    updated = 0
    for event in events:
        if str(event["place"] or "") not in PLACEHOLDERS:
            continue
        rows = connection.execute(
            "SELECT p.admin_province, p.admin_city, p.admin_district,"
            " p.nearest_township, p.nearest_locality"
            " FROM event_observations eo"
            " JOIN observations o ON o.id = eo.observation_id"
            " JOIN asset_places p ON p.asset_id = o.asset_id"
            " WHERE eo.event_id = ?",
            (event["id"],),
        ).fetchall()
        if not rows:
            continue
        counts = collections.Counter(_display(row) for row in rows)
        place, district = counts.most_common(1)[0][0]
        if not place:
            continue
        updated += 1
        if apply:
            connection.execute(
                "UPDATE events SET place = ?, admin_district = ? WHERE id = ?",
                (place, district, event["id"]),
            )
    if apply:
        connection.commit()
    connection.close()
    return len(events), updated


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
    backup_dir = BACKUP_ROOT / f"event-place-backfill-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    total_events = total_updated = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        events, updated = update_database(
            path,
            apply=args.apply,
            backup=backup_dir / f"{path.parent.parent.name}.db",
        )
        total_events += events
        total_updated += updated
        print(f"{path.parent.parent.name}: events={events} updated={updated}")
    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: events={total_events} updated={total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
