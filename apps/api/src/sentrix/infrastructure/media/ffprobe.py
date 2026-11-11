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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# 1.0 ``metadata.py:9-10`` 的两条正则，逐字照抄。
ISO6709_RE = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/?$")
COMPACT_TZ_RE = re.compile(r"([+-]\d{2})(\d{2})$")

# 1.0 的 timeout 是 60 秒（``metadata.py:43``）。读元数据只解容器头，与转码
# （可能几分钟）不是一件事，不该共用一个上限。
DEFAULT_PROBE_TIMEOUT_S = 60


def run_ffprobe(path: Path, *, timeout_s: int = DEFAULT_PROBE_TIMEOUT_S) -> dict[str, Any]:
    """跑一次 ``ffprobe`` 拿整包 JSON。非零退出或输出不是 JSON 一律 ``ValueError``。

    ``-show_format`` 与 ``-show_streams`` 缺一不可：拍摄时间/GPS/总时长在 format 上，
    编码与各流自己的参数（帧率 / 采样率 / 声道）在 streams 上。
    """
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(Path(path).resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if process.returncode:
        raise ValueError(f"ffprobe failed: {process.stderr.strip()[-1000:]}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"ffprobe output is not JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("ffprobe output is not a JSON object")
    return payload


def count_video_frames(path: Path, *, timeout_s: int = DEFAULT_PROBE_TIMEOUT_S) -> int:
    """缺时长时用 ``-count_frames`` 数一遍视频帧。

    少数 WebM 只有帧时间戳、容器和流上都没有 duration/nb_frames（例如从剪辑软件
    导出的片段）。它们仍能解码，只是拿不到时长；这时再跑一次带 ``-count_frames`` 的
    ffprobe，配合帧率就能回推出时长。只在 ``duration <= 0`` 时调用，正常文件不走这条
    解码整片的重路径。
    """
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(Path(path).resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if process.returncode:
        raise ValueError(f"ffprobe frame count failed: {process.stderr.strip()[-1000:]}")
    try:
        payload = json.loads(process.stdout)
        streams = payload.get("streams") or []
        return int(streams[0].get("nb_read_frames") or 0) if streams else 0
    except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
        raise ValueError(f"ffprobe frame count output is invalid: {error}") from error


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
