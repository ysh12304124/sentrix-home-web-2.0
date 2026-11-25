"""EXIF / GPS 抽取。移植自 1.0 ``backend/pipeline.py:251``。

1.0 静默吞掉一切异常并返回 ``{}``。2.0 保留"坏文件不阻断导入"的行为，
但把失败记进日志，避免 1.0 那种"字段悄悄没了没人知道"的问题。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.media.image_io import ensure_heif_support

log = logging.getLogger(__name__)

GPS_INFO_TAG = 34853
EXIF_IFD_TAG = 34665
DATETIME_ORIGINAL_TAG = 36867
DATETIME_DIGITIZED_TAG = 36868
DATETIME_TAG = 306
OFFSET_TIME_ORIGINAL_TAG = 36881
OFFSET_TIME_TAG = 36880
OFFSET_TIME_DIGITIZED_TAG = 36882
SUBSEC_TIME_TAG = 37520
SUBSEC_TIME_ORIGINAL_TAG = 37521
SUBSEC_TIME_DIGITIZED_TAG = 37522
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
    if _text(ref_value).upper() == negative_ref:
        degrees *= -1
    return degrees


# EXIF 的时间格式不统一：有无 OffsetTime、有无小数秒都要能解析。
_DATETIME_FORMATS = (
    "%Y:%m:%d %H:%M:%S.%f%z",
    "%Y:%m:%d %H:%M:%S.%f",
    "%Y:%m:%d %H:%M:%S%z",
    "%Y:%m:%d %H:%M:%S",
)


def _text(value: object) -> str:
    """EXIF 的值可能是 str 或带 NUL 填充的 bytes，统一成可解析的文本。"""
    if isinstance(value, bytes):
        return value.decode("ascii", "ignore").strip("\x00 ")
    return str(value).strip()


def _parse_exif_datetime(tags: dict[int, object]) -> datetime | None:
    # DateTimeOriginal / DateTimeDigitized 常在 Exif 子 IFD 里，DateTime 在 IFD0。
    # 三处都看：只读顶层会漏掉「只有 DateTimeOriginal」的相机文件。
    candidates = (
        (DATETIME_ORIGINAL_TAG, OFFSET_TIME_ORIGINAL_TAG, SUBSEC_TIME_ORIGINAL_TAG),
        (DATETIME_DIGITIZED_TAG, OFFSET_TIME_DIGITIZED_TAG, SUBSEC_TIME_DIGITIZED_TAG),
        (DATETIME_TAG, OFFSET_TIME_TAG, SUBSEC_TIME_TAG),
    )
    raw_values: list[object] = []
    for datetime_tag, offset_tag, subsecond_tag in candidates:
        raw = tags.get(datetime_tag)
        if not raw:
            continue
        raw_values.append(raw)
        text = _text(raw)
        # SubSecTime* is the standard EXIF representation of fractional seconds.
        # Preserve at Python's microsecond precision; DateTimeOriginal can also
        # already contain a fraction in non-standard camera files.
        subsecond = _text(tags.get(subsecond_tag) or "").lstrip(".")
        if subsecond and "." not in text:
            fraction = (subsecond + "000000")[:6]
            text = f"{text}.{fraction}"
        offset = _text(tags.get(offset_tag) or "")
        # OffsetTime is tied to its matching DateTime* tag. Never borrow an
        # unrelated offset (for example DateTime's offset for DateTimeOriginal).
        has_offset = bool(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text))
        if offset.startswith(("+", "-")) and len(offset) >= 5 and not has_offset:
            text = f"{text}{offset}"
        for fmt in _DATETIME_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    if raw_values:
        log.warning("exif_datetime_unparseable raw=%r", raw_values)
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
                for tag in (
                    DATETIME_ORIGINAL_TAG,
                    DATETIME_DIGITIZED_TAG,
                    DATETIME_TAG,
                    OFFSET_TIME_ORIGINAL_TAG,
                    OFFSET_TIME_DIGITIZED_TAG,
                    OFFSET_TIME_TAG,
                    SUBSEC_TIME_ORIGINAL_TAG,
                    SUBSEC_TIME_DIGITIZED_TAG,
                    SUBSEC_TIME_TAG,
                ):
                    if tag in capture_tags:
                        tags[tag] = capture_tags[tag]
                try:
                    # Exif 子 IFD 才是 DateTimeOriginal / OffsetTimeOriginal 的家。
                    # 顶层优先，子 IFD 补缺；只取顶层会把这些文件当成「没有拍摄时间」。
                    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif) or {}
                    if isinstance(exif_ifd, dict):
                        tags = {**exif_ifd, **tags}
                except Exception:
                    log.warning("exif_sub_ifd_read_failed path=%s", path, exc_info=True)
                try:
                    gps_tags = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
                except Exception:
                    gps_tags = tags.get(GPS_INFO_TAG) or {}
                if not isinstance(gps_tags, dict):
                    gps_tags = {}
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

        make = _text(tags.get(MAKE_TAG) or "")
        model = _text(tags.get(MODEL_TAG) or "")
        device = model or make or None

        return captured_at, point, device
