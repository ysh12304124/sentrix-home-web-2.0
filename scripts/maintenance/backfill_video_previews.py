"""给浏览器不能直接播放的视频补 H.264 MP4 预览件。

旧规则只看 codec：H.264 的 QuickTime `.mov` 不转码，手机浏览器拿到的原片
MIME/容器不受支持，表现为视频「失效」。这里按「H.264 + MP4/M4V 容器」才直出，
其余补转码。默认 dry-run，``--apply`` 才写并先备份整库。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sentrix.infrastructure.media.video_transcode import (
    BROWSER_VIDEO_SUFFIXES,
    H264_CODECS,
    FfmpegVideoTranscoder,
)

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"


def needs_preview(codec: str, suffix: str) -> bool:
    return codec.lower() not in H264_CODECS or suffix.lower() not in BROWSER_VIDEO_SUFFIXES


def process_database(path: Path, *, apply: bool, backup: Path) -> tuple[int, int]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    if apply:
        destination = sqlite3.connect(backup)
        connection.backup(destination)
        destination.close()
    rows = connection.execute(
        "SELECT id, file_name, path, metadata_json FROM assets WHERE media_type = 'video'"
    ).fetchall()
    transcoded = skipped = 0
    for row in rows:
        asset_id = str(row["id"])
        source = Path(str(row["path"] or ""))
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        video = metadata.get("video_metadata") or {}
        codec = str(video.get("codec") or "")
        if not source.is_file() or not needs_preview(codec, source.suffix):
            skipped += 1
            continue
        existing = str(metadata.get("browser_preview_path") or "")
        if existing and Path(existing).is_file():
            skipped += 1
            continue
        if not apply:
            print(f"  NEED {asset_id} {row['file_name']} codec={codec or '?'} suffix={source.suffix}")
            transcoded += 1
            continue
        try:
            target = FfmpegVideoTranscoder(source.parent).browser_preview(
                asset_id, source, codec
            )
            if target is None:
                skipped += 1
                continue
            metadata["browser_preview_path"] = str(target)
            metadata["browser_preview_error"] = ""
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    asset_id,
                ),
            )
            transcoded += 1
        except Exception as error:  # noqa: BLE001 - 单个视频失败不阻断其余
            metadata["browser_preview_error"] = f"{type(error).__name__}: {error}"
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    asset_id,
                ),
            )
            print(f"  FAIL {asset_id}: {error}", file=sys.stderr)
    if apply:
        connection.commit()
    connection.close()
    return transcoded, skipped


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
    backup_dir = BACKUP_ROOT / f"video-preview-backfill-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    total = skipped_all = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        transcoded, skipped = process_database(
            path, apply=args.apply, backup=backup_dir / f"{path.parent.parent.name}.db"
        )
        total += transcoded
        skipped_all += skipped
        print(f"{path.parent.parent.name}: transcoded={transcoded} skipped={skipped}")
    print(
        f"{'APPLIED' if args.apply else 'DRY-RUN'}: transcoded={total} skipped={skipped_all}"
        f" backup={backup_dir if args.apply else '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
