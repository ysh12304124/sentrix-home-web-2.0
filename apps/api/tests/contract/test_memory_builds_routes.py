"""S0 构建状态读取端点：空/legacy 列表与构建详情。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.domain.media.models import ObservationBuild
from sentrix.infrastructure.persistence.database import Database

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(database: Database) -> None:
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, caption, short_caption,"
            " detail_caption, tags_json, entities_json, field_states_json, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'vlm', '海边', '一家人在海边', '一家三口沿着海边走。',"
            ' \'["海边"]\', \'[{"name":"海边","type":"place"}]\', \'{"caption":"present"}\', ?, ?)',
            (NOW.isoformat(), NOW.isoformat()),
        )


def test_inventory_lists_legacy_partial(client: TestClient) -> None:
    _seed(client.app.state.container.database)
    response = client.get("/api/v1/memory-builds", params={"scope_id": "album1"})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["total"] == 1
    assert body["assets"][0]["status"] == "legacy_partial"
    assert body["assets"][0]["available_fields"] == ["caption"]


def test_unknown_build_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/memory-builds/missing").status_code == 404


def test_asset_memories_exposes_s1_fields_and_accepts_corrections(client: TestClient) -> None:
    _seed(client.app.state.container.database)
    response = client.get("/api/v1/assets/asset_a/memories")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["short_caption"] == "一家人在海边"
    assert item["detail_caption"] == "一家三口沿着海边走。"
    assert item["tags"] == ["海边"]
    assert item["entities"][0]["name"] == "海边"
    assert item["field_states"] == {"caption": "present"}

    corrected = client.patch(
        "/api/v1/memories/observation/obs_a",
        json={
            "revision": item["revision"],
            "changes": {
                "detail_caption": "用户修正后的详细描述",
                "tags": ["新标签"],
                "entities": [{"name": "海边", "type": "place"}],
            },
            "reason": "test correction",
        },
    )
    assert corrected.status_code == 200
    after = client.get("/api/v1/assets/asset_a/memories").json()["items"][0]
    assert after["detail_caption"] == "用户修正后的详细描述"
    assert after["tags"] == ["新标签"]
    assert after["entities"][0]["entity_type"] == "place"


def test_build_detail_exposes_provenance(client: TestClient) -> None:
    database = client.app.state.container.database
    _seed(database)
    build, _ = client.app.state.container.memory_builds.create_or_get(
        ObservationBuild(
            id="build_a",
            scope_id="album1",
            asset_id="asset_a",
            observation_id="obs_a",
            stage="observation",
            status="succeeded",
            build_key="key-a",
            model_name="qwen3.8-flash-next-iq3_xxs",
            protocol_version="image-observation-v1",
        )
    )
    response = client.get(f"/api/v1/memory-builds/{build.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["build"]["model_name"] == "qwen3.8-flash-next-iq3_xxs"
    assert body["build"]["protocol_version"] == "image-observation-v1"
    assert body["evidence"] == []
