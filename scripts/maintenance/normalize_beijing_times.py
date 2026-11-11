"""把已有用户库的拍摄时间统一到北京时间，并用文件名补齐能恢复的时间。

这是**数据维护脚本**，不是 schema 迁移：
- 已有 EXIF/视频时间：aware 转到 Asia/Shanghai；naive 有 GPS 时按坐标时区还原后转北京，
  没 GPS 时按北京时间解释。
- 缺失时间：尝试文件名（相机/微信/QQ/截图），仍没有就保持 NULL。
- 观察时间跟随资产；事件时间从成员资产重新取 min/max；没有成员时按同一规则归一。
- 先备份整库；默认 dry-run，只有 --apply 才写。

用法：
    .venv/bin/python scripts/maintenance/normalize_beijing_times.py --all --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sentrix.domain.media.capture_time import BEIJING, capture_time_from_filename
from sentrix.infrastructure.media.capture_time import coordinate_timezone

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"


@dataclass(frozen=True)
class TimeChange:
    before: str | None
    after: str | None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _normalize(value: datetime | None, gps: tuple[float, float] | None) -> tuple[
    datetime | None, str, str
]:
    """(北京时间, original_timezone, status)。"""
    if value is None:
        return None, "", "unknown"
    if value.utcoffset() is not None:
        name = coordinate_timezone(*gps) if gps else ""
        return value.astimezone(BEIJING), name, "offset_gps_local" if name else "offset_only"
    name = coordinate_timezone(*gps) if gps else ""
    if name:
        try:
            local = value.replace(tzinfo=ZoneInfo(name))
            return local.astimezone(BEIJING), name, "gps_timezone_inferred"
        except Exception:  # noqa: BLE001 - 坐标时区异常就按北京解释
            pass
    return value.replace(tzinfo=BEIJING), "", "beijing_assumed"


def _normalize_event(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.utcoffset() is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


def _evidence(
    old: dict[str, object],
    *,
    raw: str,
    source: str,
    original_timezone: str,
    status: str,
) -> dict[str, object]:
    return {
        **old,
        "raw": str(old.get("raw") or raw or ""),
        "source": str(old.get("source") or source or "unknown"),
        "timezone": "Asia/Shanghai",
        "original_timezone": original_timezone
        or str(old.get("original_timezone") or old.get("timezone") or ""),
        "status": str(old.get("status") or status),
    }


def normalize_database(database: Path, *, apply: bool, backup: Path | None = None) -> dict[str, int]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    if apply and backup is not None:
        destination = sqlite3.connect(backup)
        connection.backup(destination)
        destination.close()
    assets = connection.execute("SELECT * FROM assets ORDER BY id").fetchall()
    changes: dict[str, TimeChange] = {}
    original = {row["id"]: row for row in assets}
    for row in assets:
        asset_id = str(row["id"])
        gps = (
            (float(row["gps_latitude"]), float(row["gps_longitude"]))
            if row["gps_latitude"] is not None and row["gps_longitude"] is not None
            else None
        )
        value = _parse(row["captured_at"])
        source = "exif" if row["media_type"] == "image" else "unknown"
        if value is None and row["file_name"]:
            value = capture_time_from_filename(str(row["file_name"]))
            if value is not None:
                source = "filename"
        if value is None and row["parent_asset_id"] and row["source_timestamp_sec"] is not None:
            parent = original.get(str(row["parent_asset_id"]))
            parent_value = _parse(parent["captured_at"]) if parent else None
            if parent_value is not None:
                value = parent_value + timedelta(seconds=float(row["source_timestamp_sec"] or 0))
                source = "derived_frame"
        normalized, original_timezone, status = _normalize(value, gps)
        if normalized is None:
            continue
        after = normalized.isoformat()
        if after != row["captured_at"]:
            changes[asset_id] = TimeChange(row["captured_at"], after)
            if apply:
                old_evidence = json.loads(row["capture_time_json"] or "{}")
                evidence = _evidence(
                    old_evidence,
                    raw=value.isoformat() if value else "",
                    source=source,
                    original_timezone=original_timezone,
                    status=status,
                )
                connection.execute(
                    "UPDATE assets SET captured_at = ?, capture_time_json = ? WHERE id = ?",
                    (after, json.dumps(evidence, ensure_ascii=False), asset_id),
                )
                connection.execute(
                    "UPDATE observations SET captured_at = ? WHERE asset_id = ?",
                    (after, asset_id),
                )
    # 事件时间从成员资产重新取 min/max；没成员时归一旧字符串。
    event_changes = 0
    events = connection.execute(
        "SELECT id, time_start, time_end, source_type, source_asset_id, source_end_sec"
        " FROM events"
    ).fetchall()
    for event in events:
        bounds = connection.execute(
            "SELECT MIN(a.captured_at) AS start, MAX(a.captured_at) AS end"
            " FROM event_observations eo"
            " JOIN observations o ON o.id = eo.observation_id"
            " JOIN assets a ON a.id = o.asset_id"
            " WHERE eo.event_id = ? AND a.captured_at IS NOT NULL",
            (event["id"],),
        ).fetchone()
        start = bounds["start"] if bounds else None
        end = bounds["end"] if bounds else None
        # 视频事件覆盖整段视频：成员资产是中间的关键帧，不能用它们当事件起止。
        if event["source_type"] == "video_scene" and event["source_asset_id"]:
            source = connection.execute(
                "SELECT captured_at FROM assets WHERE id = ?", (event["source_asset_id"],)
            ).fetchone()
            source_start = _normalize_event(
                _parse(source["captured_at"] if source else None)
            )
            if source_start is not None:
                start = source_start.isoformat()
                end = (
                    source_start
                    + timedelta(seconds=float(event["source_end_sec"] or 0.0))
                ).isoformat()
        if start is None:
            start_value = _normalize_event(_parse(event["time_start"]))
            end_value = _normalize_event(_parse(event["time_end"]))
            start = start_value.isoformat() if start_value else None
            end = end_value.isoformat() if end_value else None
        if start != event["time_start"] or end != event["time_end"]:
            event_changes += 1
            if apply:
                connection.execute(
                    "UPDATE events SET time_start = ?, time_end = ? WHERE id = ?",
                    (start, end, event["id"]),
                )
    if apply:
        connection.commit()
    connection.close()
    return {"assets": len(changes), "events": event_changes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.all and args.database is None:
        parser.error("需要 --database 或 --all")
    paths = (
        sorted(DEMO_ROOT.glob("*/data/sentrix.db"))
        if args.all
        else [args.database.resolve()]
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"beijing-backfill-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    total_assets = total_events = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        result = normalize_database(
            path,
            apply=args.apply,
            backup=backup_dir / f"{path.parent.parent.name}.db" if args.apply else None,
        )
        total_assets += result["assets"]
        total_events += result["events"]
        print(f"{path.parent.parent.name}: assets={result['assets']} events={result['events']}")
    print(
        f"{'APPLIED' if args.apply else 'DRY-RUN'}: "
        f"assets={total_assets} events={total_events} backup={backup_dir if args.apply else '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
