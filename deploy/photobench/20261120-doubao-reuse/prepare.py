"""Prepare an independent historical Doubao memory copy; never rerun media inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
HERE = Path(__file__).resolve().parent
SOURCE = Path("/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db")
TARGET = HERE / "product-data/sentrix.db"
SCOPE = "PhotoBench-20261112-174516-atm-bench-media-current"
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses")
PRODUCT = BASE / "conversation-delete-9363d4b-v2/product/apps/api/src"
UNIT = "sentrix-atm100-doubao-api.service"
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
BASELINE_TABLES = (
    "assets",
    "observations",
    "memory_vectors",
    "text_vectors",
    "asset_places",
    "events",
    "facts",
    "entities",
    "relationships",
    "memory_graph_nodes",
    "memory_graph_edges",
    "memory_graph_builds",
    "observation_search_terms",
    "face_clusters",
)


def now():
    return datetime.now().astimezone().isoformat()


def write(name, data, *, exclusive=False):
    path = HERE / name
    if exclusive:
        with path.open("x") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    else:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.replace(path)


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=30) as response:
        return json.load(response)


def process_pid():
    return int(
        subprocess.check_output(["systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"], text=True)
    )


def environment(pid):
    return dict(
        p.split("=", 1) for p in Path(f"/proc/{pid}/environ").read_bytes().decode().split("\0") if "=" in p
    )


def connect(path):
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("pragma query_only=on")
    return db


def fingerprints(db):
    output = {}
    for table in BASELINE_TABLES:
        columns = list(db.execute(f'pragma table_info("{table}")'))
        keys = [r["name"] for r in sorted(columns, key=lambda r: r["pk"]) if r["pk"]]
        if not keys:
            keys = [r["name"] for r in columns]
        order = ",".join('"' + key + '"' for key in keys)
        digest = hashlib.sha256()
        count = 0
        for row in db.execute(f'select * from "{table}" where scope_id=? order by {order}', (SCOPE,)):
            digest.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode() + b"\n")
            count += 1
        output[table] = {"count": count, "sha256": digest.hexdigest()}
    return output


def media_fingerprints(db):
    queries = {
        "asset_payloads": "select id,file_name,path,media_type,status,content_sha256,parent_asset_id,"
        "derived_kind,source_timestamp_sec,source_frame_index,source_scene_index,"
        "captured_at,gps_latitude,gps_longitude,capture_time_json from assets "
        "where scope_id=? and media_type!='text' order by id",
        "media_observations": "select o.* from observations o join assets a on a.id=o.asset_id "
        "where o.scope_id=? and a.media_type!='text' order by o.id",
        "memory_vectors": "select * from memory_vectors where scope_id=? order by id",
    }
    result = {}
    for key, query in queries.items():
        digest = hashlib.sha256()
        count = 0
        for row in db.execute(query, (SCOPE,)):
            digest.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode() + b"\n")
            count += 1
        result[key] = {"count": count, "sha256": digest.hexdigest()}
    return result


def preflight():
    from sentrix.application.media.documents import parse_document_package

    records = parse_document_package(
        (PACKAGE / "originals/emails.json").read_bytes(), "emails.json", "atm_email"
    )
    assert len(records) == len({r.external_source_id for r in records}) == 6742
    for record in records:
        assert (PACKAGE / "documents" / (record.external_source_id + ".txt")).read_text() == record.text
    with connect(SOURCE) as db:
        assert (
            db.execute(
                "select count(*) from assets where scope_id=? and media_type='text'", (SCOPE,)
            ).fetchone()[0]
            == 0
        )
        models = [
            list(r)
            for r in db.execute(
                "select distinct model_name,dimension from text_vectors where scope_id=?", (SCOPE,)
            )
        ]
        assert models == [["tencent/WeMM-Embedding-2B", 2048]], models
    assert not api(8874, "active-work")["work"]
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert "doubao" not in mapping, "Inspect an existing Doubao mapping before any launch"
    result = {
        "status": "verified",
        "emails": len(records),
        "email_texts_match_package": True,
        "text_embedding_models": models,
        "source_database": str(SOURCE),
        "source_database_bytes": SOURCE.stat().st_size,
        "independent_volume_free": shutil.disk_usage(HERE).free,
        "target_absent": not TARGET.exists(),
        "observed_at": now(),
    }
    write("preflight.json", result, exclusive=True)
    print(json.dumps(result), flush=True)


def launch(wait_id):
    assert not (HERE / "launch-intent.json").exists(), "Consumed launch; inspect its exact worker."
    assert not TARGET.exists()
    assert json.loads((HERE / "preflight.json").read_text())["status"] == "verified"
    assert not api(8874, "active-work")["work"]
    imports = api(8294, "imports/progress")
    assert imports["library"]["processing"] == 0 and imports["postprocess"]["state"] == "idle"
    acceptance = json.loads((BASE / "qwen-terminal-00db192/media-payload-acceptance.json").read_text())
    assert acceptance["counts"] == {"image": 3759, "image_matches": 3759, "video": 533, "video_matches": 533}
    assert not acceptance["mismatches"]
    model = json.load(urllib.request.urlopen("http://127.0.0.1:18107/v1/models", timeout=30))
    assert {m["id"] for m in model["data"]} == {"doubao-seed-2.1-lite"}
    pid = process_pid()
    env = environment(pid)
    assert Path(env["SENTRIX_DATA_DIR"]) / "sentrix.db" == SOURCE
    args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    assert args[args.index("--app-dir") + 1] == str(PRODUCT)
    with connect(SOURCE) as db:
        assert not db.execute("select id from agent_turns where status in ('pending','running')").fetchall()
    assert shutil.disk_usage(HERE).free > SOURCE.stat().st_size + 3 * 1024**3
    env.update(
        SENTRIX_DATA_DIR=str(TARGET.parent),
        SENTRIX_VLM_BASE_URL="http://127.0.0.1:18107/v1",
        SENTRIX_VLM_REASONING_CONTEXT_TOKENS="16384",
        PYTHONPATH=str(PRODUCT),
    )
    write(
        "launch-intent.json",
        {
            "wait_id": wait_id,
            "created_at": now(),
            "source_database": str(SOURCE),
            "target_database": str(TARGET),
            "scope_id": SCOPE,
            "source_product_pid": pid,
            "source_app_dir": str(PRODUCT),
            "model": "doubao-seed-2.1-lite",
            "media_payload_acceptance_sha256": hashlib.sha256(
                json.dumps(acceptance, sort_keys=True).encode()
            ).hexdigest(),
            "historical_media_reuse": True,
            "media_vlm_rerun": False,
        },
        exclusive=True,
    )
    with (HERE / "prepare.log").open("xb") as log:
        worker = subprocess.Popen(
            [PYTHON, str(Path(__file__).resolve()), "work"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    response = {
        "worker_pid": worker.pid,
        "started_at": now(),
        "wait_id": wait_id,
        "source_product_pid": pid,
        "scope_id": SCOPE,
        "target_database": str(TARGET),
    }
    write("launch-response.json", response, exclusive=True)
    print(json.dumps(response), flush=True)


def work():
    state = {
        "status": "running",
        "phase": "snapshot",
        "worker_pid": os.getpid(),
        "started_at": now(),
        "scope_id": SCOPE,
        "completed_emails": 0,
        "total_emails": 6742,
        "media_vlm_rerun": False,
    }

    def progress(phase, **fields):
        state.update(phase=phase, updated_at=now(), **fields)
        write("progress.json", state)
        assert shutil.disk_usage(HERE).free > 1536 * 1024**2, (
            "Less than 1.5GiB free in independent copy volume"
        )

    try:
        assert os.environ["SENTRIX_DATA_DIR"] == str(TARGET.parent)
        TARGET.parent.mkdir()
        progress("snapshot")
        with connect(SOURCE) as source, sqlite3.connect(TARGET) as target:
            source.backup(target, pages=512)
        with connect(TARGET) as db:
            assert db.execute("pragma quick_check").fetchone()[0] == "ok"
            baseline = fingerprints(db)
            visual_before = media_fingerprints(db)
            assets = [
                dict(r)
                for r in db.execute(
                    "select id,file_name,parent_asset_id,metadata_json from assets "
                    "where scope_id=? and media_type!='text' order by parent_asset_id,id",
                    (SCOPE,),
                )
            ]
        with connect(SOURCE) as source:
            assert fingerprints(source) == baseline
        write("source-baseline.json", {"tables": baseline, "media": visual_before}, exclusive=True)
        from sentrix.application.media.documents import parse_document_package
        from sentrix.application.media.index_place import IndexAssetPlace
        from sentrix.bootstrap.container import Container
        from sentrix.bootstrap.import_wiring import build_import_assets, build_media_postprocess
        from sentrix.config import Settings
        from sentrix.runtime.work import import_work

        settings = Settings()
        assert settings.database_path == TARGET
        assert settings.embedding_scheme == "wemm"
        assert [
            getattr(settings, "reasoning_max_" + k + "_calls")
            for k in ("model", "tool", "search", "other_tool")
        ] == [100, 600, 100, 500]
        container = Container.build(settings)
        source_rows = {
            r["file_name"]: r
            for r in (
                json.loads(line)
                for line in (PACKAGE / "media-metadata.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        parent_names = {a["id"]: a["file_name"] for a in assets if a["parent_asset_id"] is None}
        assert len(parent_names) == 4292 and len(source_rows) == 4292
        progress("addresses", original_media=4292, assets_including_historical_frames=len(assets))
        with sqlite3.connect(TARGET) as db:
            for asset in assets:
                source_name = (
                    parent_names[asset["parent_asset_id"]] if asset["parent_asset_id"] else asset["file_name"]
                )
                source = {k: v for k, v in source_rows[source_name].items() if k != "file_name"}
                metadata = json.loads(asset["metadata_json"] or "{}")
                metadata["source_metadata"] = source
                db.execute(
                    "update assets set metadata_json=? where id=?",
                    (json.dumps(metadata, ensure_ascii=False), asset["id"]),
                )
        index_place = IndexAssetPlace(
            resolver=container.place_resolver, places=container.asset_place_repository
        )
        for index, record in enumerate(assets, 1):
            asset = container.asset_repository.get(record["id"])
            index_place.index(asset)
            for observation in container.observation_repository.list_by_asset(asset.id):
                container.lexical_index.refresh_from_observation(observation)
            if index % 250 == 0:
                progress("addresses", address_assets_completed=index)
        records = parse_document_package(
            (PACKAGE / "originals/emails.json").read_bytes(), "emails.json", "atm_email"
        )
        assert len(records) == len({r.external_source_id for r in records}) == 6742
        manifest = json.loads((PACKAGE / "manifest.json").read_text())
        assert {r.external_source_id + ".txt" for r in records} == {
            Path(p).name for p in manifest["documents"]
        }
        importer = build_import_assets(container, observe=False)
        progress("emails", address_assets_completed=len(assets))
        for index, record in enumerate(records, 1):
            assert (PACKAGE / "documents" / (record.external_source_id + ".txt")).read_text() == record.text
            with import_work():
                result = importer.execute(record.import_request(SCOPE))
            assert result.indexed, f"Email was not indexed: {record.external_source_id}"
            if index % 10 == 0 or index == len(records):
                progress("emails", completed_emails=index)
        progress("postprocess", completed_emails=6742)
        postprocess = build_media_postprocess(container)
        failures = postprocess(SCOPE, 0)
        assert not failures, failures
        progress("verification")
        with connect(TARGET) as db:
            assert media_fingerprints(db) == visual_before, (
                "Historical media payload/understanding/visual vectors changed"
            )
            rows = list(
                db.execute(
                    "select a.external_source_id,o.source_text,a.status from assets a "
                    "join observations o on o.asset_id=a.id "
                    "where a.scope_id=? and a.media_type='text'",
                    (SCOPE,),
                )
            )
            actual = {r["external_source_id"]: r for r in rows}
            assert len(actual) == len(rows) == 6742
            for record in records:
                assert actual[record.external_source_id]["source_text"] == record.text
                assert actual[record.external_source_id]["status"] == "processed"
            coverage = db.execute(
                "select count(*) from observations o join assets a on a.id=o.asset_id "
                "where o.scope_id=? and a.media_type='text' and not exists("
                "select 1 from text_vectors v where v.scope_id=o.scope_id "
                "and v.source_id=o.id and v.space='semantic')",
                (SCOPE,),
            ).fetchone()[0]
            assert coverage == 0, coverage
            assert db.execute("pragma quick_check").fetchone()[0] == "ok"
            models = [
                list(r)
                for r in db.execute(
                    "select model_name,dimension,count(*) from text_vectors "
                    "where scope_id=? group by model_name,dimension",
                    (SCOPE,),
                )
            ]
        with connect(SOURCE) as source:
            assert fingerprints(source) == baseline, "Original baseline changed during preparation"
        write(
            "prepare-acceptance.json",
            {
                "scope_id": SCOPE,
                "database": str(TARGET),
                "old_baseline_unchanged": True,
                "historical_media_unchanged": True,
                "media": visual_before,
                "emails": 6742,
                "missing_email_vector_coverage": coverage,
                "text_embedding_models": models,
                "postprocess_failures": failures,
                "label": "历史豆包媒体记忆复用+新版邮件增量",
                "finished_at": now(),
            },
            exclusive=True,
        )
        progress("ready", status="completed", finished_at=now())
    except Exception as error:
        state.update(
            status="failed", error=f"{type(error).__name__}: {error}", finished_at=now(), updated_at=now()
        )
        write("progress.json", state)
        traceback.print_exc()
        raise


def probe():
    launch = json.loads((HERE / "launch-response.json").read_text())
    pid = launch["worker_pid"]
    cmd = Path(f"/proc/{pid}/cmdline")
    alive = (
        cmd.exists()
        and str(Path(__file__).resolve()).encode() in cmd.read_bytes()
        and b"work" in cmd.read_bytes().split(b"\0")
    )
    state = (
        json.loads((HERE / "progress.json").read_text())
        if (HERE / "progress.json").exists()
        else {"status": "starting"}
    )
    state.update(
        worker_alive=alive,
        worker_pid=pid,
        disk_free={p: shutil.disk_usage(p).free for p in ("/home", "/var/tmp")},
    )
    print(json.dumps(state, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["preflight", "launch", "work", "probe"])
    parser.add_argument("--wait-id")
    args = parser.parse_args()
    if args.operation == "preflight":
        preflight()
    elif args.operation == "launch":
        assert args.wait_id
        launch(args.wait_id)
    elif args.operation == "work":
        work()
    else:
        probe()
