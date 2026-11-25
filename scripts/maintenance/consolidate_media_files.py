#!/usr/bin/env python3
"""将已登记的历史原件合并为硬链接；默认只报告，--apply 才修改文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sentrix.infrastructure.media.storage import FileSystemMediaStorage, sanitise


def _file_bytes(paths: list[Path]) -> int:
    unique = {(path.stat().st_dev, path.stat().st_ino): path.stat().st_size for path in paths}
    return sum(unique.values())


def consolidate(database: Path, media_dir: Path, *, apply: bool = False) -> dict[str, object]:
    root = media_dir.resolve(strict=True)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT id,file_name,path,content_sha256 FROM assets"
            " WHERE parent_asset_id IS NULL ORDER BY created_at,id"
        ).fetchall()
    managed: list[tuple[str, str, Path]] = []
    contents: dict[str, int] = {}
    missing: list[str] = []
    external: list[str] = []
    for asset_id, file_name, raw_path, expected in rows:
        path = Path(raw_path)
        if path.is_symlink() or path.resolve().parent != root:
            external.append(asset_id)
            continue
        if not path.is_file():
            missing.append(asset_id)
            continue
        if path.resolve() != root / f"{asset_id}_{sanitise(file_name)}":
            raise ValueError(f"asset path does not match its managed name: {asset_id}")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if expected and digest != expected:
            raise ValueError(f"stored media SHA256 mismatch: {asset_id}")
        contents[digest] = path.stat().st_size
        managed.append((asset_id, file_name, path))
    paths = [path for _, _, path in managed]
    before = _file_bytes(paths)
    if apply:
        storage = FileSystemMediaStorage(root)
        for asset_id, file_name, path in managed:
            storage.copy_from_path(asset_id, file_name, path)
    after = _file_bytes(paths)
    return {
        "mode": "apply" if apply else "dry_run",
        "managed_assets": len(managed),
        "unique_contents": len(contents),
        "original_file_bytes_before": before,
        "original_file_bytes_after": after,
        "reclaimable_file_bytes": before - sum(contents.values()),
        "reclaimed_file_bytes": before - after,
        "missing_asset_ids": missing,
        "external_asset_ids": external,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="合并已有原件；默认仅核查")
    args = parser.parse_args()
    report = consolidate(args.database, args.media_dir, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
