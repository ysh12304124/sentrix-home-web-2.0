"""Real HTTP text lifecycle and pre-unification email preservation acceptance."""

import argparse
import json
import sqlite3
from pathlib import Path

import requests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", required=True)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--previous-database", type=Path, required=True)
    p.add_argument("--email-source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    base = args.api.rstrip("/") + "/api/v1"
    checks = {}

    def call(method, path, **kwargs):
        r = requests.request(method, base + path, timeout=120, **kwargs)
        r.raise_for_status()
        return r

    def connect(path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    scope = "TextLifecycleAcceptance-20261118"
    call("POST", "/memory-spaces", json={"id": scope, "name": "Unified text lifecycle", "kind": "benchmark"})
    payload = {
        "scope_id": scope,
        "items": [
            {
                "external_id": "text-lifecycle-001",
                "text": "originaltextcanary7143",
                "metadata": {"source_type": "note"},
            }
        ],
    }
    result = call("POST", "/text/import", json=payload).json()
    assert result["status"] == "completed", result
    aid = result["items"][0]["asset_id"]
    detail = call("GET", "/text/" + aid).json()
    assert detail["memory_type"] == "text" and detail["observation"]["source_type"] == "text"
    assert detail["observation"]["captured_at"] is None
    checks["common_import_and_unknown_time"] = True
    assert call("POST", "/text/import", json=payload).json()["skipped"] == 1
    checks["idempotence"] = True
    observation = detail["observation"]
    state = detail["index_state"]
    corrected = call(
        "PATCH",
        "/memories/observation/" + observation["id"],
        json={
            "revision": observation["revision"],
            "changes": {"caption": "Revised text record", "text_content": "updatedtextcanary9265"},
            "reason": "unified acceptance",
        },
    ).json()
    assert corrected["sync_status"] == "synced", corrected
    assert "updatedtextcanary9265" in call("GET", "/assets/" + aid + "/file").text
    assert "originaltextcanary7143" in call("GET", "/assets/" + aid + "/file?original=true").text
    checks["correction_canonical_body_and_reindex"] = True
    with connect(args.database) as db:
        path = Path(db.execute("SELECT path FROM assets WHERE id=?", (aid,)).fetchone()[0])
    deleted = call("DELETE", "/assets/" + aid).json()
    assert deleted["deleted"] and not deleted.get("warnings"), deleted
    assert not path.exists()
    with connect(args.database) as db:
        for table, column, value in (
            ("assets", "id", aid),
            ("observations", "id", observation["id"]),
            ("events", "id", state["event_id"]),
            ("text_import_state", "asset_id", aid),
            ("observation_search_terms", "asset_id", aid),
            ("observation_search_fts", "asset_id", aid),
        ):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", (value,)).fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM text_vectors WHERE source_id IN (?,?)",
                (observation["id"], state["event_id"]),
            ).fetchone()[0]
            == 0
        )
        for table in ("memory_graph_nodes", "memory_graph_edges"):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE scope_id=?", (scope,)).fetchone()[0] == 0
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    checks["owned_authority_indices_graph_and_file_deleted"] = True
    query = (
        "SELECT id,asset_id,external_id,caption,text_content,revision,captured_at FROM observations "
        "WHERE scope_id='ATMEmail-20261118-validation' AND external_id!='' ORDER BY id"
    )
    with connect(args.previous_database) as before, connect(args.database) as after:
        rows = before.execute(query).fetchall()
        assert len(rows) == 6742 and rows == after.execute(query).fetchall()
        assert (
            after.execute(
                "SELECT count(*) FROM observations WHERE source_type='text' "
                "AND scope_id='ATMEmail-20261118-validation'"
            ).fetchone()[0]
            == 6742
        )
        assert (
            after.execute(
                "SELECT count(*) FROM observations WHERE source_type='text' "
                "AND scope_id='ATMEmail-20261118-validation' "
                "AND json_extract(source_metadata_json,'$.source_type')='email'"
            ).fetchone()[0]
            == 6742
        )
    checks["all_6742_old_ids_body_revisions_and_times_preserved"] = True
    email = json.loads(args.email_source.read_text())[0]
    reused = call(
        "POST",
        "/emails/import",
        json={"scope_id": "ATMEmail-20261118-validation", "items": [email], "rebuild_graph": True},
    ).json()
    assert reused["skipped"] == 1 and reused["imported"] == 0, reused
    aid = reused["items"][0]["asset_id"]
    assert call("GET", "/emails/" + aid).json()["memory_type"] == "text"
    checks["legacy_email_endpoint_reuses_same_text_record"] = True
    args.output.write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
