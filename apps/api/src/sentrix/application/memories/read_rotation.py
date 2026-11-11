"""回忆首页的候选选取、稳定序列与游标。

后端决定顺序；前端只负责展示。序列在当前自然日内稳定，新导入只在下一轮生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentrix.domain.media.import_jobs import ImportJobStatus
from sentrix.domain.media.models import AssetStatus, MediaType
from sentrix.domain.media.ports import AssetRepository, ObservationRepository, VideoRepository
from sentrix.domain.memories.ports import EventRepository
from sentrix.domain.memories.rotation import RotationMedia, RotationRepository

PAGE_SIZE = 10
RECENT_VIEWED_LIMIT = 10
ACTIVE_JOB_STATUSES = {
    ImportJobStatus.ACCEPTED.value,
    ImportJobStatus.QUEUED.value,
    ImportJobStatus.RUNNING.value,
    ImportJobStatus.WAITING_DEPENDENCY.value,
    ImportJobStatus.RETRY_SCHEDULED.value,
}


@dataclass(frozen=True, slots=True)
class RotationPage:
    sequence_id: str
    day: str
    cursor: int
    has_more: bool
    media: tuple[RotationMedia, ...] = ()
    empty_reason: str = ""


@dataclass(slots=True)
class _Candidate:
    media: RotationMedia
    epoch: float


class ReadRotation:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        videos: VideoRepository,
        rotations: RotationRepository,
        jobs,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._videos = videos
        self._rotations = rotations
        self._jobs = jobs
        self._clock = clock

    def execute(
        self, scope_id: str | None, *, cursor: int | None = None, page_size: int = PAGE_SIZE
    ) -> RotationPage:
        now = self._clock()
        day = now.date().isoformat()
        scope_key = scope_id or ""
        sequence = self._rotations.sequence_for(scope_key, day)

        if sequence is None:
            candidates = self._candidates(scope_id)
            if not candidates:
                return RotationPage(
                    sequence_id="",
                    day=day,
                    cursor=0,
                    has_more=False,
                    empty_reason=self._empty_reason(scope_id),
                )
            viewed = self._rotations.recently_viewed(scope_key, limit=RECENT_VIEWED_LIMIT)
            ordered = self._order(candidates, viewed)
            sequence = self._rotations.create_sequence(
                scope_key, day, tuple(item.media.event_id for item in ordered), now=now
            )
            position = 0
            # 已经算过全部候选，这一页直接复用；再调一次 _candidates 会把同一批
            # 事件与观察重新查一遍（4800 份资料时这一跳就是几秒）。
            media_by_event = {item.media.event_id: item.media for item in candidates}
        else:
            position = cursor if cursor is not None else sequence.position
            media_by_event = {}

        if position >= len(sequence.event_ids):
            # 本轮已放完：刷新时开新一轮，而不是判空。优先排除最近看过的，
            # 不够时按既有规则放宽。正在播放的页面不受影响（客户端持有旧卡片）。
            fresh = self._candidates(scope_id)
            if not fresh:
                return RotationPage(
                    sequence_id=sequence.sequence_id,
                    day=day,
                    cursor=position,
                    has_more=False,
                    empty_reason=self._empty_reason(scope_id),
                )
            viewed = self._rotations.recently_viewed(scope_key, limit=RECENT_VIEWED_LIMIT)
            ordered = self._order(fresh, viewed)
            self._rotations.replace_sequence(
                sequence.sequence_id,
                tuple(item.media.event_id for item in ordered),
                now=now,
            )
            sequence = self._rotations.sequence_for(scope_key, day) or sequence
            position = 0
            media_by_event = {item.media.event_id: item.media for item in fresh}
        cards: list[RotationMedia] = []
        index = position
        if media_by_event:
            while index < len(sequence.event_ids) and len(cards) < page_size:
                card = media_by_event.get(sequence.event_ids[index])
                if card is not None:
                    cards.append(card)
                index += 1
        else:
            # 序列已存在时只需要当前页附近的事件，不能为了 10 张卡片把整册
            # 事件与观察重新拼一遍（这是首次加载后每翻一页都慢的主因）。
            chunk_size = max(page_size * 3, 30)
            while index < len(sequence.event_ids) and len(cards) < page_size:
                chunk = sequence.event_ids[index : index + chunk_size]
                chunk_media = {
                    item.media.event_id: item.media
                    for item in self._candidates(scope_id, event_ids=chunk)
                }
                for event_id in chunk:
                    index += 1
                    card = chunk_media.get(event_id)
                    if card is not None:
                        cards.append(card)
                    if len(cards) >= page_size:
                        break
        next_cursor = index
        has_more = next_cursor < len(sequence.event_ids) and len(sequence.event_ids) > 1
        if cards:
            self._rotations.advance(sequence.sequence_id, next_cursor, now=now)
        if not cards:
            return RotationPage(
                sequence_id=sequence.sequence_id,
                day=day,
                cursor=next_cursor,
                has_more=False,
                empty_reason="no_eligible_event",
            )
        return RotationPage(
            sequence_id=sequence.sequence_id,
            day=day,
            cursor=next_cursor,
            has_more=has_more,
            media=tuple(cards),
        )

    def mark_viewed(self, sequence_id: str, event_id: str) -> None:
        self._rotations.mark_viewed(sequence_id, event_id, now=self._clock())

    # ---- 候选与排序 ---------------------------------------------------------

    def _candidates(
        self, scope_id: str | None, *, event_ids: tuple[str, ...] | None = None
    ) -> list[_Candidate]:
        wanted = set(event_ids) if event_ids is not None else None
        # 视频场景事件在新版本里就是**视频本身**的唯一事件（关键帧已折进去）。
        # 回忆首页要展示源视频，不能整类跳过；_event_media 会把关键帧观察解析回父视频。
        events = [
            event
            for event in self._events.list_by_scope(scope_id, limit=10000)
            if wanted is None or event.id in wanted
        ]
        if not events:
            return []

        # 逐事件、逐观察、逐资产各开一次连接是回忆首屏慢的主因：4800 份资料会有
        # 上万次 SQLite 连接。这里全部按批取，事件与素材的对应关系在内存里组装。
        observation_ids_by_event = self._events.observation_ids_many([event.id for event in events])
        all_observation_ids = [
            observation_id
            for ids in observation_ids_by_event.values()
            for observation_id in ids
        ]
        observations = self._observations.get_many(all_observation_ids)
        assets = self._assets.get_many(
            [
                observation.asset_id
                for observation in observations.values()
                if observation.asset_id
            ]
        )
        # 视频事件的观察挂在关键帧上；回忆卡片要展示源视频，所以把父视频一并取回。
        parent_ids = {
            asset.parent_asset_id
            for asset in assets.values()
            if asset.parent_asset_id is not None
        }
        assets.update(
            {
                asset.id: asset
                for asset in self._assets.get_many([item for item in parent_ids if item]).values()
            }
        )
        videos_with_derived = self._videos.derived_parent_ids(
            [
                asset.id
                for asset in assets.values()
                if asset.media_type is MediaType.VIDEO
            ]
        )
        file_ok: dict[str, bool] = {}
        result: list[_Candidate] = []
        for event in events:
            media = self._event_media(
                event,
                observation_ids_by_event.get(event.id, ()),
                observations,
                assets,
                videos_with_derived,
                file_ok,
            )
            if media is None:
                continue
            result.append(_Candidate(media=media, epoch=media.sort_epoch))
        return result

    def _event_media(
        self,
        event,
        observation_ids,
        observations,
        assets,
        videos_with_derived,
        file_ok,
    ) -> RotationMedia | None:
        if not observation_ids:
            return None
        photos: list[tuple[float, str, object]] = []
        videos: list[tuple[float, str, object]] = []
        related = 0
        midpoint = _midpoint(event.time_start, event.time_end)
        for observation_id in observation_ids:
            observation = observations.get(observation_id)
            if observation is None:
                continue
            asset = assets.get(observation.asset_id)
            if asset is None or asset.status is not AssetStatus.PROCESSED:
                continue
            # 关键帧/转码件不是用户资料；回忆里只出现原图与原视频。
            # 命中关键帧时换成它的父视频，而不是把视频事件整条丢掉。
            if asset.parent_asset_id is not None:
                asset = assets.get(asset.parent_asset_id)
                if asset is None:
                    continue
            exists = file_ok.get(asset.id)
            if exists is None:
                exists = Path(asset.path).is_file()
                file_ok[asset.id] = exists
            if not exists:
                continue
            related += 1
            # 与事件中点越近越优先；等距按稳定 ID。缺少拍摄时间时视为与中点重合。
            captured = _as_utc(asset.captured_at) or midpoint
            distance = abs(captured.timestamp() - midpoint.timestamp())
            if asset.media_type is MediaType.IMAGE:
                photos.append((distance, asset.id, asset))
            elif asset.media_type is MediaType.VIDEO and asset.id in videos_with_derived:
                videos.append((distance, asset.id, asset))
        chosen = min(photos, key=lambda item: (item[0], item[1])) if photos else (
            min(videos, key=lambda item: (item[0], item[1])) if videos else None
        )
        if chosen is None:
            return None
        asset = chosen[2]
        kind = "image" if asset.media_type is MediaType.IMAGE else "video"
        return RotationMedia(
            event_id=event.id,
            asset_id=asset.id,
            media_kind=kind,
            title=(event.title or "").strip(),
            summary=(event.summary or "").strip(),
            time_label=_time_label(event.time_start),
            place_label=(event.place or event.admin_district or "").strip(),
            related_asset_count=related,
            # 视频原片与它的关键帧场景共享同一原片血缘，不能被排到相邻两页。
            source_key=asset.parent_asset_id or asset.id,
            sort_epoch=midpoint.timestamp(),
        )

    def _order(self, candidates: list[_Candidate], viewed: frozenset[str]) -> list[_Candidate]:
        ordered = sorted(candidates, key=lambda item: (item.epoch, item.media.event_id))
        if len(ordered) <= 1:
            return ordered
        adjusted = [item for item in ordered if item.media.event_id not in viewed]
        if not adjusted:
            adjusted = ordered  # 先放宽“最近看过”
        median = (ordered[0].epoch + ordered[-1].epoch) / 2
        recent = [item for item in adjusted if item.epoch >= median]
        old = [item for item in adjusted if item.epoch < median]
        interleaved: list[_Candidate] = []
        while recent or old:
            if recent:
                interleaved.append(recent.pop(0))
            if old:
                interleaved.append(old.pop(0))
        return _avoid_adjacent(interleaved)

    def _empty_reason(self, scope_id: str | None) -> str:
        assets = self._assets.list_by_scope(scope_id, limit=1)
        if not assets:
            jobs = self._jobs.list_for_scope(scope_id, limit=50)
            if any(job.status in ACTIVE_JOB_STATUSES for job in jobs):
                return "processing"
            return "no_media"
        return "no_eligible_event"


def _as_utc(value: datetime | None) -> datetime | None:
    """事件与资产时间可能无时区；统一到 UTC 后再做差。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _midpoint(start: datetime | None, end: datetime | None) -> datetime:
    start = _as_utc(start)
    end = _as_utc(end)
    if start is None:
        return datetime.now(UTC)
    if end is None or end <= start:
        return start
    return start + (end - start) / 2


def _time_label(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def _avoid_adjacent(items: list[_Candidate]) -> list[_Candidate]:
    """同一原视频的场景不连续；只有一项时原样返回。"""
    if len(items) <= 2:
        return items
    result: list[_Candidate] = []
    pending = list(items)
    while pending:
        current = pending.pop(0)
        if result and current.media.source_key == result[-1].media.source_key:
            swap = next(
                (
                    index
                    for index, candidate in enumerate(pending)
                    if candidate.media.source_key != result[-1].media.source_key
                ),
                None,
            )
            if swap is not None:
                pending.insert(0, current)
                current = pending.pop(swap + 1)
        result.append(current)
    return result
