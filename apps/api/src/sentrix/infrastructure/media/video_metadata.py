"""ffprobe 视频元数据适配器。移植自 1.0 ``backend/video/metadata.py``。

**为什么落在 infrastructure。** 它要起 ``ffprobe`` 子进程，而 ``pyproject.toml`` 的
``domain-purity`` 与 ``application-purity`` 两条 import-linter 契约明令禁止
domain/application 碰子进程那类东西。端口声明在 ``domain/media/ports.py``
（``VideoMetadataReader``），形状在 ``domain/media/models.py``（``VideoMetadata``）。

移植时保留 1.0 的全部抽取口径，一处不改：

- **QuickTime 的 ``com.apple.quicktime.creationdate`` 优先于通用的 ``creation_time``**
  （``1.0 metadata.py:60-63``）：iPhone 导出把本地时区的拍摄时间放在前者，后者是 UTC，
  取错会整体偏一个时区。``creation_source`` 就是给这件事留下的可观测面。
- **ISO6709 的 GPS**（``com.apple.quicktime.location.ISO6709`` 或 ``location``）——
  MP4 的定位不在 EXIF 里，只在这两个标签里。
- 时区写法两种都要认：``+0800`` 补成 ``+08:00``，``Z`` 换成 ``+00:00``。
- 旋转角优先读 ``side_data_list`` 的 ``rotation``，退回流标签的 ``rotate``。

与 1.0 的唯一区别是失败形态：1.0 抛 ``RuntimeError``/``ValueError``，本适配器抛
``ValueError``（端口契约里写了），由 ``ImportAssets`` 接住写进 metadata_json。

子进程与容器标签解析在 ``infrastructure/media/ffprobe.py``：音频适配器走的是同一条
命令、同一批标签，两处各写一遍迟早会分叉。本文件只留「视频流特有的那几位」
（帧率 / 宽高 / 旋转角）。
"""

from __future__ import annotations

from pathlib import Path

from sentrix.domain.media.models import VideoMetadata
from sentrix.infrastructure.media.ffprobe import (
    DEFAULT_PROBE_TIMEOUT_S,
    capture_time,
    iso6709_location,
    run_ffprobe,
)


def _rate(value: object) -> float:
    """``avg_frame_rate`` / ``r_frame_rate`` 是 ``"70400/2347"`` 这样的分数串。"""
    try:
        numerator, denominator = str(value or "0/1").split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _seconds(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


class FfprobeVideoMetadataReader:
    """domain ``VideoMetadataReader`` 的 ffprobe 实现。"""

    def __init__(self, *, timeout_s: int = DEFAULT_PROBE_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def read(self, path: Path) -> VideoMetadata:
        payload = run_ffprobe(Path(path), timeout_s=self._timeout_s)
        format_info = payload.get("format") or {}
        format_tags = format_info.get("tags") or {}
        video = next(
            (item for item in payload.get("streams") or [] if item.get("codec_type") == "video"),
            None,
        )
        if not video:
            raise ValueError("ffprobe found no video stream")
        stream_tags = video.get("tags") or {}
        captured_at, creation_source = capture_time(format_tags, stream_tags)
        # 流标签覆盖格式标签：流上的定位比容器上的更具体（1.0 同一个写法）。
        latitude, longitude = iso6709_location({**stream_tags, **format_tags})
        rotation = 0
        for item in video.get("side_data_list") or []:
            if "rotation" in item:
                rotation = int(float(item["rotation"]))
                break
        if not rotation:
            try:
                rotation = int(float(stream_tags.get("rotate") or 0))
            except ValueError:
                rotation = 0
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        # 原片总帧数：1.0 唯一的读侧是 PhotoBench 的关键帧分析，它从
        # ``raw.streams[].nb_frames`` 取（``benchmark_orchestrator.py:5723-5726``），
        # 口径是 ``int(frames or 0)`` 且类型不对就吞掉。**照抄这个容错**：很多容器
        # （尤其流式 mp4）根本不给 ``nb_frames``，缺了就是 0，不是错误、不该 raise。
        frames = video.get("nb_frames")
        try:
            frame_count = int(frames or 0)
        except (TypeError, ValueError):
            frame_count = 0
        duration = float(format_info.get("duration") or video.get("duration") or 0)
        device = " ".join(
            filter(
                None,
                [
                    format_tags.get("com.apple.quicktime.make"),
                    format_tags.get("com.apple.quicktime.model"),
                ],
            )
        ).strip()
        audio = next((s for s in payload.get("streams") or [] if s.get("codec_type") == "audio"), None)
        origin = _seconds(format_info.get("start_time")) or 0.0
        audio_start = _seconds(audio.get("start_time")) if audio else None
        return VideoMetadata(
            captured_at=captured_at,
            latitude=latitude,
            longitude=longitude,
            captured_location=f"{latitude:.6f},{longitude:.6f}" if latitude is not None else None,
            duration_sec=duration,
            fps=fps,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            codec=str(video.get("codec_name") or ""),
            rotation=rotation,
            device=device,
            creation_source=creation_source,
            frame_count=frame_count,
            audio_probed=True,
            audio_stream_index=int(audio["index"]) if audio else None,
            audio_source_start_sec=audio_start - origin if audio_start is not None else None,
            audio_duration_sec=_seconds(audio.get("duration")) if audio else None,
        )
