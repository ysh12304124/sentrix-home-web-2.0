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
        else:
            position = cursor if cursor is not None else sequence.position

        if position >= len(sequence.event_ids):
            return RotationPage(
                sequence_id=sequence.sequence_id,
                day=day,
                cursor=position,
                has_more=False,
            )

        media_by_event = {
            item.media.event_id: item.media for item in self._candidates(scope_id, event_ids=sequence.event_ids)
        }
        cards: list[RotationMedia] = []
        index = position
        while index < len(sequence.event_ids) and len(cards) < page_size:
            card = media_by_event.get(sequence.event_ids[index])
            if card is not None:
                cards.append(card)
            index += 1
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
        result: list[_Candidate] = []
        for event in self._events.list_by_scope(scope_id, limit=10000):
            if wanted is not None and event.id not in wanted:
                continue
            media = self._event_media(event)
            if media is None:
                continue
            result.append(_Candidate(media=media, epoch=media.sort_epoch))
        return result

    def _event_media(self, event) -> RotationMedia | None:
        observation_ids = self._events.observation_ids(event.id)
        if not observation_ids:
            return None
        photos: list[tuple[float, str, object]] = []
        videos: list[tuple[float, str, object]] = []
        related = 0
        midpoint = _midpoint(event.time_start, event.time_end)
        for observation_id in observation_ids:
            observation = self._observations.get(observation_id)
            if observation is None:
                continue
            asset = self._assets.get(observation.asset_id)
            if asset is None or asset.status is not AssetStatus.PROCESSED:
                continue
            if not Path(asset.path).is_file():
                continue
            related += 1
            if asset.media_type is MediaType.IMAGE:
                photos.append((-abs((asset.captured_at or midpoint).timestamp() - midpoint.timestamp()), asset.id, asset))
            elif asset.media_type is MediaType.VIDEO and self._videos.list_derived(asset.id):
                videos.append((-abs((asset.captured_at or midpoint).timestamp() - midpoint.timestamp()), asset.id, asset))
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
            source_key=(asset.parent_asset_id or asset.id) if kind == "video" else event.id,
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


def _midpoint(start: datetime | None, end: datetime | None) -> datetime:
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
