"""回忆首页的 HTTP 形状。只给后端确认过的字段与同源媒体 URL。"""

from __future__ import annotations

from pydantic import BaseModel


class RotationCard(BaseModel):
    event_id: str
    asset_id: str
    media_kind: str
    title: str = ""
    summary: str = ""
    time_label: str = ""
    place_label: str = ""
    related_asset_count: int = 0
    poster_url: str
    play_url: str | None = None


class RotationResponse(BaseModel):
    sequence_id: str
    day: str
    cursor: int
    has_more: bool
    empty_reason: str = ""
    media: list[RotationCard] = []


class RotationViewedRequest(BaseModel):
    sequence_id: str
    event_id: str
