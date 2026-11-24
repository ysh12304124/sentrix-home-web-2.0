"""事件文档仓储：追加 revision、active 选择与 merge 重定向。

事件正文只从 active 文档读取；旧 revision 保留为审计历史。发布前校验事件存在且
scope 一致，避免文档落到别的相册。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.memories.events import (
    EventContentItem,
    EventCoverage,
    EventDocument,
    EventDocumentMember,
    EventPersonRef,
    EventPlaceProjection,
    EventTimeProjection,
)
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, event_id, revision, status, doc_version, title, short_summary, summary,"
    " summary_source, summary_state, content_items_json, members_json, participants_json,"
    " time_json, place_json, coverage_json, dependencies_json, input_hash, created_at, updated_at"
)


def _new_id() -> str:
    return f"edoc_{uuid.uuid4().hex[:16]}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _load(value: object, default: object) -> object:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _load_list(value: object) -> list[Mapping[str, object]]:
    raw = _load(value, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _load_map(value: object) -> Mapping[str, object]:
    raw = _load(value, {})
    return raw if isinstance(raw, Mapping) else {}


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, (list, tuple)) else ()


def _maps(value: object) -> tuple[Mapping[str, object], ...]:
    return (
        tuple(item for item in value if isinstance(item, Mapping)) if isinstance(value, (list, tuple)) else ()
    )


def _int(value: object) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


def _content(item: EventContentItem) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "text": item.text,
        "observation_id": item.observation_id,
        "asset_id": item.asset_id,
        "observation_revision": item.observation_revision,
        "source": item.source,
        "state": item.state,
        "evidence_refs": list(item.evidence_refs),
    }


def _person(item: EventPersonRef) -> dict[str, object]:
    return {
        "person_id": item.person_id,
        "display_label": item.display_label,
        "verified_name": item.verified_name,
        "identity_states": list(item.identity_states),
        "descriptions": list(item.descriptions),
        "face_instance_ids": list(item.face_instance_ids),
        "occurrence_count": item.occurrence_count,
        "pending": item.pending,
    }


def _member(item: EventDocumentMember) -> dict[str, object]:
    return {
        "observation_id": item.observation_id,
        "asset_id": item.asset_id,
        "media_type": item.media_type,
        "observation_revision": item.observation_revision,
        "captured_at": _iso(item.captured_at),
        "capture_precision": item.capture_precision,
        "timezone_state": item.timezone_state,
        "scene": item.scene,
        "admin_district": item.admin_district,
        "physical_place": item.physical_place,
        "text": item.text,
        "text_state": item.text_state,
        "person_refs": [_person(person) for person in item.person_refs],
        "evidence_refs": list(item.evidence_refs),
    }


def _time(item: EventTimeProjection) -> dict[str, object]:
    return {
        "capture_start": _iso(item.capture_start),
        "capture_end": _iso(item.capture_end),
        "precision": item.precision,
        "timezone_state": item.timezone_state,
        "comparable": item.comparable,
        "member_dates": list(item.member_dates),
        "occurrence_time": _iso(item.occurrence_time),
        "occurrence_time_state": item.occurrence_time_state,
        "display_time": item.display_time,
        "evidence_refs": list(item.evidence_refs),
    }


def _place(item: EventPlaceProjection) -> dict[str, object]:
    return {
        "scene": list(item.scene),
        "physical_locations": list(item.physical_locations),
        "candidates": [dict(candidate) for candidate in item.candidates],
        "display_place": item.display_place,
        "admin_district": item.admin_district,
        "conflict": item.conflict,
        "evidence_refs": list(item.evidence_refs),
    }


def _coverage(item: EventCoverage) -> dict[str, object]:
    return {
        "included_members": item.included_members,
        "excluded_members": item.excluded_members,
        "missing_description": item.missing_description,
        "missing_people": item.missing_people,
        "missing_source": item.missing_source,
        "fields": list(item.fields),
        "notes": list(item.notes),
    }


def _person_from(raw: Mapping[str, object]) -> EventPersonRef:
    return EventPersonRef(
        person_id=str(raw.get("person_id") or ""),
        display_label=str(raw.get("display_label") or ""),
        verified_name=str(raw.get("verified_name") or ""),
        identity_states=_strings(raw.get("identity_states")),
        descriptions=_strings(raw.get("descriptions")),
        face_instance_ids=_strings(raw.get("face_instance_ids")),
        occurrence_count=_int(raw.get("occurrence_count")),
        pending=bool(raw.get("pending", True)),
    )


def _row_to_document(row: sqlite3.Row) -> EventDocument:
    content = _load_list(row["content_items_json"])
    members = _load_list(row["members_json"])
    participants = _load_list(row["participants_json"])
    time = _load_map(row["time_json"])
    place = _load_map(row["place_json"])
    coverage = _load_map(row["coverage_json"])
    dependencies = _load_map(row["dependencies_json"])
    return EventDocument(
        id=str(row["id"]),
        scope_id=str(row["scope_id"]),
        event_id=str(row["event_id"]),
        revision=int(row["revision"]),
        status=str(row["status"]),
        doc_version=str(row["doc_version"]),
        title=str(row["title"]),
        short_summary=str(row["short_summary"]),
        summary=str(row["summary"]),
        summary_source=str(row["summary_source"]),
        summary_state=str(row["summary_state"]),
        source_modalities=_strings(time.get("source_modalities")),
        content_items=tuple(
            EventContentItem(
                item_id=str(item.get("item_id") or ""),
                text=str(item.get("text") or ""),
                observation_id=str(item.get("observation_id") or ""),
                asset_id=str(item.get("asset_id") or ""),
                observation_revision=_int(item.get("observation_revision")),
                source=str(item.get("source") or "observation"),
                state=str(item.get("state") or "effective"),
                evidence_refs=_strings(item.get("evidence_refs")),
            )
            for item in content
            if isinstance(item, Mapping)
        ),
        members=tuple(
            EventDocumentMember(
                observation_id=str(item.get("observation_id") or ""),
                asset_id=str(item.get("asset_id") or ""),
                media_type=str(item.get("media_type") or ""),
                observation_revision=_int(item.get("observation_revision")),
                captured_at=_dt(item.get("captured_at")),
                capture_precision=str(item.get("capture_precision") or "unknown"),
                timezone_state=str(item.get("timezone_state") or "unknown"),
                scene=str(item.get("scene") or ""),
                admin_district=str(item.get("admin_district") or ""),
                physical_place=str(item.get("physical_place") or ""),
                text=str(item.get("text") or ""),
                text_state=str(item.get("text_state") or "not_provided"),
                person_refs=tuple(_person_from(person) for person in _maps(item.get("person_refs"))),
                evidence_refs=_strings(item.get("evidence_refs")),
            )
            for item in members
            if isinstance(item, Mapping)
        ),
        participants=tuple(_person_from(item) for item in participants if isinstance(item, Mapping)),
        time=EventTimeProjection(
            capture_start=_dt(time.get("capture_start")),
            capture_end=_dt(time.get("capture_end")),
            precision=str(time.get("precision") or "unknown"),
            timezone_state=str(time.get("timezone_state") or "unknown"),
            comparable=bool(time.get("comparable", False)),
            member_dates=_strings(time.get("member_dates")),
            occurrence_time=_dt(time.get("occurrence_time")),
            occurrence_time_state=str(time.get("occurrence_time_state") or "unknown"),
            display_time=str(time.get("display_time") or ""),
            evidence_refs=_strings(time.get("evidence_refs")),
        ),
        place=EventPlaceProjection(
            scene=_strings(place.get("scene")),
            physical_locations=_strings(place.get("physical_locations")),
            candidates=tuple(candidate for candidate in _maps(place.get("candidates"))),
            display_place=str(place.get("display_place") or ""),
            admin_district=str(place.get("admin_district") or ""),
            conflict=bool(place.get("conflict", False)),
            evidence_refs=_strings(place.get("evidence_refs")),
        ),
        coverage=EventCoverage(
            included_members=_int(coverage.get("included_members")),
            excluded_members=_int(coverage.get("excluded_members")),
            missing_description=_int(coverage.get("missing_description")),
            missing_people=_int(coverage.get("missing_people")),
            missing_source=_int(coverage.get("missing_source")),
            fields=_strings(coverage.get("fields")),
            notes=_strings(coverage.get("notes")),
        ),
        dependencies={str(k): str(v) for k, v in dependencies.items()}
        if isinstance(dependencies, Mapping)
        else {},
        input_hash=str(row["input_hash"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


class SqliteEventDocumentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def publish(self, document: EventDocument) -> EventDocument:
        if not document.input_hash.strip():
            raise ValueError("event document input_hash is required")
        with self._database.connect() as connection, connection:
            event = connection.execute(
                "SELECT scope_id FROM events WHERE id = ?", (document.event_id,)
            ).fetchone()
            if event is None:
                raise ValueError("event not found")
            if str(event["scope_id"]) != document.scope_id:
                raise ValueError("event document scope mismatch")
            active = connection.execute(
                f"SELECT {_COLUMNS} FROM event_documents"
                " WHERE event_id = ? AND status = 'active' ORDER BY revision DESC LIMIT 1",
                (document.event_id,),
            ).fetchone()
            if active is not None and str(active["input_hash"]) == document.input_hash:
                return _row_to_document(active)
            revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 AS next FROM event_documents WHERE event_id = ?",
                    (document.event_id,),
                ).fetchone()["next"]
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE event_documents SET status = 'superseded', updated_at = ?"
                " WHERE event_id = ? AND status = 'active'",
                (now, document.event_id),
            )
            time_payload = _time(document.time)
            time_payload["source_modalities"] = list(document.source_modalities)
            connection.execute(
                "INSERT INTO event_documents("
                " id, scope_id, event_id, revision, status, doc_version, title, short_summary,"
                " summary, summary_source, summary_state, content_items_json, members_json,"
                " participants_json, person_refs_json, time_json, place_json, coverage_json,"
                " dependencies_json, input_hash, capture_start, capture_end, time_precision,"
                " timezone_state, member_dates_json, physical_place_text, scene_text,"
                " place_conflict, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _new_id(),
                    document.scope_id,
                    document.event_id,
                    revision,
                    "active",
                    document.doc_version,
                    document.title,
                    document.short_summary,
                    document.summary,
                    document.summary_source,
                    document.summary_state,
                    json.dumps([_content(item) for item in document.content_items], ensure_ascii=False),
                    json.dumps([_member(item) for item in document.members], ensure_ascii=False),
                    json.dumps([_person(item) for item in document.participants], ensure_ascii=False),
                    json.dumps([_person(item) for item in document.participants], ensure_ascii=False),
                    json.dumps(time_payload, ensure_ascii=False),
                    json.dumps(_place(document.place), ensure_ascii=False),
                    json.dumps(_coverage(document.coverage), ensure_ascii=False),
                    json.dumps(dict(document.dependencies), ensure_ascii=False),
                    document.input_hash,
                    _iso(document.time.capture_start),
                    _iso(document.time.capture_end),
                    document.time.precision,
                    document.time.timezone_state,
                    json.dumps(list(document.time.member_dates), ensure_ascii=False),
                    document.place.display_place,
                    " / ".join(document.place.scene),
                    1 if document.place.conflict else 0,
                    now,
                    now,
                ),
            )
        created = self.active_for_event(document.event_id)
        assert created is not None
        return created

    def active_for_event(self, event_id: str) -> EventDocument | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM event_documents"
                " WHERE event_id = ? AND status = 'active' ORDER BY revision DESC LIMIT 1",
                (event_id,),
            ).fetchone()
        return _row_to_document(row) if row else None

    def active_for_events(self, event_ids: Sequence[str]) -> Mapping[str, EventDocument]:
        unique = list(dict.fromkeys(item for item in event_ids if item))
        if not unique:
            return {}
        placeholders = ",".join("?" for _ in unique)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM event_documents"
                f" WHERE event_id IN ({placeholders}) AND status = 'active'"
                " ORDER BY event_id, revision DESC",
                tuple(unique),
            ).fetchall()
        documents: dict[str, EventDocument] = {}
        for row in rows:
            documents.setdefault(str(row["event_id"]), _row_to_document(row))
        return documents

    def list_active(self, scope_id: str | None, *, limit: int = 1000) -> Sequence[EventDocument]:
        clause = " WHERE status = 'active'"
        params: list[object] = []
        if scope_id:
            clause += " AND scope_id = ?"
            params.append(scope_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM event_documents{clause}"
                " ORDER BY capture_start DESC, event_id, revision DESC LIMIT ?",
                (*params, max(1, int(limit))),
            ).fetchall()
        return tuple(_row_to_document(row) for row in rows)

    def redirect(self, *, scope_id: str, old_event_id: str, new_event_id: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO event_redirects(scope_id, old_event_id, new_event_id, revision, created_at)"
                " VALUES (?,?,?,1,?) ON CONFLICT(scope_id, old_event_id) DO UPDATE SET"
                " new_event_id = excluded.new_event_id, revision = event_redirects.revision + 1,"
                " created_at = excluded.created_at",
                (scope_id, old_event_id, new_event_id, datetime.now(UTC).isoformat()),
            )

    def redirect_target(self, event_id: str) -> str | None:
        with self._database.connect() as connection:
            seen: set[str] = set()
            current = event_id
            while current not in seen:
                seen.add(current)
                row = connection.execute(
                    "SELECT new_event_id FROM event_redirects WHERE old_event_id = ?", (current,)
                ).fetchone()
                if row is None:
                    break
                current = str(row["new_event_id"])
            return current if current != event_id else None
