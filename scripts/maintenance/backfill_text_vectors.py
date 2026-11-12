"""把误写进 memory_vectors 的 semantic/episodic 行复制到 text_vectors。

导入侧曾把文本向量写进视觉表，而 RetrieveTextAnn 只读 text_vectors；结果是导入
成功、文本检索永远为空。默认 dry-run，``--apply`` 才写并先备份整库。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"
COLUMNS = (
    "id, scope_id, space, source_type, source_id, model_name, dimension,"
    " vector_json, metadata_json, created_at, updated_at"
)


def copy_rows(path: Path, *, apply: bool, backup: Path) -> tuple[int, int]:
    connection = sqlite3.connect(path)
    if apply:
        destination = sqlite3.connect(backup)
        connection.backup(destination)
        destination.close()
    before = connection.execute("SELECT COUNT(*) FROM text_vectors").fetchone()[0]
    source = connection.execute(
        "SELECT COUNT(*) FROM memory_vectors WHERE space IN ('semantic', 'episodic')"
    ).fetchone()[0]
    if apply:
        connection.execute(
            f"INSERT OR IGNORE INTO text_vectors ({COLUMNS})"
            f" SELECT {COLUMNS} FROM memory_vectors"
            " WHERE space IN ('semantic', 'episodic')"
        )
        connection.commit()
    after = connection.execute("SELECT COUNT(*) FROM text_vectors").fetchone()[0]
    connection.close()
    return source, after - before


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
    backup_dir = BACKUP_ROOT / f"text-vectors-backfill-{stamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    total_src = total_added = 0
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        source, added = copy_rows(
            path, apply=args.apply, backup=backup_dir / f"{path.parent.parent.name}.db"
        )
        total_src += source
        total_added += added
        print(f"{path.parent.parent.name}: source={source} added={added}")
    print(
        f"{'APPLIED' if args.apply else 'DRY-RUN'}: source={total_src} added={total_added}"
        f" backup={backup_dir if args.apply else '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
