"""``ffprobe`` 子进程与容器标签解析的**共用件**（视频与音频）。

视频（``video_metadata.py``）与音频（``audio_metadata.py``）两个适配器走的是同一条
命令、读的是同一批标签：拍摄时间与 GPS 对这两种媒体没有区别，它们都在 mp4/mov 的
**容器标签**里（``com.apple.quicktime.creationdate`` / ``location`` 等），与流是
视频还是音频无关。所以命令行、ISO6709 解析、时区规整只写一份——两处各写一遍的话，
「creationdate 取成 UTC 导致整体偏一个时区」这类 bug 要修两遍，而它不报错、只是错。

**为什么落在 infrastructure**：它起 ``ffprobe`` 子进程，``pyproject.toml`` 的
``domain-purity`` 与 ``application-purity`` 两条 import-linter 契约明令禁止
domain/application 碰子进程那类东西。

两个正则与解析口径逐字照抄 1.0 ``backend/video/metadata.py:9-10,31-45``，认的写法
与 1.0 一样多。失败形态是 1.0 的 ``ValueError``（端口契约里写了），由用例接住写进
``metadata_json``。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# 1.0 ``metadata.py:9-10`` 的两条正则，逐字照抄。
ISO6709_RE = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/?$")
COMPACT_TZ_RE = re.compile(r"([+-]\d{2})(\d{2})$")

# 1.0 的 timeout 是 60 秒（``metadata.py:43``）。读元数据只解容器头，与转码
# （可能几分钟）不是一件事，不该共用一个上限。
DEFAULT_PROBE_TIMEOUT_S = 60
DEFAULT_PROBE_RETRIES = 2


def run_ffprobe(
    path: Path,
    *,
    timeout_s: int = DEFAULT_PROBE_TIMEOUT_S,
    retries: int = DEFAULT_PROBE_RETRIES,
) -> dict[str, Any]:
    """读取 ``ffprobe`` 整包 JSON，并对瞬时探测失败做有限重试。

    整册导入时多个视频会同时占用磁盘/进程资源，macOS 上 ``ffprobe`` 偶发返回非零
    且 stderr 为空。以前一次失败就直接落成 ``video_probe_error``，既丢失了时长，也
    让后续只能走导入时间兜底。重试只放在 infrastructure，且次数有上限，不改变 1.0
    的命令和字段口径；最终失败时把 return code、stderr 和路径都留下，方便定位。

    ``-show_format`` 与 ``-show_streams`` 缺一不可：拍摄时间/GPS/总时长在 format 上，
    编码与各流自己的参数（帧率 / 采样率 / 声道）在 streams 上。
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(Path(path).resolve()),
    ]
    last_error = "ffprobe failed"
    for attempt in range(max(0, retries) + 1):
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if process.returncode:
            stderr = process.stderr.strip()[-1000:] or "no stderr"
            last_error = f"ffprobe failed (returncode={process.returncode}): {stderr}"
        else:
            try:
                payload = json.loads(process.stdout)
            except json.JSONDecodeError as error:
                last_error = f"ffprobe output is not JSON: {error}"
            else:
                if not isinstance(payload, dict):
                    last_error = "ffprobe output is not a JSON object"
                else:
                    return payload
        if attempt < max(0, retries):
            time.sleep(0.1 * (attempt + 1))
    raise ValueError(f"{last_error}; path={Path(path).resolve()}")


def normalized_datetime(value: object) -> str | None:
    """时区两种写法都要认：``+0800`` 补成 ``+08:00``，``Z`` 换成 ``+00:00``。"""
    text = str(value or "").strip()
    if not text:
        return None
    text = COMPACT_TZ_RE.sub(r"\1:\2", text)
    return text[:-1] + "+00:00" if text.endswith("Z") else text


def iso6709_location(tags: Mapping[str, object]) -> tuple[float | None, float | None]:
    """ISO6709 的 GPS：``com.apple.quicktime.location.ISO6709`` 或 ``location``。

    MP4/MOV 的定位不在 EXIF 里，只在这两个标签里。两个分量解析不出来就返回
    ``(None, None)``——半个坐标不该冒充一个完整位置。
    """
    raw = str(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location") or "").strip()
    match = ISO6709_RE.match(raw)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def capture_time(
    format_tags: Mapping[str, object], stream_tags: Mapping[str, object]
) -> tuple[str | None, str]:
    """拍摄时间与它的来源，按 1.0 的优先级。

    **QuickTime 的 ``com.apple.quicktime.creationdate`` 优先于通用的 ``creation_time``**
    （1.0 ``metadata.py:60-63``）：iPhone 导出把本地时区的拍摄时间放在前者，后者是 UTC，
    取错会整体偏一个时区。``creation_source`` 就是给这件事留下的可观测面。
    """
    captured_at = (
        format_tags.get("com.apple.quicktime.creationdate")
        or format_tags.get("creation_time")
        or stream_tags.get("creation_time")
    )
    creation_source = (
        "com.apple.quicktime.creationdate"
        if format_tags.get("com.apple.quicktime.creationdate")
        else "format.creation_time"
        if format_tags.get("creation_time")
        else "stream.creation_time"
    )
    return normalized_datetime(captured_at), creation_source
