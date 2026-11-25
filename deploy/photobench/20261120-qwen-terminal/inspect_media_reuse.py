"""Inventory the explicitly authorized old Doubao media scope without changing it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DATABASE = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
OLD = "PhotoBench-20261112-174516-atm-bench-media-current"
QWEN = "PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current"
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses")


def main():
    with sqlite3.connect("file:" + DATABASE + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma query_only=on")
        tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
        selected = [
            t
            for t in tables
            if any(
                s in t for s in ("asset", "scope", "memory", "source", "embedding", "pipeline", "document")
            )
        ]
        schema = {t: [r[1] for r in db.execute(f'pragma table_info("{t}")')] for t in selected}
        summaries = {}
        for scope in (OLD, QWEN):
            records = [
                dict(r)
                for r in db.execute(
                    "select id,file_name,media_type,status,path from assets "
                    "where scope_id=? and parent_asset_id is null order by id",
                    (scope,),
                )
            ]
            names = {r["file_name"] for r in records}
            manifest = json.loads((PACKAGE / "manifest.json").read_text())
            expected = {Path(n).name for n in manifest["photos"] + manifest["videos"]}
            counts = [
                dict(r)
                for r in db.execute(
                    "select media_type,status,count(*) AS count from assets "
                    "where scope_id=? group by media_type,status",
                    (scope,),
                )
            ]
            scope_tables = {}
            for table, columns in schema.items():
                if "scope_id" not in columns:
                    continue
                if "status" in columns:
                    value = [
                        list(r)
                        for r in db.execute(
                            f'select status,count(*) from "{table}" where scope_id=? group by status',
                            (scope,),
                        )
                    ]
                else:
                    value = db.execute(
                        f'select count(*) from "{table}" where scope_id=?', (scope,)
                    ).fetchone()[0]
                scope_tables[table] = value
            summaries[scope] = {
                "original_assets": len(records),
                "counts_including_derived": counts,
                "expected_media": len(expected),
                "missing_media_names": sorted(expected - names),
                "extra_original_names": sorted(names - expected),
                "original_asset_identity_sha256": hashlib.sha256(
                    json.dumps(records, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
                "scope_table_counts": scope_tables,
            }
        active = [
            dict(r)
            for r in db.execute(
                "select id,scope_id,status from agent_turns where status in ('pending','running')"
            )
        ]
    print(
        json.dumps(
            {
                "observed_at": datetime.now().astimezone().isoformat(),
                "read_only": True,
                "schema": schema,
                "scopes": summaries,
                "active_product_turns": active,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
