"""关键帧抽取：ffmpeg 在指定秒数上取一帧写成 JPEG。

**为什么落在 infrastructure。** 它起 ``ffmpeg`` 子进程，``domain-purity`` 与
``application-purity`` 两条契约都禁止 domain/application 碰子进程。端口在
``domain/media/ports.py``（``VideoKeyframeExtractor``），切窗规则在
``domain/media/scenes.py``。

**移植的是 1.0 的哪一半。** 只搬「每个场景落一张代表帧文件」这一步
（``backend/video/processor.py`` 的 ``shutil.copy2`` 那几行）；**抽帧算法不搬**——
1.0 的 YOLO/Pose 预筛 + Katna 质量门 + WorldMM 聚类是一整套重模型链路，用户
2026-10-04 裁决①「先做简化闭环」，换成「时间窗中点取一帧」。所以这个文件里的
编码参数 1.0 没有对应物，不是照抄。

产物路径与 ``FfmpegVideoTranscoder`` 同一个根（``media_dir/derived/video``）：换数据
目录就整体换，不必两处各配一次。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ``-q:v 2`` 是 JPEG 的质量档（2 = 接近最高，1.0 没有对应物）。关键帧要喂给 VLM 与
# 嵌入模型，画质是这两个下游的唯一输入，所以取高档；体积代价由 MAX_SCENES 封顶兜住。
FRAME_QUALITY = 2

# 抽一帧是「seek + 解一帧」，与转码整条视频不是一个量级。给 5 分钟是给大文件
# （数 GB 的 mov、网络盘）留余量，不是预期耗时。
KEYFRAME_TIMEOUT_S = 300


def _seek_seconds(timestamp_sec: float) -> str:
    """把取帧时间格式化成三位小数，**绝不因四舍五入越过原时间**。

    场景时间戳可能是最后一帧的精确 PTS（如 23.063761）。直接 ``f"{:.3f}"`` 会变成
    23.064，ffmpeg 输入侧 seek 到最后一帧之后，退出码仍是 0 但一个字节都不写。
    """
    seek = max(0.0, float(timestamp_sec))
    if float(f"{seek:.3f}") > seek:
        seek = max(0.0, seek - 0.001)
    return f"{seek:.3f}"


class FfmpegVideoKeyframeExtractor:
    """domain ``VideoKeyframeExtractor`` 的 ffmpeg 实现。"""

    def __init__(self, media_dir: Path) -> None:
        self._root = Path(media_dir) / "derived" / "video"

    def extract(self, source: Path, asset_id: str, scene_index: int, timestamp_sec: float) -> Path:
        """把 ``source`` 第 ``timestamp_sec`` 秒那一帧写成图片，返回产物路径。

        ``-ss`` 放在 ``-i`` **之前**：这是输入侧 seek，ffmpeg 会从最近的关键帧解到目标
        位置，比全程解码快几个数量级，而结果仍是准确的（不是「最近的关键帧」）。
        ``-frames:v 1`` 保证只写一帧就退出。
        """
        target = self._root / asset_id / "scenes" / f"{scene_index:03d}" / "kf_0001.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        # 输入侧 seek 在最后一帧附近可能“退出 0 但不写文件”。先按安全时间取一次；
        # 只有这种边界失败才向前退 50ms / 500ms 重试，真正的 ffmpeg 报错立即抛出。
        candidates = list(
            dict.fromkeys(
                max(0.0, float(timestamp_sec) - offset) for offset in (0.0, 0.05, 0.5)
            )
        )
        last_stderr = ""
        for seek in candidates:
            target.unlink(missing_ok=True)
            process = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    _seek_seconds(seek),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    # 手机视频常见非方像素（例如 720x480 + SAR 32:27，显示比例 16:9）。
                    # 不做这一步，抽出的 JPEG 会按方像素显示，人像被横向压扁；海报与
                    # 资料库预览正是直接拿这张 JPEG。按 SAR 展开到显示宽度再标记方像素。
                    "-vf",
                    "scale=iw*sar:ih,setsar=1",
                    "-q:v",
                    str(FRAME_QUALITY),
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=KEYFRAME_TIMEOUT_S,
            )
            if process.returncode:
                raise RuntimeError(
                    process.stderr.strip()[-1000:] or "ffmpeg keyframe extraction failed"
                )
            if target.is_file():
                return target
            last_stderr = process.stderr.strip()[-1000:]
        # 不只看返回码：ffmpeg 有些失败形态（写不出文件）返回 0。两个都判，
        # 与 ``transcode_to_h264`` 同一条口径。
        raise RuntimeError(last_stderr or "ffmpeg keyframe extraction failed")
