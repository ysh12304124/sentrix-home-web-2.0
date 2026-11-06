"""时间线读取用例：按 scope 取事件，按**日期**分组，每组带上照片与地点。

对应 1.0 ``app.py`` 的 ``/api/memories/timeline`` 一族端点（按 ``time_start`` 倒序取
``list_events``，客户端再按天分组）。2.0 把分组放在后端：日期属于事件的语义
（``time_start`` 缺失时退回 ``created_at``，这个决定不该让每个前端各写一遍），
而且分组在分页**之后**做——先取一页事件再分组，页才是「事件的页」。

**地点展示**（计划 Task 61）：事件的 ``place`` 直接来自 ``asset_places``（``AssignEvents``
建事件时写的那一列），照片卡片上的地点由 ``Place.display()`` 现算——
``浙江省杭州市萧山区 · 赵州镇大石桥村附近``。前端不做任何拼接，它拿到的就是最终字符串。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sentrix.domain.media.capture_time import time_sort_key
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.ports import EventRepository
from sentrix.domain.places.ports import AssetPlaceRepository

DEFAULT_SCOPE_ID = "home-default"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# 1.0 的 ``list_events(1000, scope_id)``：一次能取的活动事件上限。
EVENT_SCAN_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class TimelinePhoto:
    asset_id: str
    file_name: str
    captured_at: datetime | None
    # ``Place.display()`` 的原样输出；没有解析出地点时是空串（不是「未知」）。
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


@dataclass(frozen=True, slots=True)
class TimelineDay:
    date: str
    events: tuple[TimelineEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineResult:
    scope_id: str
    days: tuple[TimelineDay, ...] = ()
    # scope 内的**事件总数**（不是本页条数）：分页要能说出「还有多少」，
    # 而 1.0 的客户端只能靠「返回的条数少于请求的条数」猜到底了没有。
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_LIMIT


@dataclass(slots=True)
class _DayBucket:
    date: str
    events: list[TimelineEvent] = field(default_factory=list)


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
    ) -> TimelineResult:
        """按 scope 取一页事件并按天分组。

        ``place`` 是**服务端**的地点筛选（计划 Task 61）：前端只把用户输入原样传过来。
        匹配发生在 SQL 里（``place`` / ``admin_district`` 的子串），不是取回来再过滤——
        否则每一页的条数会随筛选条件变化，分页就说不清「还有没有下一页」。
        """
        # **空 scope 表示全部相册，不兜底成某个空间。** 这里原先是
        # ``scope_id or DEFAULT_SCOPE_ID``——把「用户在界面上选了全部相册」与
        # 「用户想查一个叫 home-default 的空间」当成了同一件事，而后者在 2.0 的
        # 空库里根本不存在。表现是「时间线一个事件都没有」，而事件其实好好地
        # 在 album1 里（17 个）。1.0 ``db.py:1514`` 的 ``if scope_id:`` 就是这个意思。
        target_scope = scope_id or None
        page_size = max(1, min(int(limit), MAX_LIMIT))
        start = max(0, int(offset))
        events = list(
            self._events.list_by_scope(
                target_scope,
                limit=min(EVENT_SCAN_LIMIT, start + page_size),
                offset=start,
                place=place,
            )
        )[:page_size]

        days: list[_DayBucket] = []
        for event in events:
            key = _day_key(event)
            if not days or days[-1].date != key:
                days.append(_DayBucket(date=key))
            days[-1].events.append(self._expand(event))

        return TimelineResult(
            # 回给调用方的是**用户请求的范围**，不是内部用于过滤的值：
            # 全部相册用空串表示（前端 `src/app.js` 的约定）。
            scope_id=target_scope or "",
            days=tuple(TimelineDay(date=day.date, events=tuple(day.events)) for day in days),
            total=self._events.count_by_scope(target_scope, place=place),
            offset=start,
            limit=page_size,
        )

    def get_event(self, event_id: str) -> TimelineEvent | None:
        event = self._events.get(event_id)
        return self._expand(event) if event is not None else None

    # ---- 内部 ------------------------------------------------------------------

    def _expand(self, event: EventRecord) -> TimelineEvent:
        asset_ids = self._asset_ids(event.id)
        places = self._places.get_many(asset_ids)
        photos: list[TimelinePhoto] = []
        for asset_id in asset_ids:
            asset = self._assets.get(asset_id)
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
        )

    def _asset_ids(self, event_id: str) -> list[str]:
        """事件里出现过的 asset，按拍摄时间排序、去重。"""
        ordered: list[tuple[str, str]] = []
        for observation_id in self._events.observation_ids(event_id):
            observation = self._observations.get(observation_id)
            if observation is None:
                continue
            asset = self._assets.get(observation.asset_id)
            if asset is None:
                continue
            ordered.append((time_sort_key(asset.captured_at or asset.created_at), asset.id))
        ordered.sort(key=lambda item: (item[0], item[1]))
        return list(dict.fromkeys(asset_id for _, asset_id in ordered))


def _day_key(event: EventRecord) -> str:
    """事件属于哪一天。``time_start`` 缺失时退回 ``created_at``。

    1.0 让客户端自己挑这个回退，于是同一份数据在两个前端上分到不同的天。
    """
    moment = event.time_start or event.created_at
    if moment is None:
        return ""
    return moment.date().isoformat()
