"""S6：导出快照创建、列表与固定文件下载契约。"""

from __future__ import annotations

import json
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
        for index, asset_id in enumerate(("asset_a", "asset_b"), start=1):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
                " captured_at, created_at, updated_at)"
                " VALUES (?, 'album1', ?, 'image', ?, 'processed', ?, ?, ?)",
                (
                    asset_id,
                    f"IMG_{index}.jpg",
                    f"/tmp/{asset_id}.jpg",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " caption, short_caption, tags_json, entities_json, revision, created_at, updated_at)"
                " VALUES (?, 'album1', ?, 'import', ?, '海边合影', '海边', '[]', '[]', 1, ?, ?)",
                (f"obs_{index}", asset_id, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
            )


def test_create_list_and_download_export(client: TestClient) -> None:
    _seed(client)
    created = client.post("/api/v1/memory-exports", json={"scope_id": "album1", "compat_mode": "strict_v3"})
    assert created.status_code == 201
    snapshot = created.json()
    assert snapshot["status"] == "ready"
    assert {item["name"] for item in snapshot["files"]} == {
        "image_memories.jsonl",
        "image_memory_provenance.jsonl",
        "export_manifest.json",
    }

    listed = client.get("/api/v1/memory-exports", params={"scope_id": "album1"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [snapshot["id"]]

    manifest = client.get(f"/api/v1/memory-exports/{snapshot['id']}/files/export_manifest.json")
    assert manifest.status_code == 200
    assert json.loads(manifest.text)["place_policy"] == "physical_location_only"
    records = client.get(f"/api/v1/memory-exports/{snapshot['id']}/files/image_memories.jsonl")
    assert records.status_code == 200
    assert len(records.text.splitlines()) == 2
    assert client.get(f"/api/v1/memory-exports/{snapshot['id']}/files/traces.jsonl").status_code == 409


def test_duplicate_stem_returns_blocked_snapshot(client: TestClient) -> None:
    _seed(client)
    database = client.app.state.container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status,"
            " created_at, updated_at)"
            " VALUES ('asset_c', 'album1', 'IMG_1.jpg', 'image', '/tmp/c.jpg', 'processed', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    response = client.post("/api/v1/memory-exports", json={"scope_id": "album1"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "blocked"
    assert body["files"] == []
    assert any(item.startswith("duplicate_id:IMG_1") for item in body["conflicts"])
