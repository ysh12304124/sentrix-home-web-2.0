"""EXIF / GPS 抽取。移植自 1.0 ``backend/pipeline.py:251``。

1.0 静默吞掉一切异常并返回 ``{}``。2.0 保留"坏文件不阻断导入"的行为，
但把失败记进日志，避免 1.0 那种"字段悄悄没了没人知道"的问题。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.media.image_io import ensure_heif_support

log = logging.getLogger(__name__)

GPS_INFO_TAG = 34853
DATETIME_ORIGINAL_TAG = 36867
DATETIME_TAG = 306
OFFSET_TIME_ORIGINAL_TAG = 36881
OFFSET_TIME_TAG = 36880
MAKE_TAG = 271
MODEL_TAG = 272

GPS_LATITUDE_REF, GPS_LATITUDE = 1, 2
GPS_LONGITUDE_REF, GPS_LONGITUDE = 3, 4


def _rational(value: Any) -> float:
    try:
        return float(value[0]) / float(value[1])
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return float(value)


def _degrees_minutes_seconds(values: Sequence[object], *, negative_ref: str, ref_value: str) -> float:
    # 60**index 在 typeshed 里是 Any（int.__pow__ 要覆盖负指数返回 float 的情形），
    # 所以给 degrees 落一个显式标注，把实数类型钉住；表达式与运行结果都不变。
    degrees: float = sum(_rational(item) / (60**index) for index, item in enumerate(values))
    if str(ref_value).upper() == negative_ref:
        degrees *= -1
    return degrees


# EXIF 的时间格式不统一：有无 OffsetTime、有无小数秒都要能解析。
_DATETIME_FORMATS = (
    "%Y:%m:%d %H:%M:%S.%f%z",
    "%Y:%m:%d %H:%M:%S%z",
    "%Y:%m:%d %H:%M:%S",
)


def _parse_exif_datetime(tags: dict[int, object]) -> datetime | None:
    raw = tags.get(DATETIME_ORIGINAL_TAG) or tags.get(DATETIME_TAG)
    if not raw:
        return None
    text = str(raw).strip()
    offset_tag = OFFSET_TIME_ORIGINAL_TAG if tags.get(DATETIME_ORIGINAL_TAG) else OFFSET_TIME_TAG
    offset = str(tags.get(offset_tag) or "").strip()
    # EXIF 的 offset 写作 "+0800"，strptime 的 %z 接受 "+08:00" 也接受 "+0800"，直接拼接即可
    if offset.startswith(("+", "-")) and len(offset) >= 5:
        text = f"{text}{offset}"
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    log.warning("exif_datetime_unparseable raw=%r", raw)
    return None


class PillowMetadataReader:
    def read(self, path: Path) -> tuple[datetime | None, GpsPoint | None, str | None]:
        """返回 (拍摄时间, GPS, 设备型号)；任何一步失败都降级为 None。"""
        try:
            from PIL import ExifTags, Image

            ensure_heif_support()
            with Image.open(path) as image:
                exif = image.getexif()
                tags = {key: value for key, value in exif.items()}
                # Original capture time and its offset belong to the Exif IFD.
                try:
                    capture_tags = exif.get_ifd(ExifTags.IFD.Exif) or {}
                except (KeyError, TypeError, ValueError, OSError):
                    capture_tags = {}
                for tag in (DATETIME_ORIGINAL_TAG, OFFSET_TIME_ORIGINAL_TAG, OFFSET_TIME_TAG):
                    if tag in capture_tags:
                        tags[tag] = capture_tags[tag]
                try:
                    gps_tags = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
                except Exception:
                    gps_tags = tags.get(GPS_INFO_TAG) or {}
        except Exception:
            log.warning("exif_read_failed path=%s", path, exc_info=True)
            return None, None, None

        captured_at = _parse_exif_datetime(tags)

        point: GpsPoint | None = None
        latitude_values = gps_tags.get(GPS_LATITUDE)
        longitude_values = gps_tags.get(GPS_LONGITUDE)
        if latitude_values and longitude_values:
            try:
                point = GpsPoint(
                    latitude=_degrees_minutes_seconds(
                        latitude_values,
                        negative_ref="S",
                        ref_value=gps_tags.get(GPS_LATITUDE_REF, "N"),
                    ),
                    longitude=_degrees_minutes_seconds(
                        longitude_values,
                        negative_ref="W",
                        ref_value=gps_tags.get(GPS_LONGITUDE_REF, "E"),
                    ),
                )
            except (ValueError, TypeError):
                log.warning("exif_gps_invalid path=%s", path, exc_info=True)
                point = None

        make = str(tags.get(MAKE_TAG) or "").strip()
        model = str(tags.get(MODEL_TAG) or "").strip()
        device = model or make or None

        return captured_at, point, device
