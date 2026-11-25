import json
from dataclasses import replace

import pytest

from sentrix.application.media.index_place import IndexAssetPlace
from sentrix.domain.media.source_metadata import SourceMetadata
from sentrix.infrastructure.media.source_address import read_source_address


def package():
    return [
        {
            "id": "email202407260006",
            "timestamp": "2024-07-26 18:29:00",
            "short_summary": "Cairo booking change ZEPHYR",
            "detail": "Date: 2024-07-26\nSubject: Cairo booking\n\nContent: Total $64.08.",
        },
        {
            "id": "email202407260012",
            "timestamp": "2024-07-26 19:29:00",
            "short_summary": "Cairo payment",
            "detail": "Subject: Cairo receipt\nTotal $367.99.",
        },
    ]


def upload(client, rows=None, scope="emails", **data):
    return client.post(
        "/api/v1/documents/import",
        files={"file": ("emails.json", json.dumps(rows or package()).encode(), "application/json")},
        data={"scope_id": scope, **data},
    )


def test_official_emails_are_distinct_idempotent_and_preserve_original(client):
    validation = upload(client, validate_only="true")
    assert validation.status_code == 200
    assert client.get("/api/v1/assets", params={"scope_id": "emails"}).json()["total"] == 0
    first = upload(client)
    assert first.status_code == 200, first.text
    assert first.json()["failed"] == 0
    assert len({r["asset_id"] for r in first.json()["items"]}) == 2
    assert all(r["indexed"] for r in first.json()["items"])
    again = upload(client).json()
    assert all(r["status"] == "reused" for r in again["items"])
    identifier = first.json()["items"][0]["asset_id"]
    asset = client.get(f"/api/v1/assets/{identifier}").json()
    assert asset["source_metadata"]["title"] == "Cairo booking"
    assert asset["source_metadata"]["document_type"] == "email"
    assert asset["captured_at"] == "2024-07-26T18:29:00"
    assert client.get(f"/api/v1/assets/{identifier}/file").text == package()[0]["detail"]
    container = client.app.state.container
    assert container.lexical_index.search_fts("ZEPHYR", scope_id="emails", limit=2)[0][0] == identifier
    changed = package()
    changed[0]["short_summary"] = "changed"
    result = upload(client, changed).json()
    assert result["items"][0]["status"] == "failed"
    assert result["items"][1]["status"] == "reused"


def test_qa_and_duplicate_ids_rejected_before_mutation(client):
    rows = package()
    rows[0]["answer"] = "$64.08"
    assert upload(client, rows).status_code == 400
    assert upload(client, [package()[0], package()[0]]).status_code == 400
    assert client.get("/api/v1/assets", params={"scope_id": "emails"}).json()["total"] == 0


def test_source_type_search_filters_before_fusion(client):
    from datetime import UTC, datetime

    from sentrix.bootstrap.assistant_wiring import build_run_turn

    upload(client)
    client.post(
        "/api/v1/documents/import", files={"file": ("note.md", b"ZEPHYR Cairo")}, data={"scope_id": "emails"}
    )
    container = client.app.state.container
    toolbox = build_run_turn(container, reasoning=True)._toolbox
    outcome = toolbox.execute(
        "search_memories",
        {"query": "ZEPHYR", "source_type": "email"},
        scope_id="emails",
        now=datetime.now(UTC),
    )
    assert outcome.ok, outcome.observation
    result = container.result_set_repository.get(outcome.observation["result_set_id"])
    assert result.asset_ids
    assert all(
        container.asset_repository.get(identifier).source_metadata.document_type == "email"
        for identifier in result.asset_ids
    )


def test_standard_jsonl_and_markdown(client):
    record = {"external_source_id": "doc-v1", "text": "Original 内容", "title": "Title", "summary": "Summary"}
    response = client.post(
        "/api/v1/documents/import", files={"file": ("docs.jsonl", json.dumps(record).encode(), "text/plain")}
    )
    assert response.json()["failed"] == 0

    response = client.post(
        "/api/v1/documents/import", files={"file": ("note.md", b"# My document", "text/markdown")}
    )
    assert response.json()["failed"] == 0


def test_citation_binds_scope_source_and_read_offsets(client):
    from datetime import UTC, datetime

    from sentrix.bootstrap.assistant_wiring import build_run_turn
    from sentrix.domain.assistant.result_set import ResultSet

    container = client.app.state.container
    identifier = upload(client).json()["items"][0]["asset_id"]
    now = datetime.now(UTC)
    container.result_set_repository.add(ResultSet("rs-email", "emails", "Cairo", (identifier,), now))
    toolbox = build_run_turn(container, reasoning=True)._toolbox
    citation = {"result_set_id": "rs-email", "asset_handle": "photo_1"}
    with pytest.raises(ValueError, match="先使用"):
        toolbox.cite_sources([citation], scope_id="emails", reads=[])
    read = toolbox.execute("read_source_text", citation, scope_id="emails", now=now)
    assert read.ok
    assert read.model_visible["external_source_id"] == package()[0]["id"]
    result = toolbox.cite_sources([citation], scope_id="emails", reads=[read.observation])
    assert len(result) == 1
    assert result[0].source_id == package()[0]["id"]
    assert result[0].excerpt == package()[0]["detail"]
    assert result[0].end_offset == len(package()[0]["detail"])
    with pytest.raises(ValueError, match="范围"):
        toolbox.cite_sources([{**citation, "end_offset": 10000}], scope_id="emails", reads=[read.observation])
    with pytest.raises(ValueError, match="访问"):
        toolbox.cite_sources([citation], scope_id="other", reads=[read.observation])


@pytest.mark.parametrize("gps", [None, "keep"])
def test_provided_address_avoids_geocoding_and_survives_sqlite(client, gps):
    identifier = upload(client).json()["items"][0]["asset_id"]
    container = client.app.state.container
    asset = container.asset_repository.get(identifier)
    from sentrix.domain.media.models import GpsPoint

    asset = replace(
        asset,
        captured_location=GpsPoint(30, 120) if gps else None,
        source_metadata=SourceMetadata(
            precise_address="杭州市湖滨路 1 号",
            address_source="atm_geocoding_cache",
            address_source_sha256="a" * 64,
        ),
    )

    class NoGeocoder:
        def resolve(self, gps):
            raise AssertionError("provided address must bypass reverse geocoding")

    assert IndexAssetPlace(resolver=NoGeocoder(), places=container.asset_place_repository).index(asset)
    place = container.asset_place_repository.get(identifier)
    assert place.display() == "杭州市湖滨路 1 号"
    assert place.searchable_text() == "杭州市湖滨路 1 号"
    assert place.address_source_sha256 == "a" * 64


def test_xmp_is_read_from_image_and_mp4_packets(tmp_path):
    for suffix in (".jpg", ".mp4"):
        path = tmp_path / ("sample" + suffix)
        path.write_bytes(
            b"header" * 12000 + b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:s="urn:sentrix:media:1.0/">'
            b"<s:PreciseAddress>Hobson &amp; Co</s:PreciseAddress>"
            b"<s:AddressSource>atm_geocoding_cache</s:AddressSource></x:xmpmeta>" + b"tail"
        )
        address = read_source_address(path)
        assert address.precise_address == "Hobson & Co"
        assert address.address_source == "atm_geocoding_cache"
