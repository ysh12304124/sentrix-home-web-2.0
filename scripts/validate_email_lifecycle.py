"""Exercise real email HTTP import/correction/deletion in an isolated canary scope."""

import argparse
import json
import sqlite3
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    base = args.api.rstrip("/") + "/api/v1"
    scope = "email-lifecycle-canary-20261118"
    checks = {}

    def call(method, path, **kwargs):
        response = requests.request(method, base + path, timeout=120, **kwargs)
        response.raise_for_status()
        return response

    call("POST", "/memory-spaces", json={"id": scope, "name": "Email lifecycle canary", "kind": "benchmark"})
    item = {
        "id": "email209901010001",
        "timestamp": "2099-01-01 09:00:00",
        "short_summary": "Acceptance canary",
        "detail": "emailcanaryoldtoken original test body",
    }
    imported = call("POST", "/emails/import", json={"scope_id": scope, "items": [item]}).json()
    assert imported["status"] == "completed", imported
    checks["real_embedding_import"] = True
    with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as db:
        aid, oid, eid = db.execute(
            "SELECT asset_id,observation_id,event_id FROM email_import_state "
            "WHERE asset_id IN (SELECT id FROM assets WHERE scope_id=?)",
            (scope,),
        ).fetchone()
        raw_path = Path(db.execute("SELECT path FROM assets WHERE id=?", (aid,)).fetchone()[0])
    observation = call("GET", "/emails/" + aid).json()["observation"]
    corrected = call(
        "PATCH",
        "/memories/observation/" + oid,
        json={
            "revision": observation["revision"],
            "changes": {
                "caption": "Corrected acceptance canary",
                "text_content": "emailcanarynewtoken corrected body",
            },
            "reason": "isolated HTTP acceptance",
        },
    ).json()
    assert corrected["sync_status"] == "synced", corrected
    assert "emailcanarynewtoken" in call("GET", "/assets/" + aid + "/file").text
    assert "emailcanaryoldtoken" in call("GET", "/assets/" + aid + "/file?original=true").text
    checks["correction_and_canonical_file"] = True
    deleted = call("DELETE", "/assets/" + aid).json()
    assert deleted["deleted"] and not deleted.get("warnings"), deleted
    checks["http_delete"] = True
    with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as db:
        for table, column, value in (
            ("assets", "id", aid),
            ("observations", "id", oid),
            ("events", "id", eid),
            ("email_import_state", "asset_id", aid),
            ("observation_search_terms", "asset_id", aid),
            ("observation_search_fts", "asset_id", aid),
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (value,)).fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM text_vectors WHERE source_id IN (?,?)", (oid, eid)).fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT COUNT(*) FROM memory_graph_nodes WHERE scope_id=?", (scope,)).fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT COUNT(*) FROM memory_graph_edges WHERE scope_id=?", (scope,)).fetchone()[0]
            == 0
        )
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        checks["authority_fts_vectors_graph_and_fk_clean"] = True
        assert not raw_path.exists()
        checks["owned_file_removed"] = True
        checks["real_corpus_count_preserved"] = (
            db.execute(
                "SELECT COUNT(*) FROM observations WHERE source_type='email' "
                "AND scope_id='ATMEmail-20261118-validation'"
            ).fetchone()[0]
            == 6742
        )
        assert checks["real_corpus_count_preserved"]
    args.output.write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
