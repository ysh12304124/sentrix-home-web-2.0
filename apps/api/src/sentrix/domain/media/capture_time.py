"""拍摄时间规则。无时区的日历查询按拍摄当地时间解释。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class CaptureTime:
    raw: str = ""
    source: str = "unknown"
    timezone: str = ""
    status: str = "unknown"


class CoordinateTimezone(Protocol):
    def __call__(self, latitude: float, longitude: float) -> str: ...


def restore_capture_timezone(value: datetime | None, evidence: CaptureTime) -> datetime | None:
    """ISO落库保留offset；恢复IANA时区以正确计算跨夏令时的派生帧。"""
    if value is not None and value.utcoffset() is not None and evidence.timezone:
        return value.astimezone(ZoneInfo(evidence.timezone))
    return value


def within_time_bounds(bounds: tuple[datetime, datetime], value: datetime | None) -> bool | None:
    if value is None:
        return None
    start, end = bounds
    if (start.utcoffset() is None) != (end.utcoffset() is None):
        raise ValueError("时间窗两端必须使用相同的时区语义")
    if start.utcoffset() is None:
        # 未指定查询时区：用每个媒体的拍摄当地日历，不用服务器日历。
        return start <= value.replace(tzinfo=None) < end
    if value.utcoffset() is None:
        return None
    return start.astimezone(UTC) <= value.astimezone(UTC) < end.astimezone(UTC)


def time_sort_key(value: datetime | None) -> str:
    """已知绝对时刻、未知偏移墙上时间、无时间分组；组内各自排序。"""
    if value is None:
        return ""
    if value.utcoffset() is None:
        return "0:" + value.isoformat(timespec="microseconds")
    return "1:" + value.astimezone(UTC).isoformat(timespec="microseconds")


def elapsed_seconds(one: datetime, two: datetime) -> float:
    if (one.utcoffset() is None) != (two.utcoffset() is None):
        raise ValueError("未知时区时间不能与绝对时刻相减")
    if one.utcoffset() is not None:
        return (one.astimezone(UTC) - two.astimezone(UTC)).total_seconds()
    return (one - two).total_seconds()


def offset_capture(value: datetime | None, seconds: float) -> datetime | None:
    if value is None:
        return None
    delta = timedelta(seconds=seconds)
    if value.utcoffset() is None:
        return value + delta
    return (value.astimezone(UTC) + delta).astimezone(value.tzinfo)
