"""Read-only compatibility evidence before reusing the historical Doubao memory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

DB = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
OLD = "PhotoBench-20261112-174516-atm-bench-media-current"
QWEN = "PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current"
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    with sqlite3.connect("file:" + DB + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma query_only=on")
        tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
        schema = {t: [dict(r) for r in db.execute(f'pragma table_info("{t}")')] for t in tables}
        scope_counts = {}
        for scope in (OLD, QWEN):
            scope_counts[scope] = {}
            for table, columns in schema.items():
                if "scope_id" in {c["name"] for c in columns}:
                    scope_counts[scope][table] = db.execute(
                        f'select count(*) from "{table}" where scope_id=?', (scope,)
                    ).fetchone()[0]
        models = {}
        for scope in (OLD, QWEN):
            models[scope] = {}
            for table in ("memory_vectors", "text_vectors"):
                models[scope][table] = [
                    dict(r)
                    for r in db.execute(
                        f"select model_name,dimension,space,source_type,count(*) AS count from {table} "
                        "where scope_id=? group by model_name,dimension,space,source_type",
                        (scope,),
                    )
                ]
        manifest = json.loads((PACKAGE / "manifest.json").read_text())
        expected = {Path(n).name: PACKAGE / n for n in manifest["photos"] + manifest["videos"]}
        media = list(
            db.execute(
                "select id,file_name,path,content_sha256,metadata_json from assets "
                "where scope_id=? and parent_asset_id is null order by id",
                (OLD,),
            )
        )
        comparisons = Counter()
        differences = []
        for row in media:
            old_path = Path(row["path"])
            new_path = expected[row["file_name"]]
            a, b = sha(old_path), sha(new_path)
            comparisons["files"] += 1
            comparisons["stored_sha_matches_file"] += a == row["content_sha256"]
            comparisons["exact_content_matches"] += a == b
            if a != b:
                differences.append(
                    {"id": row["id"], "file_name": row["file_name"], "old_sha256": a, "new_sha256": b}
                )
        receipts = [
            dict(r)
            for r in db.execute(
                "select operation_id,scope_id,kind,status,started_at,finished_at,error "
                "from pipeline_receipts where scope_id in (?,?)",
                (OLD, QWEN),
            )
        ]
        metadata = {}
        for scope in (OLD, QWEN):
            metadata[scope] = [
                dict(r)
                for r in db.execute(
                    "select id,file_name,metadata_json from assets where scope_id=? "
                    "and parent_asset_id is null and media_type!='text' order by file_name limit 2",
                    (scope,),
                )
            ]
    print(
        json.dumps(
            {
                "read_only": True,
                "database": DB,
                "schema": schema,
                "scope_counts": scope_counts,
                "embedding_models": models,
                "content_comparison": dict(comparisons),
                "different_files": differences,
                "pipeline_receipts": receipts,
                "metadata_samples": metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
