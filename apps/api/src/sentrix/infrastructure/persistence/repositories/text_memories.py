"""Unified authority storage for text, retaining origin only as metadata."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sentrix.domain.media.text_memory import TextMemoryItem, text_asset_id
from sentrix.infrastructure.persistence.database import Database


class SqliteTextMemoryRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def find(self, scope_id: str, external_id: str) -> dict[str, object] | None:
        with self._database.connect() as c:
            row = c.execute(
                "SELECT a.id asset_id,a.content_sha256,o.id observation_id,o.revision,s.event_id,"
                "s.indexed_revision,s.last_error,o.source_metadata_json FROM observations o "
                "JOIN assets a ON a.id=o.asset_id JOIN text_import_state s ON s.asset_id=a.id "
                "WHERE o.scope_id=? AND o.source_type='text' AND o.external_id=?",
                (scope_id, external_id),
            ).fetchone()
        return dict(row) if row else None

    def insert(self, scope_id: str, item: TextMemoryItem, path: str) -> dict[str, object]:
        aid = text_asset_id(scope_id, item.external_id)
        oid, eid = "obs_" + aid, "evt_" + aid
        now = datetime.now(UTC).isoformat()
        stamp = datetime.fromisoformat(item.timestamp).isoformat() if item.timestamp else None
        metadata = {**item.metadata, "external_id": item.external_id, "timestamp_kind": "source_record_time"}
        with self._database.connect() as c, c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT scope_id,media_type,content_sha256 FROM assets WHERE id=?",
                (aid,),
            ).fetchone()
            if existing and (
                existing["scope_id"] != scope_id
                or existing["media_type"] != "text"
                or existing["content_sha256"] != item.fingerprint
            ):
                raise ValueError(f"文本资产 ID 与现有媒体冲突：{item.external_id}")
            c.execute(
                "INSERT INTO assets(id,scope_id,file_name,media_type,path,mime_type,size_bytes,status,"
                "content_sha256,captured_at,capture_time_json,created_at,updated_at) "
                "VALUES(?,?,?,'text',?,'text/plain',?,'processed',?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    aid,
                    scope_id,
                    item.external_id + ".txt",
                    path,
                    len(item.body.encode()),
                    item.fingerprint,
                    stamp,
                    json.dumps(
                        {
                            "raw": item.timestamp,
                            "source": "text_record_time" if stamp else "unknown",
                            "timezone": "",
                            "status": "known" if stamp else "unknown",
                        }
                    ),
                    now,
                    now,
                ),
            )
            c.execute(
                "INSERT INTO observations(id,scope_id,asset_id,source_type,captured_at,caption,"
                "text_content,external_id,event_type,confidence,created_at,updated_at,source_metadata_json,"
                "source_text) "
                "VALUES(?,?,?,'text',?,?,?,?,'text_record',1,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    oid,
                    scope_id,
                    aid,
                    stamp,
                    item.summary,
                    item.body,
                    item.external_id,
                    now,
                    now,
                    json.dumps(item.metadata, ensure_ascii=False),
                    item.body,
                ),
            )
            c.execute(
                "INSERT INTO events(id,scope_id,title,summary,event_type,time_start,time_end,source_type,"
                "source_asset_id,source_metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,'text_record',?,?,'text',?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    eid,
                    scope_id,
                    item.summary[:120],
                    item.summary,
                    stamp,
                    stamp,
                    aid,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            c.execute(
                "INSERT INTO event_observations(event_id,observation_id,created_at) VALUES(?,?,?) "
                "ON CONFLICT(event_id,observation_id) DO NOTHING",
                (eid, oid, now),
            )
            c.execute(
                "INSERT INTO text_import_state(asset_id,observation_id,event_id) VALUES(?,?,?) "
                "ON CONFLICT(asset_id) DO UPDATE SET observation_id=excluded.observation_id,"
                "event_id=excluded.event_id",
                (aid, oid, eid),
            )
        result = self.find(scope_id, item.external_id)
        assert result is not None
        return result

    def finish(self, asset_id: str, revision: int, error: str) -> None:
        with self._database.connect() as c, c:
            c.execute(
                "UPDATE text_import_state SET indexed_revision=?,last_error=? WHERE asset_id=?",
                (0 if error else revision, error, asset_id),
            )
