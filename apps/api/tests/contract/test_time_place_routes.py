"""S1b：资产时地投影与候选证据的 HTTP 契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.domain.media.models import GpsPoint, MemoryEvidence
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed(client: TestClient) -> str:
    container = client.app.state.container
    database = container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', 'album1', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, status, captured_at,"
            " gps_latitude, gps_longitude, capture_time_json, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?, ?, ?,"
            " ?, ?)",
            (
                "2024-01-01T23:30:00+08:00",
                30.0,
                120.0,
                json.dumps(
                    {
                        "raw": "2024:01:01 23:30:00",
                        "source": "exif",
                        "timezone": "",
                        "status": "offset_only",
                        "precision": "datetime",
                        "timezone_source": "exif_offset",
                    }
                ),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    container.asset_place_repository.save(
        asset_id="asset_a",
        scope_id="album1",
        gps=GpsPoint(30.0, 120.0),
        place=Place(
            admin=AdminArea(province="浙江省", city="杭州市"),
            nearest=NearestPlace(),
            precision=PlacePrecision.CITY,
            source=PlaceSource.PYGEOCN,
        ),
    )
    evidence = SqliteMemoryEvidenceRepository(database)
    evidence.add(
        MemoryEvidence(
            id="ev_time",
            scope_id="album1",
            owner_kind="asset",
            owner_id="asset_a",
            field_path="asset.capture_time",
            role="capture_time",
            value={"precision": "datetime", "timezone_source": "exif_offset"},
            source_kind="metadata",
            source_channel="exif",
        )
    )
    evidence.add(
        MemoryEvidence(
            id="ev_addr_embedded",
            scope_id="album1",
            owner_kind="asset",
            owner_id="asset_a",
            field_path="asset.place.address",
            role="address",
            value={"address": "嵌入地址"},
            source_kind="embedded",
            source_channel="file_metadata",
            status="conflict",
        )
    )
    return "asset_a"


def test_time_place_projection_exposes_precision_sources_and_conflicts(client: TestClient) -> None:
    _seed(client)

    response = client.get("/api/v1/assets/asset_a/time-place")

    assert response.status_code == 200
    body = response.json()
    assert body["time"]["precision"] == "datetime"
    assert body["time"]["timezone_source"] == "exif_offset"
    assert body["time"]["timezone_state"] == "offset_only"
    assert body["time"]["evidence_refs"] == ["ev_time"]
    assert body["place"]["admin_province"] == "浙江省"
    assert body["place"]["admin_city"] == "杭州市"
    assert (body["place"]["latitude"], body["place"]["longitude"]) == (30.0, 120.0)
    assert body["conflict"] is True
    assert body["conflicts"] == ["asset.place.address"]
    assert {item["id"] for item in body["evidence"]} == {"ev_time", "ev_addr_embedded"}
