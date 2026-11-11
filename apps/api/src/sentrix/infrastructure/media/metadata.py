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
MAKE_TAG = 271
MODEL_TAG = 272

GPS_LATITUDE_REF, GPS_LATITUDE = 1, 2
GPS_LONGITUDE_REF, GPS_LONGITUDE = 3, 4


def _rational(value: Any) -> float | None:
    """EXIF 有理数转 float；``0/0`` 这类占位值返回 ``None``。

    部分手机把整组 GPS 写成 ``0/0``，Pillow 读出来是 ``IFDRational(0, 0)``；
    ``float()`` 在 Python 3.11 会直接除零。先看分母，既避开异常，也不会把
    0,0 误当成一个真实坐标。
    """
    if getattr(value, "denominator", None) == 0:
        return None
    try:
        return float(value[0]) / float(value[1])
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _degrees_minutes_seconds(
    values: Sequence[object], *, negative_ref: str, ref_value: str
) -> float | None:
    """度分秒；任一分量是无效有理数时整组坐标作废，返回 ``None``。"""
    numbers: list[float] = []
    for item in values:
        number = _rational(item)
        if number is None:
            return None
        numbers.append(number)
    if not numbers:
        return None
    # 60**index 在 typeshed 里是 Any（int.__pow__ 要覆盖负指数返回 float 的情形），
    # 所以给 degrees 落一个显式标注，把实数类型钉住；表达式与运行结果都不变。
    degrees: float = sum(value / (60**index) for index, value in enumerate(numbers))
    if _text(ref_value).upper() == negative_ref:
        degrees *= -1
    return degrees


# EXIF 的时间格式不统一：有无 OffsetTime、有无小数秒都要能解析。
_DATETIME_FORMATS = (
    "%Y:%m:%d %H:%M:%S.%f%z",
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
    raw = (
        tags.get(DATETIME_ORIGINAL_TAG)
        or tags.get(DATETIME_DIGITIZED_TAG)
        or tags.get(DATETIME_TAG)
    )
    if not raw:
        return None
    text = _text(raw)
    # 有 DateTimeOriginal 时优先用它的 offset；否则退回通用 offset。
    offset_tag = OFFSET_TIME_ORIGINAL_TAG if tags.get(DATETIME_ORIGINAL_TAG) else OFFSET_TIME_TAG
    offset = _text(tags.get(offset_tag) or "")
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


def _parse_xmp_capture(image: object) -> datetime | None:
    """XMP 的 DateCreated/CreateDate；编辑导出图常保留它而丢掉 EXIF。"""
    info = getattr(image, "info", {}) or {}
    raw = info.get("xmp") or info.get("XML:com.adobe.xmp") or ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    text = str(raw)
    if not text:
        return None
    patterns = (
        r"<photoshop:DateCreated>([^<]+)</photoshop:DateCreated>",
        r"<xmp:CreateDate>([^<]+)</xmp:CreateDate>",
        r'(?:photoshop:DateCreated|xmp:CreateDate)="([^"]+)"',
        r"(20\d{2}[-:]\d{2}[-:]\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2})?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = match.group(1).strip()
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
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
                xmp_captured = _parse_xmp_capture(image)
        except Exception:
            log.warning("exif_read_failed path=%s", path, exc_info=True)
            return None, None, None

        captured_at = _parse_exif_datetime(tags) or xmp_captured

        point: GpsPoint | None = None
        latitude_values = gps_tags.get(GPS_LATITUDE)
        longitude_values = gps_tags.get(GPS_LONGITUDE)
        if latitude_values and longitude_values:
            latitude = _degrees_minutes_seconds(
                latitude_values,
                negative_ref="S",
                ref_value=gps_tags.get(GPS_LATITUDE_REF, "N"),
            )
            longitude = _degrees_minutes_seconds(
                longitude_values,
                negative_ref="W",
                ref_value=gps_tags.get(GPS_LONGITUDE_REF, "E"),
            )
            if latitude is not None and longitude is not None:
                try:
                    point = GpsPoint(latitude=latitude, longitude=longitude)
                except (ValueError, TypeError):
                    log.warning("exif_gps_invalid path=%s", path, exc_info=True)
                    point = None

        make = _text(tags.get(MAKE_TAG) or "")
        model = _text(tags.get(MODEL_TAG) or "")
        device = model or make or None

        return captured_at, point, device
