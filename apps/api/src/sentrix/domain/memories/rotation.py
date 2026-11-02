"""回忆序列的读模型与持久化契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RotationMedia:
    event_id: str
    asset_id: str
    media_kind: str  # image | video
    title: str
    summary: str
    time_label: str
    place_label: str
    related_asset_count: int
    # 同一原视频的不同场景相邻时要错开。
    source_key: str
    sort_epoch: float


@dataclass(frozen=True, slots=True)
class RotationSequence:
    sequence_id: str
    scope_id: str
    day: str
    event_ids: tuple[str, ...]
    position: int


class RotationRepository(Protocol):
    def sequence_for(self, scope_id: str, day: str) -> RotationSequence | None: ...

    def create_sequence(
        self, scope_id: str, day: str, event_ids: tuple[str, ...], *, now: datetime
    ) -> RotationSequence: ...

    def advance(self, sequence_id: str, position: int, *, now: datetime) -> None: ...

    def recently_viewed(self, scope_id: str, *, limit: int) -> frozenset[str]: ...

    def mark_viewed(
        self, sequence_id: str, event_id: str, *, now: datetime
    ) -> None: ...
