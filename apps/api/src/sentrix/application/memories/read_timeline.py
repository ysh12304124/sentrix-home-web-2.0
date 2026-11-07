"""时间线读取用例：年月日索引、按日分页与事件详情。

日期属于事件的语义：``time_start`` 是拍摄时间；缺失时归入 **``unknown``**，不退回
``created_at``（上传时间不是拍摄时间）。索引、计数和按日列表共用同一组 SQL 谓词，
前端不需要在已加载的一页上自行筛选。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sentrix.domain.media.models import MediaType
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.ports import EventRepository
from sentrix.domain.places.ports import AssetPlaceRepository

DEFAULT_SCOPE_ID = "home-default"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
EVENT_SCAN_LIMIT = 1000
#: 没有拍摄时间的事件桶。与日期筛选参数共用一个字面量。
UNKNOWN_DAY = "unknown"


@dataclass(frozen=True, slots=True)
class TimelinePhoto:
    asset_id: str
    file_name: str
    captured_at: datetime | None
    place: str


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
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._places = places

    def execute(
        self,
        *,
        scope_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        place: str = "",
        has_place: bool = False,
        date: str = "",
    ) -> TimelineResult:
        target_scope = scope_id or None
        page_size = max(1, min(int(limit), MAX_LIMIT))
        start = max(0, int(offset))
        events = list(
            self._events.list_by_scope(
                target_scope,
                limit=min(EVENT_SCAN_LIMIT, start + page_size),
                offset=start,
                place=place,
                has_place=has_place,
                date=date,
            )
        )[:page_size]

        asset_ids_by_event = self._asset_ids_many([event.id for event in events])
        asset_ids = [asset_id for ids in asset_ids_by_event.values() for asset_id in ids]
        assets = self._assets.get_many(asset_ids)

        days: list[_DayBucket] = []
        for event in events:
            key = _day_key(event)
            if not days or days[-1].date != key:
                days.append(_DayBucket(date=key))
            days[-1].events.append(
                self._summarize(event, asset_ids_by_event.get(event.id, ()), assets)
            )

        return TimelineResult(
            scope_id=target_scope or "",
            days=tuple(TimelineDay(date=day.date, events=tuple(day.events)) for day in days),
            total=self._events.count_by_scope(
                target_scope, place=place, has_place=has_place, date=date
            ),
            offset=start,
            limit=page_size,
        )

    def calendar(
        self, scope_id: str | None = None, *, place: str = "", has_place: bool = False
    ) -> CalendarIndex:
        rows = self._events.calendar(scope_id or None, place=place, has_place=has_place)
        years: dict[int, dict[int, list[CalendarDay]]] = {}
        year_counts: dict[int, int] = {}
        month_counts: dict[tuple[int, int], int] = {}
        unknown_count = 0
        for date, count in rows:
            if date == UNKNOWN_DAY:
                unknown_count += count
                continue
            try:
                year = int(date[:4])
                month = int(date[5:7])
            except (TypeError, ValueError):
                continue
            years.setdefault(year, {}).setdefault(month, []).append(
                CalendarDay(date=date, count=count)
            )
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
            timeline_date=_day_key(event),
        )

    # ---- 内部 ------------------------------------------------------------------

    def _summarize(
        self,
        event: EventRecord,
        asset_ids: tuple[str, ...] | list[str],
        assets: dict[str, object],
    ) -> TimelineEventSummary:
        representative = next(
            (assets[asset_id] for asset_id in asset_ids if asset_id in assets), None
        )
        image = next(
            (
                assets[asset_id]
                for asset_id in asset_ids
                if asset_id in assets
                and getattr(assets[asset_id], "media_type", None) is MediaType.IMAGE
            ),
            None,
        )
        chosen = image or representative
        return TimelineEventSummary(
            id=event.id,
            title=event.title,
            time_start=event.time_start,
            time_end=event.time_end,
            place=event.place,
            admin_district=event.admin_district,
            summary=event.summary,
            representative_asset_id=getattr(chosen, "id", None),
            representative_media_type=(
                getattr(getattr(chosen, "media_type", None), "value", None)
            ),
            asset_count=len(asset_ids),
        )

    def _asset_ids_many(self, event_ids: list[str]) -> dict[str, list[str]]:
        """一页事件的素材关联一次取完，再按拍摄时间排序去重。"""
        if not event_ids:
            return {}
        observation_ids_by_event = self._events.observation_ids_many(event_ids)
        all_observation_ids = [
            observation_id
            for ids in observation_ids_by_event.values()
            for observation_id in ids
        ]
        observations = self._observations.get_many(all_observation_ids)
        all_asset_ids = [
            observation.asset_id
            for observation in observations.values()
            if observation.asset_id
        ]
        assets = self._assets.get_many(all_asset_ids)

        result: dict[str, list[str]] = {}
        for event_id in event_ids:
            ordered: list[tuple[datetime, str]] = []
            for observation_id in observation_ids_by_event.get(event_id, ()):
                observation = observations.get(observation_id)
                if observation is None:
                    continue
                asset = assets.get(observation.asset_id)
                if asset is None:
                    continue
                ordered.append((asset.captured_at or asset.created_at, asset.id))
            ordered.sort(key=lambda item: (item[0], item[1]))
            result[event_id] = list(dict.fromkeys(asset_id for _, asset_id in ordered))
        return result


def _day_key(event: EventRecord) -> str:
    """事件归属的日期。**没有 time_start 就是未知**，不能退回 created_at——
    那是上传时间，不是拍摄时间。"""
    if event.time_start is None:
        return UNKNOWN_DAY
    return event.time_start.date().isoformat()
