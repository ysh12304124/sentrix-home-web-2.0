"""时间线读取用例：年月日索引、按日分页与事件详情。

日期属于事件的语义：有 active 事件文档时按成员拍摄日期过滤与计数；legacy 事件回退
``events.time_start``。缺失时归入 **``unknown``**，不退回 ``created_at``（上传时间不是
拍摄时间）。列表、计数与日历共用这一组后端谓词，前端不自行筛选或推算。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sentrix.domain.media.capture_time import time_sort_key
from sentrix.domain.media.models import Asset, MediaType
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventDocument, EventRecord
from sentrix.domain.memories.ports import EventDocumentRepository, EventRepository
from sentrix.domain.places.ports import AssetPlaceRepository

DEFAULT_SCOPE_ID = "home-default"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
EVENT_SCAN_LIMIT = 10000
#: 没有拍摄时间的事件桶。与日期筛选参数共用一个字面量。
UNKNOWN_DAY = "unknown"


@dataclass(frozen=True, slots=True)
class TimelinePhoto:
    asset_id: str
    file_name: str
    captured_at: datetime | None
    place: str


@dataclass(frozen=True, slots=True)
class TimelineDocumentProjection:
    """事件文档的展示投影；``None`` 表示 legacy 事件，不冒充逐项可溯源。"""

    document_id: str
    revision: int
    doc_version: str
    title: str
    short_summary: str
    summary_source: str
    summary_state: str
    source_modalities: tuple[str, ...] = ()
    time_basis: str = "capture"
    display_time: str = ""
    capture_start: datetime | None = None
    capture_end: datetime | None = None
    precision: str = "unknown"
    timezone_state: str = "unknown"
    member_dates: tuple[str, ...] = ()
    occurrence_time: datetime | None = None
    occurrence_time_state: str = "unknown"
    scene: tuple[str, ...] = ()
    physical_locations: tuple[str, ...] = ()
    display_place: str = ""
    place_conflict: bool = False
    participants: tuple[dict[str, object], ...] = ()
    content_items: tuple[dict[str, object], ...] = ()
    coverage: dict[str, object] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    id: str
    title: str
    event_type: str
    time_start: datetime | None
    time_end: datetime | None
    place: str
    admin_district: str
    activity: str
    summary: str
    confidence: float
    photos: tuple[TimelinePhoto, ...] = ()
    scope_id: str = ""
    timeline_date: str = ""
    document: TimelineDocumentProjection | None = None


@dataclass(frozen=True, slots=True)
class TimelineEventSummary:
    """按日列表的紧凑事件：只够渲染一张卡，不展开全部素材。"""

    id: str
    title: str
    time_start: datetime | None
    time_end: datetime | None
    place: str
    admin_district: str
    summary: str
    representative_asset_id: str | None
    representative_media_type: str | None
    asset_count: int
    document: TimelineDocumentProjection | None = None


@dataclass(frozen=True, slots=True)
class TimelineDay:
    date: str
    events: tuple[TimelineEventSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineResult:
    scope_id: str
    days: tuple[TimelineDay, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True, slots=True)
class CalendarDay:
    date: str
    count: int


@dataclass(frozen=True, slots=True)
class CalendarMonth:
    month: int
    count: int
    days: tuple[CalendarDay, ...]


@dataclass(frozen=True, slots=True)
class CalendarYear:
    year: int
    count: int
    months: tuple[CalendarMonth, ...]


@dataclass(frozen=True, slots=True)
class CalendarIndex:
    """时间线日历索引。``unknown_count`` 是没有拍摄时间的事件数。

    它不塞进 ``years``：未知时间不是某一年某一天，混进去会让前端把上传日当成拍摄日。
    """

    years: tuple[CalendarYear, ...] = ()
    unknown_count: int = 0


@dataclass(slots=True)
class _DayBucket:
    date: str
    events: list[TimelineEventSummary] = field(default_factory=list)


class ReadTimeline:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        places: AssetPlaceRepository,
        documents: EventDocumentRepository | None = None,
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._places = places
        self._documents = documents

    def _documents_for(self, event_ids: Sequence[str]) -> dict[str, EventDocument]:
        if self._documents is None:
            return {}
        return dict(self._documents.active_for_events(list(event_ids)))

    def execute(
        self,
        *,
        scope_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        place: str = "",
        scene: str = "",
        has_place: bool = False,
        date: str = "",
        date_filter_mode: str = "any_capture",
    ) -> TimelineResult:
        target_scope = scope_id or None
        page_size = max(1, min(int(limit), MAX_LIMIT))
        start = max(0, int(offset))
        events = list(self._events.list_by_scope(target_scope, limit=EVENT_SCAN_LIMIT))
        documents = self._documents_for([event.id for event in events])
        pairs = [
            (event, documents.get(event.id))
            for event in events
            if _matches(
                event,
                documents.get(event.id),
                place=place,
                scene=scene,
                has_place=has_place,
                date=date,
                date_filter_mode=date_filter_mode,
            )
        ]
        pairs.sort(key=lambda pair: pair[0].id)
        pairs.sort(key=lambda pair: _sort_value(pair[0], pair[1]), reverse=True)
        total = len(pairs)
        page = pairs[start : start + page_size]
        asset_ids_by_event = self._asset_ids_many([event.id for event, _ in page])
        asset_ids = [asset_id for ids in asset_ids_by_event.values() for asset_id in ids]
        assets = self._assets.get_many(asset_ids)

        days: list[_DayBucket] = []
        for event, document in page:
            key = _day_key(event, document)
            if not days or days[-1].date != key:
                days.append(_DayBucket(date=key))
            days[-1].events.append(
                self._summarize(event, document, asset_ids_by_event.get(event.id, ()), assets)
            )
        return TimelineResult(
            scope_id=target_scope or "",
            days=tuple(TimelineDay(date=day.date, events=tuple(day.events)) for day in days),
            total=total,
            offset=start,
            limit=page_size,
        )

    def calendar(
        self,
        scope_id: str | None = None,
        *,
        place: str = "",
        scene: str = "",
        has_place: bool = False,
        date_filter_mode: str = "any_capture",
    ) -> CalendarIndex:
        events = list(self._events.list_by_scope(scope_id or None, limit=EVENT_SCAN_LIMIT))
        documents = self._documents_for([event.id for event in events])
        counts: dict[str, int] = {}
        for event in events:
            document = documents.get(event.id)
            if not _matches(
                event,
                document,
                place=place,
                scene=scene,
                has_place=has_place,
                date="",
                date_filter_mode=date_filter_mode,
            ):
                continue
            for day in _event_days(event, document, date_filter_mode):
                counts[day] = counts.get(day, 0) + 1
        years: dict[int, dict[int, list[CalendarDay]]] = {}
        year_counts: dict[int, int] = {}
        month_counts: dict[tuple[int, int], int] = {}
        unknown_count = 0
        for date, count in sorted(counts.items()):
            if date == UNKNOWN_DAY:
                unknown_count += count
                continue
            parts = _calendar_parts(date)
            if parts is None:
                unknown_count += count
                continue
            year, month = parts
            years.setdefault(year, {}).setdefault(month, []).append(CalendarDay(date=date, count=count))
            year_counts[year] = year_counts.get(year, 0) + count
            month_counts[(year, month)] = month_counts.get((year, month), 0) + count
        return CalendarIndex(
            years=tuple(
                CalendarYear(
                    year=year,
                    count=year_counts.get(year, 0),
                    months=tuple(
                        CalendarMonth(
                            month=month,
                            count=month_counts.get((year, month), 0),
                            days=tuple(days[month]),
                        )
                        for month in sorted(days)
                    ),
                )
                for year in sorted(years, reverse=True)
                for days in (years[year],)
            ),
            unknown_count=unknown_count,
        )

    def get_event(self, event_id: str) -> TimelineEvent | None:
        event = self._events.get(event_id)
        if event is None:
            return None
        document = self._documents.active_for_event(event_id) if self._documents is not None else None
        asset_ids = self._asset_ids_many([event.id]).get(event.id, ())
        assets = self._assets.get_many(asset_ids)
        places = self._places.get_many(asset_ids)
        photos: list[TimelinePhoto] = []
        for asset_id in asset_ids:
            asset = assets.get(asset_id)
            if asset is None:
                continue
            place = places.get(asset_id)
            photos.append(
                TimelinePhoto(
                    asset_id=asset_id,
                    file_name=asset.file_name,
                    captured_at=asset.captured_at,
                    place=place.display() if place else "",
                )
            )
        return TimelineEvent(
            id=event.id,
            title=event.title,
            event_type=event.event_type,
            time_start=event.time_start,
            time_end=event.time_end,
            place=event.place,
            admin_district=event.admin_district,
            activity=event.activity,
            summary=event.summary,
            confidence=event.confidence,
            photos=tuple(photos),
            scope_id=event.scope_id,
            timeline_date=_day_key(event, document),
            document=_document_projection(document),
        )

    def _summarize(
        self,
        event: EventRecord,
        document: EventDocument | None,
        asset_ids: tuple[str, ...] | list[str],
        assets: Mapping[str, Asset],
    ) -> TimelineEventSummary:
        representative = next((assets[asset_id] for asset_id in asset_ids if asset_id in assets), None)
        image = next(
            (
                assets[asset_id]
                for asset_id in asset_ids
                if asset_id in assets and assets[asset_id].media_type is MediaType.IMAGE
            ),
            None,
        )
        chosen = image or representative
        projection = _document_projection(document)
        return TimelineEventSummary(
            id=event.id,
            title=document.title if document is not None else event.title,
            time_start=event.time_start,
            time_end=event.time_end,
            place=document.place.display_place if document is not None else event.place,
            admin_district=event.admin_district,
            summary=document.short_summary if document is not None else event.summary,
            representative_asset_id=chosen.id if chosen is not None else None,
            representative_media_type=str(chosen.media_type) if chosen is not None else None,
            asset_count=len(asset_ids),
            document=projection,
        )

    def _asset_ids_many(self, event_ids: list[str]) -> dict[str, list[str]]:
        """一页事件的素材关联一次取完，再按拍摄时间排序去重。"""
        if not event_ids:
            return {}
        observation_ids_by_event = self._events.observation_ids_many(event_ids)
        all_observation_ids = [
            observation_id for ids in observation_ids_by_event.values() for observation_id in ids
        ]
        observations = self._observations.get_many(all_observation_ids)
        all_asset_ids = [
            observation.asset_id for observation in observations.values() if observation.asset_id
        ]
        assets = self._assets.get_many(all_asset_ids)

        result: dict[str, list[str]] = {}
        for event_id in event_ids:
            ordered: list[tuple[str, str]] = []
            for observation_id in observation_ids_by_event.get(event_id, ()):
                observation = observations.get(observation_id)
                if observation is None:
                    continue
                asset = assets.get(observation.asset_id)
                if asset is None:
                    continue
                ordered.append((time_sort_key(asset.captured_at or asset.created_at), asset.id))
            ordered.sort(key=lambda item: (item[0], item[1]))
            result[event_id] = list(dict.fromkeys(asset_id for _, asset_id in ordered))
        return result


def _document_projection(document: EventDocument | None) -> TimelineDocumentProjection | None:
    if document is None:
        return None
    refs: list[str] = []
    for member in document.members:
        refs.extend(member.evidence_refs)
    refs.extend(document.time.evidence_refs)
    refs.extend(document.place.evidence_refs)
    return TimelineDocumentProjection(
        document_id=document.id,
        revision=document.revision,
        doc_version=document.doc_version,
        title=document.title,
        short_summary=document.short_summary,
        summary_source=document.summary_source,
        summary_state=document.summary_state,
        source_modalities=document.source_modalities,
        display_time=document.time.display_time,
        capture_start=document.time.capture_start,
        capture_end=document.time.capture_end,
        precision=document.time.precision,
        timezone_state=document.time.timezone_state,
        member_dates=document.time.member_dates,
        occurrence_time=document.time.occurrence_time,
        occurrence_time_state=document.time.occurrence_time_state,
        scene=document.place.scene,
        physical_locations=document.place.physical_locations,
        display_place=document.place.display_place,
        place_conflict=document.place.conflict,
        participants=tuple(
            {
                "person_id": item.person_id,
                "display_label": item.display_label,
                "verified_name": item.verified_name,
                "pending": item.pending,
                "identity_states": list(item.identity_states),
                "descriptions": list(item.descriptions),
                "face_instance_ids": list(item.face_instance_ids),
            }
            for item in document.participants
        ),
        content_items=tuple(
            {
                "item_id": item.item_id,
                "text": item.text,
                "observation_id": item.observation_id,
                "asset_id": item.asset_id,
                "observation_revision": item.observation_revision,
                "source": item.source,
                "state": item.state,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in document.content_items
        ),
        coverage={
            "included_members": document.coverage.included_members,
            "excluded_members": document.coverage.excluded_members,
            "missing_description": document.coverage.missing_description,
            "missing_people": document.coverage.missing_people,
            "missing_source": document.coverage.missing_source,
            "fields": list(document.coverage.fields),
            "notes": list(document.coverage.notes),
        },
        evidence_refs=tuple(dict.fromkeys(refs)),
    )


def _matches(
    event: EventRecord,
    document: EventDocument | None,
    *,
    place: str,
    scene: str,
    has_place: bool,
    date: str,
    date_filter_mode: str,
) -> bool:
    physical = " ".join(document.place.physical_locations) if document is not None else ""
    admin = document.place.admin_district if document is not None else event.admin_district
    scene_text = " ".join(document.place.scene) if document is not None else event.place
    legacy_place = event.place
    if place:
        target = f"{physical} {admin}" if document is not None else f"{legacy_place} {admin}"
        if place.casefold() not in target.casefold():
            return False
    if scene and scene.casefold() not in scene_text.casefold():
        return False
    if has_place:
        if document is not None:
            if not document.place.physical_locations:
                return False
        elif legacy_place.strip() in {"", "其他或不确定", "未知", "未知地点"}:
            return False
    return _date_matches(event, document, date, date_filter_mode)


def _date_matches(event: EventRecord, document: EventDocument | None, date: str, mode: str) -> bool:
    if not date:
        return True
    if document is not None:
        dates = document.time.member_dates
        if date == UNKNOWN_DAY:
            return not dates and document.time.capture_start is None
        if mode == "first_capture":
            value = (
                document.time.capture_start.date().isoformat()
                if document.time.capture_start is not None
                else (dates[0] if dates else "")
            )
            return _date_prefix(value, date)
        return any(_date_prefix(value, date) for value in dates)
    if event.time_start is None:
        return date == UNKNOWN_DAY
    if date == UNKNOWN_DAY:
        return False
    return _date_prefix(event.time_start.date().isoformat(), date)


def _date_prefix(value: str, date: str) -> bool:
    return value == date if len(date) >= 10 else value.startswith(date)


def _event_days(event: EventRecord, document: EventDocument | None, mode: str) -> tuple[str, ...]:
    if document is not None:
        if mode == "first_capture" and document.time.capture_start is not None:
            return (document.time.capture_start.date().isoformat(),)
        if document.time.member_dates:
            return document.time.member_dates
        if document.time.capture_start is not None:
            return (document.time.capture_start.date().isoformat(),)
        return (UNKNOWN_DAY,)
    if event.time_start is None:
        return (UNKNOWN_DAY,)
    return (event.time_start.date().isoformat(),)


def _sort_value(event: EventRecord, document: EventDocument | None) -> tuple[int, str]:
    if document is not None:
        if document.time.capture_start is not None:
            return (0, time_sort_key(document.time.capture_start))
        if document.time.member_dates:
            return (0, document.time.member_dates[0])
    if event.time_start is not None:
        return (0, time_sort_key(event.time_start))
    return (1, "")


def _day_key(event: EventRecord, document: EventDocument | None) -> str:
    """事件归属的日期；没有可比较日期就是未知，不能退回 created_at。"""
    if document is not None:
        if document.time.capture_start is not None:
            return document.time.capture_start.date().isoformat()
        for value in document.time.member_dates:
            if len(value) == 10:
                return value
        return UNKNOWN_DAY
    if event.time_start is None:
        return UNKNOWN_DAY
    return event.time_start.date().isoformat()


def _calendar_parts(value: str) -> tuple[int, int] | None:
    try:
        if len(value) == 4:
            return int(value), 1
        if len(value) >= 7:
            return int(value[:4]), int(value[5:7])
    except ValueError:
        return None
    return None
