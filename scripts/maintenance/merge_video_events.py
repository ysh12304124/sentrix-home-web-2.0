"""把一个视频的多个场景事件合并成一条，并补上视频描述。

视频关键帧是内部证据：用户应该看到一个视频事件，而不是每个关键帧一条事件。
新导入的视频已经在抽取阶段合并；这个脚本处理历史库。默认 dry-run，``--apply``
才写，并在写前用 SQLite backup 备份整库。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"
PLACEHOLDER_PLACES = ("", "其他或不确定", "未知", "未知地点")


def _latest_observations(connection: sqlite3.Connection, event_ids: list[str]) -> list[tuple[float, str]]:
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        "SELECT a.source_timestamp_sec, o.caption, o.activity, o.place, o.ocr_text"
        " FROM event_observations eo"
        " JOIN observations o ON o.id = eo.observation_id"
        " JOIN assets a ON a.id = o.asset_id"
        f" WHERE eo.event_id IN ({placeholders})",
        event_ids,
    ).fetchall()
    result: list[tuple[float, str]] = []
    for row in rows:
        parts: list[str] = []
        for value in (row[1], row[2], row[3]):
            text = str(value or "").strip()
            if text and text not in parts:
                parts.append(text)
        ocr = str(row[4] or "").strip()
        if ocr:
            parts.append(f"文字：{ocr[:120]}")
        if parts:
            result.append((float(row[0] or 0.0), "；".join(parts)[:300]))
    return result


def _speech(connection: sqlite3.Connection, events: list[sqlite3.Row]) -> tuple[str, list[dict[str, object]]]:
    transcripts: list[str] = []
    segments: list[dict[str, object]] = []
    for event in events:
        try:
            metadata = json.loads(event["source_metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        text = str(metadata.get("speech_transcript") or "").strip()
        if text and text not in transcripts:
            transcripts.append(text)
        raw_segments = metadata.get("speech_segments")
        if isinstance(raw_segments, list):
            segments.extend(item for item in raw_segments if isinstance(item, dict))
    return "；".join(transcripts), segments


def _description(connection: sqlite3.Connection, events: list[sqlite3.Row], file_name: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for event in events:
        summary = str(event["summary"] or "").strip()
        if (
            summary
            and not summary.startswith(("视频场景", "时间地点信息不足", "当前提供的观察数据", "时间戳记录"))
            and summary not in seen
        ):
            seen.add(summary)
            parts.append(summary)
    for timestamp, text in sorted(_latest_observations(connection, [event["id"] for event in events])):
        if text in seen:
            continue
        seen.add(text)
        parts.append(f"{timestamp:.0f}s：{text}")
    transcript, _segments = _speech(connection, events)
    if transcript:
        parts.append(f"语音：{transcript}")
    if not parts:
        parts.append(f"视频：{file_name}")
    return "；".join(parts)[:1500]


def merge_database(path: Path, *, apply: bool, backup: Path) -> tuple[int, int]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    if apply:
        destination = sqlite3.connect(backup)
        connection.backup(destination)
        destination.close()
    videos = connection.execute(
        "SELECT DISTINCT source_asset_id FROM events"
        " WHERE source_type = 'video_scene' AND status = 'active'"
        " AND source_asset_id IS NOT NULL AND source_asset_id != ''"
        " UNION"
        " SELECT DISTINCT o.asset_id FROM observations o"
        " JOIN assets a ON a.id = o.asset_id WHERE a.media_type = 'video'"
    ).fetchall()
    merged_videos = moved_events = 0
    for row in videos:
        video_id = str(row["source_asset_id"])
        scene_events = connection.execute(
            "SELECT * FROM events WHERE source_type = 'video_scene' AND status = 'active'"
            " AND source_asset_id = ? ORDER BY source_scene_index, created_at",
            (video_id,),
        ).fetchall()
        generic_events = connection.execute(
            "SELECT DISTINCT e.* FROM events e"
            " JOIN event_observations eo ON eo.event_id = e.id"
            " JOIN observations o ON o.id = eo.observation_id"
            " WHERE o.asset_id = ? AND e.status = 'active'"
            " AND COALESCE(e.source_type, '') != 'video_scene'",
            (video_id,),
        ).fetchall()
        events = list(scene_events) + list(generic_events)
        if not events:
            continue
        asset = connection.execute(
            "SELECT file_name, captured_at FROM assets WHERE id = ?", (video_id,)
        ).fetchone()
        file_name = str(asset["file_name"] if asset else video_id)
        captured_at = None
        if asset is not None and asset["captured_at"]:
            try:
                captured_at = datetime.fromisoformat(str(asset["captured_at"]))
            except ValueError:
                captured_at = None
        primary = scene_events[0] if scene_events else generic_events[0]
        sources = [str(event["id"]) for event in events if event["id"] != primary["id"]]
        summary = _description(connection, events, file_name)
        if (
            not sources
            and primary["title"]
            and primary["title"] not in {"待总结事件"}
            and not str(primary["title"]).startswith("视频 ·")
        ):
            # 已经是事件总结器产出的标题/摘要，重跑合并时不要退回逐帧拼接。
            summary = str(primary["summary"] or summary)
        transcript, segments = _speech(connection, events)
        starts = [event["time_start"] for event in events if event["time_start"]]
        ends = [event["time_end"] for event in events if event["time_end"]]
        duration = max([float(event["source_end_sec"] or 0.0) for event in events] or [0.0])
        places = [str(event["place"] or "").strip() for event in events]
        place = next((value for value in places if value not in PLACEHOLDER_PLACES), primary["place"] or "")
        merged_videos += 1
        moved_events += len(sources)
        if not apply:
            continue
        with connection:
            for source_id in sources:
                for table in ("event_observations", "event_participants", "event_entities"):
                    connection.execute(
                        f"UPDATE OR IGNORE {table} SET event_id = ? WHERE event_id = ?",
                        (primary["id"], source_id),
                    )
                    connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (source_id,))
                connection.execute(
                    "UPDATE person_moments SET event_id = ? WHERE event_id = ?",
                    (primary["id"], source_id),
                )
                connection.execute("DELETE FROM events WHERE id = ?", (source_id,))
            try:
                metadata = json.loads(primary["source_metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if transcript:
                metadata["speech_transcript"] = transcript
            if segments:
                metadata["speech_segments"] = segments
            title = str(primary["title"] or "").strip()
            if title == "" or title in {"待总结事件"} or title.startswith("视频 ·"):
                title = f"视频 · {Path(file_name).stem}"[:120]
            connection.execute(
                "UPDATE events SET title = ?, summary = ?, place = ?, time_start = ?, time_end = ?,"
                " source_type = 'video_scene', source_asset_id = ?, source_scene_index = 0,"
                " source_start_sec = 0, source_end_sec = ?, source_metadata_json = ?,"
                " revision = revision + 1, updated_at = ? WHERE id = ?",
                (
                    title,
                    summary,
                    place,
                    captured_at.isoformat() if captured_at else (min(starts) if starts else None),
                    (
                        (captured_at + timedelta(seconds=duration)).isoformat()
                        if captured_at
                        else (max(ends) if ends else None)
                    ),
                    video_id,
                    duration,
                    json.dumps(metadata, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    primary["id"],
                ),
            )
    connection.close()
    return merged_videos, moved_events


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
    backup_dir = BACKUP_ROOT / f"video-event-merge-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    total_videos = total_events = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        videos, moved = merge_database(
            path, apply=args.apply, backup=backup_dir / f"{path.parent.parent.name}.db"
        )
        total_videos += videos
        total_events += moved
        print(f"{path.parent.parent.name}: videos={videos} merged_events={moved}")
    print(
        f"{'APPLIED' if args.apply else 'DRY-RUN'}: videos={total_videos}"
        f" merged_events={total_events} backup={backup_dir if args.apply else '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
