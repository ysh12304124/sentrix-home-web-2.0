import json
import runpy
import sqlite3
from pathlib import Path

import pytest

repair = runpy.run_path(
    str(Path(__file__).resolve().parents[4] / "scripts/maintenance/repair_benchmark_spaces.py")
)["repair"]


@pytest.fixture
def repair_data(tmp_path):
    database = tmp_path / "sentrix.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE memory_spaces(id TEXT PRIMARY KEY,kind TEXT,include_in_people INT,updated_at TEXT);"
            "CREATE TABLE assets(id TEXT PRIMARY KEY,scope_id TEXT);"
            "INSERT INTO memory_spaces VALUES('verified','household',1,'old');"
            "INSERT INTO memory_spaces VALUES('PhotoBench-name-only','household',1,'old');"
            "INSERT INTO memory_spaces VALUES('home-default','household',1,'old');"
            "INSERT INTO assets VALUES('asset-test','verified');"
        )
    results = tmp_path / "results"
    run = results / "run1"
    run.mkdir(parents=True)
    record = {
        "scope_id": "verified",
        "mode": "full",
        "status": "completed",
        "pipeline_asset_ids": ["asset-test"],
    }
    (run / "run.json").write_text(json.dumps(record))
    return database, results, record


def test_repair_requires_run_and_asset_evidence_and_preserves_data(repair_data):
    database, results, _ = repair_data
    planned = repair(database, results)
    assert not planned["applied"]
    assert [entry["before"]["id"] for entry in planned["spaces"]] == ["verified"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT include_in_people FROM memory_spaces WHERE id='verified'"
        ).fetchone() == (1,)
    applied = repair(database, results, apply=True)
    assert applied["applied"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT kind,include_in_people FROM memory_spaces WHERE id='verified'"
        ).fetchone() == ("benchmark", 0)
        assert connection.execute("SELECT count(*) FROM assets").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM memory_spaces WHERE kind='household'").fetchone() == (
            2,
        )
    with sqlite3.connect(applied["backup_directory"] + "/sentrix.db") as backup:
        assert backup.execute(
            "SELECT kind,include_in_people FROM memory_spaces WHERE id='verified'"
        ).fetchone() == ("household", 1)
    assert repair(database, results, apply=True)["spaces"] == []


def test_unmatched_run_record_is_not_sufficient_evidence(repair_data):
    database, results, record = repair_data
    record["scope_id"] = "home-default"
    (results / "run1/run.json").write_text(json.dumps(record))
    assert repair(database, results)["spaces"] == []


def test_active_run_prevents_repair(repair_data):
    database, results, record = repair_data
    record["status"] = "running"
    (results / "run1/run.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="run still active"):
        repair(database, results, apply=True)
