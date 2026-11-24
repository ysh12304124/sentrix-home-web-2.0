"""S8：H1 回填、H2 任务提交/进度/取消 HTTP 契约。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(client: TestClient) -> None:
    database = client.app.state.container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )


def test_h1_and_h2_actions(client: TestClient) -> None:
    _seed(client)
    dry = client.post("/api/v1/memory-builds/backfill", json={"scope_id": "album1", "dry_run": True})
    assert dry.status_code == 200
    assert dry.json()["counts"]["model_calls"] == 0

    submitted = client.post(
        "/api/v1/memory-builds/rebuild",
        json={"scope_id": "album1", "stages": ["face_binding"], "dry_run": False},
    )
    assert submitted.status_code == 200
    body = submitted.json()
    assert body["tasks"][0]["stage"] == "face_binding"

    tasks = client.get("/api/v1/memory-builds/tasks", params={"scope_id": "album1"})
    assert tasks.status_code == 200
    assert tasks.json()["counts"] == {"queued": 1}

    run = client.post("/api/v1/memory-builds/run", json={"limit": 5})
    assert run.status_code == 200
    assert run.json()["done"] == 1

    canceled = client.post("/api/v1/memory-builds/cancel", json={"scope_id": "album1"})
    assert canceled.status_code == 200
    assert canceled.json()["canceled"] == 0
