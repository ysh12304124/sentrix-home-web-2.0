"""离线坐标时区推定；保留元数据原值与推定状态。"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from sentrix.domain.media.capture_time import BEIJING, CaptureTime, CoordinateTimezone
from sentrix.domain.media.models import GpsPoint


@lru_cache(maxsize=4096)
def coordinate_timezone(latitude: float, longitude: float) -> str:
    import math

    from tzfpy import get_tzs

    # 单点重叠或邻近时区边界不猜名字；约200米采样只是保守检查，不是精确边界认证。
    dx = 0.002 / max(0.01, math.cos(math.radians(latitude)))
    zones: set[str] = set()
    for dy in (-0.002, 0.0, 0.002):
        for shift in (-dx, 0.0, dx):
            names = get_tzs((longitude + shift + 180) % 360 - 180, min(90, max(-90, latitude + dy)))
            if len(names) != 1:
                return ""
            zones.update(names)
    return next(iter(zones)) if len(zones) == 1 else ""


class ResolveCaptureTime:
    def __init__(self, timezone_at: CoordinateTimezone = coordinate_timezone) -> None:
        self._timezone_at = timezone_at

    def __call__(
        self, value: datetime | None, gps: GpsPoint | None, source: str
    ) -> tuple[datetime | None, CaptureTime]:
        if value is None:
            return None, CaptureTime()
        raw = value.isoformat()
        name = self._timezone_at(gps.latitude, gps.longitude) if gps else ""
        if value.utcoffset() is not None:
            local = value.astimezone(ZoneInfo(name)) if name else value
            status = "offset_gps_local" if name else "offset_only"
            return local.astimezone(BEIJING), CaptureTime(
                raw, source, "Asia/Shanghai", status, name
            )
        if not name:
            # 没有 GPS/时区时，按用户的北京时间日历解释 naive EXIF 时间。
            return value.replace(tzinfo=BEIJING), CaptureTime(
                raw, source, "Asia/Shanghai", "beijing_assumed"
            )
        zone = ZoneInfo(name)
        choices = [value.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
        valid = [v for v in choices if v.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value]
        if not valid or len({v.utcoffset() for v in valid}) != 1:
            status = "ambiguous_local_time" if valid else "nonexistent_local_time"
            # 无法唯一还原当地 offset 时仍按北京时间落库，避免再出现无时区行。
            return value.replace(tzinfo=BEIJING), CaptureTime(
                raw, source, "Asia/Shanghai", status, name
            )
        return valid[0].astimezone(BEIJING), CaptureTime(
            raw, source, "Asia/Shanghai", "gps_timezone_inferred", name
        )
