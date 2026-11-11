"""拍摄时间规则。无时区的日历查询按拍摄当地时间解释。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

_EPOCH_MS = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_DATE_TIME = re.compile(
    r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"[ _T.-]?([01]\d|2[0-3])[:.-]?([0-5]\d)[:.-]?([0-5]\d)(?!\d)"
)
# 只有日期没有时刻的文件名（`IMG_20240102.jpg`、`Screenshot 2024-01-02.png`）。
# 裸 8 位数字太容易是编号，必须带分隔符或文件名里有相机/聊天工具关键词才收。
_DATE_ONLY = re.compile(
    r"(?<!\d)(20\d{2})([-_.]?)(0[1-9]|1[0-2])(?:\2)(0[1-9]|[12]\d|3[01])(?!\d)"
)
_DATE_HINT = re.compile(
    r"(IMG|VID|PXL|DSC|Screenshot|Screen[ _-]?Shot|截图|截屏|屏幕|微信|WeChat|QQ|图片|照片|"
    r"mmexport|wx_camera|photo|image)",
    re.IGNORECASE,
)


def capture_time_from_filename(file_name: str) -> datetime | None:
    """从常见相机/微信/QQ/截图文件名恢复时间；日历时间保持 naive。"""
    stem = Path(file_name).stem
    for match in _DATE_TIME.finditer(stem):
        try:
            value = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), int(match.group(5)), int(match.group(6)),
            )
        except ValueError:
            continue
        if 2000 <= value.year <= 2100:
            return value
    for match in _DATE_ONLY.finditer(stem):
        separator = match.group(2)
        if not separator and _DATE_HINT.search(stem) is None:
            continue
        try:
            value = datetime(
                int(match.group(1)), int(match.group(3)), int(match.group(4))
            )
        except ValueError:
            continue
        if 2000 <= value.year <= 2100:
            return value
    for match in _EPOCH_MS.finditer(stem):
        milliseconds = int(match.group(1))
        if 946684800000 <= milliseconds <= 4102444800000:
            return datetime.fromtimestamp(milliseconds / 1000.0, tz=UTC)
    return None


def to_beijing(value: datetime | None) -> datetime | None:
    """已知绝对时刻/本地日历时间统一落到北京时间。naive 时间按北京时间解释。"""
    if value is None:
        return None
    if value.utcoffset() is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


@dataclass(frozen=True, slots=True)
class CaptureTime:
    raw: str = ""
    source: str = "unknown"
    timezone: str = ""
    status: str = "unknown"
    original_timezone: str = ""


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
