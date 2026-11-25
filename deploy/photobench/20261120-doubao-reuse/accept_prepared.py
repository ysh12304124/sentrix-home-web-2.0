"""Independently read and accept the completed historical-memory copy."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
PREPARED = BASE / "doubao-reuse-49f347c"
HERE = Path(__file__).resolve().parent


def main():
    spec = importlib.util.spec_from_file_location("prepared", PREPARED / "prepare.py")
    prepared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepared)
    baseline = json.loads((PREPARED / "source-baseline.json").read_text())
    progress = json.loads((PREPARED / "progress.json").read_text())
    receipt = json.loads((PREPARED / "prepare-acceptance.json").read_text())
    assert progress["status"] == "completed" and progress["phase"] == "ready"
    assert not Path(f"/proc/{progress['worker_pid']}").exists()
    assert not receipt["postprocess_failures"]
    with prepared.connect(prepared.SOURCE) as source:
        assert prepared.fingerprints(source) == baseline["tables"]
    print("Original 14-table baseline verified", flush=True)
    metadata = {
        row["file_name"]: {k: v for k, v in row.items() if k != "file_name"}
        for row in map(json.loads, (prepared.PACKAGE / "media-metadata.jsonl").read_text().splitlines())
    }
    with prepared.connect(prepared.TARGET) as db:
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
        assert prepared.media_fingerprints(db) == baseline["media"] == receipt["media"]
        parents = dict(db.execute(
            "select id,file_name from assets where scope_id=? and media_type!='text' "
            "and parent_asset_id is null", (prepared.SCOPE,)
        ))
        address_count = 0
        for row in db.execute(
            "select id,file_name,parent_asset_id,metadata_json from assets "
            "where scope_id=? and media_type!='text'", (prepared.SCOPE,)
        ):
            name = parents[row["parent_asset_id"]] if row["parent_asset_id"] else row["file_name"]
            assert json.loads(row["metadata_json"])["source_metadata"] == metadata[name]
            address_count += 1
        assert len(parents) == 4292 and address_count == 6330
        emails = 0
        observation_ids = set()
        for row in db.execute(
            "select a.external_source_id,a.status,o.id,o.source_text from assets a "
            "join observations o on o.asset_id=a.id where a.scope_id=? and a.media_type='text'",
            (prepared.SCOPE,),
        ):
            assert row["status"] == "processed"
            assert row["source_text"] == (
                prepared.PACKAGE / "documents" / (row["external_source_id"] + ".txt")
            ).read_text()
            observation_ids.add(row["id"])
            emails += 1
        assert emails == len(observation_ids) == 6742
        covered = {space: {} for space in ("semantic", "episodic")}
        for row in db.execute(
            "select space,source_id,model_name,dimension,metadata_json from text_vectors "
            "where scope_id=?", (prepared.SCOPE,)
        ):
            extra = json.loads(row["metadata_json"])
            observation = extra.get("observation_id", row["source_id"].split(":chunk:")[0])
            if observation not in observation_ids:
                continue
            assert row["model_name"] == "tencent/WeMM-Embedding-2B" and row["dimension"] == 2048
            covered[row["space"]].setdefault(observation, []).append(extra)
        chunks = {}
        for space, rows in covered.items():
            assert set(rows) == observation_ids
            for entries in rows.values():
                expected = int(entries[0]["chunk_count"])
                assert len(entries) == expected
                assert {int(e["chunk_index"]) for e in entries} == set(range(expected))
            chunks[space] = sum(map(len, rows.values()))
        lexical = db.execute(
            "select count(distinct t.observation_id) from observation_search_terms t "
            "join observations o on o.id=t.observation_id join assets a on a.id=o.asset_id "
            "where a.scope_id=? and a.media_type='text'", (prepared.SCOPE,)
        ).fetchone()[0]
        assert lexical == 6742
        graph = [dict(r) for r in db.execute(
            "select * from memory_graph_builds where scope_id=?", (prepared.SCOPE,)
        )]
        assert graph
        postprocess = [dict(r) for r in db.execute(
            "select * from scope_postprocess where scope_id=?", (prepared.SCOPE,)
        )]
        assert not any(r["processing_generation"] is not None for r in postprocess)
        counts = {table: dict(db.execute(f"select status,count(*) from {table} group by status"))
                  for table in ("agent_turns", "import_jobs", "video_jobs")}
        assert not counts["agent_turns"].get("pending") and not counts["agent_turns"].get("running")
        pipelines = [dict(r) for r in db.execute(
            "select operation_id,scope_id,kind,status,started_at,finished_at from pipeline_receipts "
            "where status='running' or operation_id='141834a96b9c4c0989ececbf326c4981'"
        )]
    assert not prepared.api(8874, "active-work")["work"]
    imports = prepared.api(8294, "imports/progress")
    assert imports["library"]["processing"] == 0 and imports["postprocess"]["state"] == "idle"
    product_pid = prepared.process_pid()
    env = prepared.environment(product_pid)
    assert Path(env["SENTRIX_DATA_DIR"]) / "sentrix.db" == prepared.SOURCE
    result = {
        "status": "accepted", "observed_at": prepared.now(), "scope_id": prepared.SCOPE,
        "original_baseline_tables_unchanged": 14, "historical_media_unchanged": True,
        "media": baseline["media"], "original_media": len(parents),
        "address_metadata_assets": address_count, "emails": emails,
        "email_texts_match": True, "email_lexical_coverage": lexical,
        "email_vector_chunks": chunks, "email_vector_coverage": 6742,
        "memory_graph_builds": graph, "postprocess": postprocess,
        "postprocess_failures": receipt["postprocess_failures"],
        "copied_database_activity": counts, "historical_pipeline_receipts": pipelines,
        "product_pid": product_pid, "product_still_on_original_database": True,
        "preparation_worker_exited": True, "imports": imports,
        "disk_free": {p: shutil.disk_usage(p).free for p in ("/home", "/var/tmp")},
        "memory_available_kib": dict(
            line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
        )["MemAvailable"].strip(),
        "label": receipt["label"], "pid": os.getpid(),
    }
    with (HERE / "preparation-independent-acceptance.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
