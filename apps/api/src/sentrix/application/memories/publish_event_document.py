"""事件文档的确定性构建与发布。

顺序固定：成员完整 → 全量出现/时地/参与者投影 → 现有总结或可溯源回退 → 发布新
revision。这里不调用模型、不重新归并事件；模型总结由既有 ``SummarizeEvent`` 产出，
本用例只负责把它和可核对的证据一起发布。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sentrix.domain.identity.models import PersonOccurrenceRecord
from sentrix.domain.identity.ports import FaceRepository
from sentrix.domain.media.ports import (
    AssetRepository,
    MemoryEvidenceRepository,
    ObservationRepository,
)
from sentrix.domain.memories.events import (
    EventContentItem,
    EventCoverage,
    EventDocument,
    EventDocumentMember,
    EventParticipantRecord,
    EventPersonRef,
    EventPlaceProjection,
    EventRecord,
    EventTimeProjection,
)
from sentrix.domain.memories.ports import (
    EventDocumentRepository,
    EventParticipantRepository,
    EventRepository,
)
from sentrix.domain.places.ports import AssetPlaceRepository

_PLACEHOLDERS = {"", "待总结事件", "待判断", "其他或不确定", "未知", "未知地点"}
_PRECISION_RANK = {"unknown": 0, "year": 1, "year_month": 2, "date": 3, "datetime": 4}
_VERIFIED_STATES = {"user_verified", "auto_trusted"}
_SCHEMA_VERSION = "event-doc-v1"


def _json_hash(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _norm(value: object) -> str:
    return str(value or "").strip()


def _verified(record: PersonOccurrenceRecord) -> str:
    if record.identity_state not in _VERIFIED_STATES or not record.person_id:
        return ""
    label = record.display_label
    return "" if label.startswith(("人物…", "本图…")) else label


def _member_person(record: PersonOccurrenceRecord) -> EventPersonRef:
    return EventPersonRef(
        person_id=record.person_id or "",
        display_label=record.display_label,
        verified_name=_verified(record),
        identity_states=(record.identity_state,),
        descriptions=(record.description,) if record.description else (),
        face_instance_ids=(record.face_instance_id,),
        occurrence_count=1,
        pending=record.identity_state not in _VERIFIED_STATES,
    )


def _merge_person(target: dict[str, object], source: EventPersonRef) -> None:
    states = set(cast(tuple[str, ...], target["identity_states"]))
    states.update(source.identity_states)
    target["identity_states"] = tuple(sorted(states))
    descriptions = list(cast(tuple[str, ...], target["descriptions"]))
    descriptions.extend(item for item in source.descriptions if item not in descriptions)
    target["descriptions"] = tuple(descriptions)
    faces = list(cast(tuple[str, ...], target["face_instance_ids"]))
    faces.extend(item for item in source.face_instance_ids if item not in faces)
    target["face_instance_ids"] = tuple(faces)
    target["occurrence_count"] = int(cast(int, target["occurrence_count"])) + 1
    target["pending"] = not bool(target["verified_name"]) and bool(target["pending"])


def _aggregate_people(
    members: Sequence[EventDocumentMember], participants: Sequence[EventParticipantRecord]
) -> tuple[EventPersonRef, ...]:
    grouped: dict[str, dict[str, object]] = {}
    for member in members:
        for person in member.person_refs:
            key = person.person_id or f"local:{person.face_instance_ids[0]}"
            if key not in grouped:
                grouped[key] = {
                    "person_id": person.person_id,
                    "display_label": person.display_label,
                    "verified_name": person.verified_name,
                    "identity_states": person.identity_states,
                    "descriptions": person.descriptions,
                    "face_instance_ids": person.face_instance_ids,
                    "occurrence_count": person.occurrence_count,
                    "pending": person.pending,
                }
            else:
                _merge_person(grouped[key], person)
    for participant in participants:
        key = participant.person_id
        confirmed = participant.person_status == "confirmed"
        name = participant.person_name if confirmed and participant.person_name else ""
        label = name or f"人物…{participant.person_id[-6:]}"
        if key not in grouped:
            grouped[key] = {
                "person_id": key,
                "display_label": label,
                "verified_name": name,
                "identity_states": (),
                "descriptions": (),
                "face_instance_ids": (),
                "occurrence_count": 0,
                "pending": not confirmed,
            }
        elif name:
            grouped[key]["verified_name"] = name
            grouped[key]["display_label"] = name
            grouped[key]["pending"] = False
    people = [
        EventPersonRef(
            person_id=str(value["person_id"]),
            display_label=str(value["display_label"]),
            verified_name=str(value["verified_name"]),
            identity_states=tuple(cast(tuple[str, ...], value["identity_states"])),
            descriptions=tuple(cast(tuple[str, ...], value["descriptions"])),
            face_instance_ids=tuple(cast(tuple[str, ...], value["face_instance_ids"])),
            occurrence_count=int(cast(int, value["occurrence_count"])),
            pending=bool(value["pending"]),
        )
        for value in grouped.values()
    ]
    people.sort(
        key=lambda item: (
            not bool(item.verified_name),
            item.pending,
            item.person_id,
            item.display_label,
        )
    )
    return tuple(people)


def _time_projection(
    members: Sequence[EventDocumentMember], evidence_refs: Sequence[str]
) -> EventTimeProjection:
    dated = [member for member in members if member.captured_at is not None]
    if not dated:
        return EventTimeProjection(evidence_refs=tuple(evidence_refs), display_time="未知")
    aware = [item.captured_at.utcoffset() is not None for item in dated if item.captured_at is not None]
    comparable = all(aware) or not any(aware)
    starts = [item.captured_at for item in dated if item.captured_at is not None]
    start = min(starts) if comparable else None
    end = max(starts) if comparable else None
    precision = min(
        (item.capture_precision for item in dated), key=lambda item: _PRECISION_RANK.get(item, 0)
    )
    timezone_state = "unknown"
    if aware and all(aware):
        timezone_state = "absolute"
    elif aware and not any(aware):
        timezone_state = "floating"
    elif aware:
        timezone_state = "mixed"
    member_dates: list[str] = []
    for member in dated:
        value = member.captured_at
        if value is None:
            continue
        precision_value = member.capture_precision
        if precision_value == "year":
            text = f"{value.year:04d}"
        elif precision_value == "year_month":
            text = f"{value.year:04d}-{value.month:02d}"
        else:
            text = value.date().isoformat()
        if text not in member_dates:
            member_dates.append(text)
    if start is not None and end is not None:
        if precision == "year":
            display = f"{start.year:04d}"
        elif precision == "year_month":
            display = (
                f"{start.year:04d}-{start.month:02d}"
                if (start.year, start.month) == (end.year, end.month)
                else f"{start.year:04d}-{start.month:02d} 至 {end.year:04d}-{end.month:02d}"
            )
        elif start.date() == end.date():
            display = start.date().isoformat()
        else:
            display = f"{start.date().isoformat()} 至 {end.date().isoformat()}"
    else:
        display = "时间不可比较（时区未知）"
    return EventTimeProjection(
        capture_start=start,
        capture_end=end,
        precision=precision,
        timezone_state=timezone_state,
        comparable=comparable,
        member_dates=tuple(member_dates),
        occurrence_time=None,
        occurrence_time_state="unknown",
        display_time=display,
        evidence_refs=tuple(evidence_refs),
    )


class PublishEventDocument:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        places: AssetPlaceRepository,
        faces: FaceRepository,
        participants: EventParticipantRepository | None = None,
        evidence: MemoryEvidenceRepository | None = None,
        documents: EventDocumentRepository,
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._places = places
        self._faces = faces
        self._participants = participants
        self._evidence = evidence
        self._documents = documents

    def execute(
        self, event_id: str, *, summary_source: str = "auto", summary_state: str = ""
    ) -> EventDocument | None:
        event = self._events.get(event_id)
        if event is None:
            return None
        observation_ids = self._events.observation_ids(event_id)
        observations = [
            item
            for item in (self._observations.get(observation_id) for observation_id in observation_ids)
            if item is not None
        ]
        assets = self._assets.get_many([item.asset_id for item in observations])
        participants = (
            self._participants.list_participants(event_id) if self._participants is not None else ()
        )
        members: list[EventDocumentMember] = []
        content: list[EventContentItem] = []
        place_refs: list[str] = []
        time_refs: list[str] = []
        scenes: list[str] = []
        physical: list[str] = []
        candidates: list[dict[str, object]] = []
        conflict = False
        admin_district = event.admin_district
        for observation in observations:
            asset = assets.get(observation.asset_id)
            if asset is None:
                continue
            member_refs = [f"observation:{observation.id}", f"asset:{asset.id}"]
            if observation.active_build_id:
                member_refs.append(f"build:{observation.active_build_id}")
            occurrences = tuple(
                self._faces.list_occurrences(
                    scope_id=event.scope_id, asset_id=asset.id, limit=100
                )
            )
            for record in occurrences:
                member_refs.append(f"face:{record.face_instance_id}")
            asset_place = self._places.get(asset.id)
            scene = _norm(observation.place)
            physical_text = asset_place.display() if asset_place is not None else ""
            if scene:
                scenes.append(scene)
                candidates.append(
                    {"kind": "scene", "text": scene, "asset_id": asset.id, "source": "observation"}
                )
            if physical_text:
                physical.append(physical_text)
                candidates.append(
                    {
                        "kind": "physical_location",
                        "text": physical_text,
                        "asset_id": asset.id,
                        "precision": asset_place.precision.value if asset_place is not None else "unknown",
                        "source": asset_place.source.value if asset_place is not None else "",
                    }
                )
                admin_district = admin_district or (
                    asset_place.admin.district or asset_place.admin.city if asset_place is not None else ""
                )
                place_refs.append(f"asset_place:{asset.id}")
            if self._evidence is not None:
                address_rows = self._evidence.list_for_owner(
                    scope_id=event.scope_id,
                    owner_kind="asset",
                    owner_id=asset.id,
                    field_path="asset.place.address",
                )
                for row in address_rows:
                    value = row.value if isinstance(row.value, dict) else {}
                    address = _norm(value.get("address"))
                    if not address:
                        continue
                    candidates.append(
                        {
                            "kind": "address",
                            "text": address,
                            "asset_id": asset.id,
                            "source": row.source_kind,
                            "status": row.status,
                        }
                    )
                    place_refs.append(row.id)
                    if row.status == "conflict":
                        conflict = True
            if observation.captured_at is not None or asset.captured_at is not None:
                time_refs.append(f"asset:{asset.id}")
            text = (
                _norm(observation.short_caption)
                or _norm(observation.caption)
                or _norm(observation.detail_caption)
                or _norm(observation.ocr_text)
            )
            if text:
                content.append(
                    EventContentItem(
                        item_id=observation.id,
                        text=text,
                        observation_id=observation.id,
                        asset_id=asset.id,
                        observation_revision=observation.revision,
                        source="observation",
                        evidence_refs=(f"observation:{observation.id}",),
                    )
                )
            members.append(
                EventDocumentMember(
                    observation_id=observation.id,
                    asset_id=asset.id,
                    media_type=str(asset.media_type),
                    observation_revision=observation.revision,
                    captured_at=asset.captured_at,
                    capture_precision=asset.capture_time.precision,
                    timezone_state=asset.capture_time.status,
                    scene=scene,
                    admin_district=asset_place.admin.district if asset_place is not None else "",
                    physical_place=physical_text,
                    text=text,
                    text_state="available" if text else "not_provided",
                    person_refs=tuple(_member_person(item) for item in occurrences),
                    evidence_refs=tuple(member_refs),
                )
            )
        if len(set(physical)) > 1:
            conflict = True
        people = _aggregate_people(members, participants)
        time = _time_projection(members, time_refs)
        unique_physical = tuple(dict.fromkeys(item for item in physical if item))
        unique_scenes = tuple(dict.fromkeys(item for item in scenes if item))
        display_place = unique_physical[0] if len(unique_physical) == 1 and not conflict else ""
        place_projection = EventPlaceProjection(
            scene=unique_scenes,
            physical_locations=unique_physical,
            candidates=tuple(candidates),
            display_place=display_place,
            admin_district=admin_district,
            conflict=conflict,
            evidence_refs=tuple(dict.fromkeys(place_refs)),
        )
        existing_summary = _norm(event.summary)
        has_summary = bool(existing_summary) and existing_summary not in _PLACEHOLDERS
        if not has_summary and content:
            existing_summary = "；".join(item.text for item in content[:8])
            summary_source_value = "deterministic"
            summary_state_value = "supported_by_content_items"
        else:
            summary_source_value = summary_source if summary_source != "auto" else "event_summary"
            summary_state_value = (
                summary_state
                or (
                    "supported_by_content_items"
                    if summary_source_value == "deterministic"
                    else "model_generated_unverified"
                )
            )
        short_summary = _short_summary(event, content, existing_summary)
        title = _norm(event.title)
        if title in _PLACEHOLDERS:
            title = (content[0].text[:40] if content else "未命名事件")
        modalities = sorted(
            {str(asset.media_type) for asset in assets.values() if asset is not None}
        )
        if event.source_type == "video_scene" and "video" not in modalities:
            modalities.append("video")
        coverage = EventCoverage(
            included_members=len(members),
            excluded_members=max(0, len(observation_ids) - len(members)),
            missing_description=sum(1 for item in members if not item.text),
            missing_people=sum(1 for item in members if not item.person_refs),
            missing_source=sum(
                1
                for item in members
                if item.captured_at is None and not item.physical_place and not item.scene
            ),
            fields=(
                "members",
                "participants",
                "time",
                "place",
                "content_items",
                "summary",
            ),
            notes=(
                ("legacy_summary_without_per_claim_refs",)
                if summary_source_value == "event_summary"
                else ()
            ),
        )
        dependencies = {
            "schema_version": _SCHEMA_VERSION,
            "event_revision": str(event.revision),
            "member_fingerprint": _json_hash(
                [
                    [item.observation_id, item.observation_revision, item.asset_id]
                    for item in members
                ]
            ),
            "participant_fingerprint": _json_hash(
                [[item.person_id, item.identity_states, item.face_instance_ids] for item in people]
            ),
            "summary_input_fingerprint": _json_hash(
                [existing_summary, summary_source_value, summary_state_value]
            ),
            "index_version": "pending",
        }
        payload = {
            "event_id": event.id,
            "revision": event.revision,
            "title": title,
            "short_summary": short_summary,
            "summary": existing_summary,
            "summary_source": summary_source_value,
            "summary_state": summary_state_value,
            "modalities": modalities,
            "content": [[item.item_id, item.text, item.observation_revision] for item in content],
            "members": [
                [item.observation_id, item.observation_revision, item.asset_id] for item in members
            ],
            "participants": [item.person_id or item.face_instance_ids for item in people],
            "time": {
                "start": _iso(time.capture_start),
                "end": _iso(time.capture_end),
                "dates": list(time.member_dates),
                "precision": time.precision,
                "timezone_state": time.timezone_state,
            },
            "place": {
                "scene": list(place_projection.scene),
                "physical": list(place_projection.physical_locations),
                "display": place_projection.display_place,
                "conflict": place_projection.conflict,
            },
            "dependencies": dependencies,
        }
        return self._documents.publish(
            EventDocument(
                id="",
                scope_id=event.scope_id,
                event_id=event.id,
                revision=0,
                status="staged",
                doc_version=_SCHEMA_VERSION,
                title=title,
                short_summary=short_summary,
                summary=existing_summary,
                summary_source=summary_source_value,
                summary_state=summary_state_value,
                source_modalities=tuple(modalities),
                content_items=tuple(content),
                members=tuple(members),
                participants=people,
                time=time,
                place=place_projection,
                coverage=coverage,
                dependencies=dependencies,
                input_hash=_json_hash(payload),
            )
        )


def _short_summary(
    event: EventRecord, content: Sequence[EventContentItem], summary: str
) -> str:
    basis = summary or (content[0].text if content else "") or event.title
    text = basis.strip()
    for separator in ("。", "；", "\n"):
        if separator in text:
            text = text.split(separator, 1)[0]
            break
    return text[:120]
