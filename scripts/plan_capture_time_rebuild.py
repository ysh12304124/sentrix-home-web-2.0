"""只读旧库和媒体原件，生成时区重建计划；不写数据库。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sentrix.domain.media.capture_time import CaptureTime, offset_capture, restore_capture_timezone
from sentrix.domain.media.models import MediaType
from sentrix.infrastructure.media.audio_metadata import FfprobeAudioMetadataReader
from sentrix.infrastructure.media.capture_time import ResolveCaptureTime
from sentrix.infrastructure.media.metadata import PillowMetadataReader
from sentrix.infrastructure.media.video_metadata import FfprobeVideoMetadataReader


def plan(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM assets ORDER BY id").fetchall()
        event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        connection.close()
    resolver = ResolveCaptureTime()
    parsed: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for row in rows:
        if row["parent_asset_id"]:
            continue
        path = Path(row["path"])
        try:
            kind = MediaType(row["media_type"])
            if kind is MediaType.IMAGE:
                value, gps, _ = PillowMetadataReader().read(path)
                source = "exif"
            elif kind in (MediaType.VIDEO, MediaType.AUDIO):
                metadata = (
                    FfprobeVideoMetadataReader().read(path)
                    if kind is MediaType.VIDEO
                    else FfprobeAudioMetadataReader().read(path)
                )
                value, gps, source = metadata.captured_datetime(), metadata.gps(), metadata.creation_source
            else:
                value, gps, source = None, None, "unknown"
            if not path.is_file():
                raise FileNotFoundError(path)
            local, evidence = resolver(value, gps, source)
            parsed[row["id"]] = {
                "asset_id": row["id"],
                "before": row["captured_at"],
                "after": local.isoformat() if local else None,
                "capture_time": asdict(evidence),
                "derived": False,
            }
        except (ValueError, OSError) as error:
            errors.append({"asset_id": row["id"], "error": str(error)})
    for row in rows:
        if not row["parent_asset_id"]:
            continue
        parent = parsed.get(row["parent_asset_id"])
        if parent is None:
            errors.append({"asset_id": row["id"], "error": "parent missing or unreadable"})
            continue
        from datetime import datetime

        value = restore_capture_timezone(
            datetime.fromisoformat(parent["after"]) if parent["after"] else None,
            CaptureTime(**parent["capture_time"]),
        )
        local = offset_capture(value, float(row["source_timestamp_sec"] or 0))
        parsed[row["id"]] = {
            "asset_id": row["id"],
            "before": row["captured_at"],
            "after": local.isoformat() if local else None,
            "capture_time": parent["capture_time"],
            "derived": True,
        }
    return {
        "database": str(database),
        "read_only": True,
        "assets": list(parsed.values()),
        "errors": errors,
        "existing_events": event_count,
        "next_steps": [
            "备份原库；在新数据目录按原始媒体、原身份背景和相同模型配置重建。",
            "重新生成观察时间、派生帧/场景时间、地点索引、词法日期锚点及事件分组。",
            "比较新旧事件成员和跨日检索，保留原库与历史评分；人工确认后切换数据目录。",
            "schema迁移仅新增来源字段；不自动推定或改写旧拍摄时间。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() == args.database.resolve():
        parser.error("输出须为尚不存在的独立JSON文件")
    result = plan(args.database)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
