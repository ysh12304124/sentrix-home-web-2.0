"""S5：/events/search 静态路由与事件命中成员展开。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.domain.memories.events import (
    EventContentItem,
    EventCoverage,
    EventDocument,
    EventDocumentMember,
    EventPlaceProjection,
    EventTimeProjection,
)

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
            " captured_at, created_at, updated_at)"
            " VALUES ('asset_a', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', 'processed', ?, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
            " revision, created_at, updated_at)"
            " VALUES ('obs_a', 'album1', 'asset_a', 'import', ?, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, time_start, time_end,"
            " place, admin_district, activity, summary, confidence, aggregation_score,"
            " aggregation_breakdown_json, status, revision, created_at, updated_at)"
            " VALUES ('event_a', 'album1', '春节聚餐', '家庭活动', ?, ?, '海边', '', '聚餐',"
            " '春节聚餐', 0.8, 0.9, '{}', 'active', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO event_observations(event_id, observation_id, created_at)"
            " VALUES ('event_a', 'obs_a', ?)",
            (NOW.isoformat(),),
        )
    client.app.state.container.event_documents.publish(
        EventDocument(
            id="",
            scope_id="album1",
            event_id="event_a",
            revision=0,
            status="staged",
            doc_version="event-doc-v1",
            title="春节聚餐",
            short_summary="春节聚餐",
            summary="春节聚餐",
            summary_source="model",
            summary_state="model_generated_unverified",
            source_modalities=("image",),
            content_items=(
                EventContentItem(
                    item_id="obs_a",
                    text="海边合影",
                    observation_id="obs_a",
                    asset_id="asset_a",
                    evidence_refs=("observation:obs_a",),
                ),
            ),
            members=(
                EventDocumentMember(
                    observation_id="obs_a",
                    asset_id="asset_a",
                    media_type="image",
                    captured_at=NOW,
                    capture_precision="datetime",
                    text="海边合影",
                    text_state="available",
                    evidence_refs=("observation:obs_a",),
                ),
            ),
            participants=(),
            time=EventTimeProjection(
                capture_start=NOW,
                capture_end=NOW,
                precision="datetime",
                member_dates=("2026-11-24",),
                display_time="2026-11-24",
            ),
            place=EventPlaceProjection(display_place="浙江省杭州市"),
            coverage=EventCoverage(included_members=1),
            dependencies={"schema_version": "event-doc-v1"},
            input_hash="event-a-v1",
        )
    )


def test_event_search_is_static_and_expands_real_members(client: TestClient) -> None:
    _seed(client)

    response = client.get("/api/v1/events/search", params={"q": "海边合影", "scope_id": "album1"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["event_id"] for item in items] == ["event_a"]
    hit = items[0]
    assert hit["document"]["document_id"]
    assert "content" in hit["matched_fields"]
    assert hit["members"] == [
        {
            "observation_id": "obs_a",
            "asset_id": "asset_a",
            "media_type": "image",
            "captured_at": NOW.isoformat(),
            "matched": True,
        }
    ]
    detail = client.get("/api/v1/events/event_a")
    assert detail.status_code == 200
    assert detail.json()["id"] == "event_a"
