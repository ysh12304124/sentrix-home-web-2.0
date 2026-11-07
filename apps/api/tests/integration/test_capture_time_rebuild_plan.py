import hashlib
import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

from sentrix.domain.media.models import GpsPoint


def rebuild_module():
    path = Path(__file__).resolve().parents[4] / "scripts/plan_capture_time_rebuild.py"
    spec = importlib.util.spec_from_file_location("capture_rebuild", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_database(tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"source-media")
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE assets (id TEXT, parent_asset_id TEXT, path TEXT, media_type TEXT,"
            " captured_at TEXT, source_timestamp_sec REAL)"
        )
        connection.execute("CREATE TABLE events (id TEXT)")
        connection.execute("INSERT INTO events VALUES ('preserve-event')")
        connection.executemany(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("photo", None, str(image), "image", "2018-04-03T10:00:00", None),
                ("frame", "photo", "unused", "image", "2018-04-03T10:00:12", 12.5),
                ("missing", None, str(tmp_path / "missing.jpg"), "image", None, None),
                ("orphan", "missing", "unused", "image", None, 1),
            ],
        )
    return database, image


def test_rebuild_plan_preserves_source_and_derives_frame_time(tmp_path, monkeypatch):
    module = rebuild_module()
    database, image = make_database(tmp_path)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (database, image)}
    monkeypatch.setattr(
        module.PillowMetadataReader,
        "read",
        lambda self, path: (datetime(2018, 4, 3, 9), GpsPoint(18.78, 99.00), "camera"),
    )
    result = module.plan(database)
    records = {row["asset_id"]: row for row in result["assets"]}
    assert result["read_only"] and result["existing_events"] == 1
    assert records["photo"]["after"] == "2018-04-03T09:00:00+07:00"
    assert records["frame"]["after"] == "2018-04-03T09:00:12.500000+07:00"
    assert records["frame"]["capture_time"] == records["photo"]["capture_time"]
    assert {error["asset_id"] for error in result["errors"]} == {"missing", "orphan"}
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}


def test_rebuild_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / "not-created.db"
    with pytest.raises(sqlite3.OperationalError):
        rebuild_module().plan(path)
    assert not path.exists()


@pytest.mark.parametrize("same_as_database", [False, True])
def test_rebuild_cli_refuses_existing_output(tmp_path, monkeypatch, same_as_database):
    database, _ = make_database(tmp_path)
    output = database if same_as_database else tmp_path / "existing.json"
    if not same_as_database:
        output.write_text("preserve")
    before = output.read_bytes()
    monkeypatch.setattr(sys, "argv", ["plan", "--database", str(database), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        rebuild_module().main()
    assert error.value.code == 2
    assert output.read_bytes() == before
