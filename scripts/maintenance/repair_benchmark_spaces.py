"""Reconcile space metadata using persisted run records and matching asset IDs.

Default is read-only. Apply takes a consistent SQLite backup and records the
old metadata before changing kind/include_in_people. Media and identities stay intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def plan_repairs(connection: sqlite3.Connection, results_root: Path) -> list[dict[str, Any]]:
    spaces = {
        row["id"]: dict(row)
        for row in connection.execute("SELECT id,kind,include_in_people,updated_at FROM memory_spaces")
    }
    verified: dict[str, dict[str, Any]] = {}
    for path in sorted(results_root.glob("*/run.json")):
        raw = path.read_bytes()
        record = json.loads(raw)
        scope = record.get("scope_id")
        if scope not in spaces or record.get("mode") not in {"full", "build"}:
            continue
        if record.get("status") in {"pending", "queued", "running"}:
            raise ValueError(f"run still active: {path.parent.name}")
        ids = record.get("pipeline_asset_ids") or []
        # A name prefix alone is never sufficient evidence.
        matched = sum(
            connection.execute("SELECT 1 FROM assets WHERE id=? AND scope_id=?", (asset_id, scope)).fetchone()
            is not None
            for asset_id in ids
        )
        if not matched:
            continue
        before = spaces[scope]
        if before["kind"] == "benchmark" and before["include_in_people"] == 0:
            continue
        if before["kind"] not in {"household", "benchmark"}:
            raise ValueError(f"unsupported existing kind for {scope}")
        verified[scope] = {
            "before": before,
            "run_path": str(path.resolve()),
            "run_sha256": hashlib.sha256(raw).hexdigest(),
            "matched_asset_ids": matched,
        }
    return [verified[scope] for scope in sorted(verified)]


def repair(database: Path, results_root: Path, *, apply: bool = False) -> dict[str, Any]:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode={'rw' if apply else 'ro'}", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    try:
        entries = plan_repairs(connection, results_root)
        report: dict[str, Any] = {"applied": False, "spaces": entries}
        if not apply or not entries:
            return report
        backup_root = database.parent / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = Path(tempfile.mkdtemp(prefix="benchmark-spaces-", dir=backup_root))
        with sqlite3.connect(backup_dir / "sentrix.db") as backup:
            connection.backup(backup)
        timestamp = datetime.now(UTC).isoformat()
        report.update(backup_directory=str(backup_dir.resolve()), updated_at=timestamp)
        audit = backup_dir / "audit.json"
        audit.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for entry in entries:
                before = entry["before"]
                changed = connection.execute(
                    "UPDATE memory_spaces SET kind='benchmark',include_in_people=0,updated_at=?"
                    " WHERE id=? AND kind=? AND include_in_people=? AND updated_at=?",
                    (
                        timestamp,
                        before["id"],
                        before["kind"],
                        before["include_in_people"],
                        before["updated_at"],
                    ),
                ).rowcount
                if changed != 1:
                    raise ValueError(f"space changed after verification: {before['id']}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        report["applied"] = True
        audit.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/sentrix.db"))
    parser.add_argument("--results-root", type=Path, default=Path("apps/photobench/results"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(repair(args.database, args.results_root, apply=args.apply), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
