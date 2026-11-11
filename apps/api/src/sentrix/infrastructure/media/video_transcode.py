"""浏览器预览转码。移植自 1.0 ``backend/video/processor.py:58-69`` 的 ``_browser_preview``。

**为什么落在 infrastructure**：它起 ``ffmpeg`` 子进程，两条 purity 契约都禁止
domain/application 碰它。端口声明在 ``domain/media/ports.py``（``VideoTranscoder``）。

1.0 的规则一条不改：

- **H.264 / avc1 直出，不转**（``processor.py:59-60``）——转一遍只会掉质量、多花时间，
  而浏览器本来就播得了。判据是 ``codec_name``，不是文件后缀。
- 其余一律转成 **libx264 / crf 23 / veryfast / yuv420p / aac / +faststart**。
  ``+faststart`` 不是可选项：moov 盒子不前置的话，浏览器必须先下完整段才能起播
  （1.0 的注释与产物 ``preview.mp4`` 都是为这件事存在的）。
- 音频用 ``-map 0:a:0?`` 的可选映射：**没有音轨的视频不该让整次转码失败**。

与 1.0 的一处差别：产物路径由适配器按 ``media_dir/derived/video/{asset_id}/preview.mp4``
自己拼（1.0 是调用方拼好传进来的），调用方不必知道派生目录长什么样。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# 1.0 ``processor.py:59`` 的集合，逐字照抄。``avc1`` 是 MP4 里的 codec_tag 写法，
# 不同的封装/工具会给出两种名字之一。
H264_CODECS = frozenset({"h264", "avc1"})
# 浏览器能直接播的容器。H.264 塞进 QuickTime `.mov` 时 Chrome/Android 仍可能拒播，
# 不能只看 codec_name。
BROWSER_VIDEO_SUFFIXES = frozenset({".mp4", ".m4v"})

# 1.0 ``processor.py:62-67`` 的参数序列。时长上不封顶（一个几小时的家庭录像也在用），
# 所以超时给到 1 小时——与读元数据那 60 秒不是一个量级的事。
TRANSCODE_TIMEOUT_S = 3600


def is_browser_playable(codec: str) -> bool:
    """浏览器能不能直接播这个编码。1.0 ``processor.py:59`` 的判据。"""
    return str(codec or "").lower() in H264_CODECS


def transcode_to_h264(source: Path, target: Path, *, timeout_s: int = TRANSCODE_TIMEOUT_S) -> Path:
    """把 ``source`` 转成 H.264 mp4 写到 ``target``，返回 ``target``。

    **只转，不判**——要不要转由 :func:`is_browser_playable` 决定，调用方（适配器）判。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if process.returncode or not target.is_file():
        raise RuntimeError(process.stderr.strip()[-1000:] or "ffmpeg preview transcode failed")
    return target


class FfmpegVideoTranscoder:
    """domain ``VideoTranscoder`` 的 ffmpeg 实现。派生预览统一放在 ``media_dir/derived/video``。"""

    def __init__(self, media_dir: Path) -> None:
        self._root = Path(media_dir) / "derived" / "video"

    def browser_preview(self, asset_id: str, source: Path, codec: str) -> Path | None:
        source = Path(source)
        if is_browser_playable(codec) and source.suffix.lower() in BROWSER_VIDEO_SUFFIXES:
            return None
        return transcode_to_h264(source, self._root / asset_id / "preview.mp4")
