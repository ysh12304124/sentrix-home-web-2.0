"""场景切分规则：把一段视频切成固定时长的时间窗，每个窗取中点一帧当代表帧。

**为什么是纯 domain。** 「一个场景多长」「最多抽几帧」是产品行为，不是解码细节：
改一个数，抽帧数量与 VLM 调用数一起变，而且不会报错。放在 domain 里，它不依赖 ffmpeg、
不受两条 purity 契约约束，能被单测逐条钉住。

**这两条常量 1.0 没有对应物。** 1.0 的抽帧是 YOLO/Pose 预筛 + Katna 质量门 + WorldMM
聚类的产物，没有「固定时长窗」这个概念；用户 2026-10-04 裁决①「先做简化闭环」，
把整条算法换成时间窗。所以它们是 2.0 新定的值，**不是照抄 1.0 的数**——这一点
逐条登记在 ``docs/refactor/behaviour-baseline.md`` 的 S8b 段。

**算法日后可换而不动其他部分**（裁决①的原文）：换掉 ``plan_scenes`` 与抽帧适配器即可，
血缘列、场景事件、派生帧走图片流水线那三件都不动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 场景窗默认时长（秒）。十秒钟的视频一个场景，十分钟的视频十个场景。
SCENE_WINDOW_SEC = 60.0

# 总场景数封顶。**为什么封顶**：每个关键帧要走完整的图片流水线（VLM 观察 + wemm 嵌入
# + 人脸），**帧数就是 VLM 调用数**；不封顶的话一个几小时的录像会把 GPU 打满。
# 超过时按比例放大窗口，而不是无限抽帧。
MAX_SCENES = 40

# 派生帧的 ``derived_kind``。**只保留一个取值**（用户裁决⑧，``s8-deviations.md`` §3
# 第 1 条）：1.0 的 worldmm / hybrid_webp 双算法并存、两套 kind、两套输出格式不移植。
KEYFRAME_DERIVED_KIND = "video_keyframe"

# 场景事件的来源标记，迁移 0010 的 ``events.source_type`` 取值之一。常量放在这里，
# 写侧（仓储）、读侧（路由）拿到的是同一个字面量——两处各写一遍字符串，改一处就会
# 得到「写得进去、读不出来」（1.0 的 ``video_keyframe`` / ``video_keyframe_webp``
# 就是这么错的，``s8-deviations.md`` §2 第 1、3 条）。
VIDEO_SCENE_SOURCE_TYPE = "video_scene"


@dataclass(frozen=True, slots=True)
class SceneWindow:
    """一个场景在原片里的区间，以及代表帧取在哪一秒。

    ``keyframe_timestamp_sec`` 是**窗口中点**（用户裁决：每个场景取窗口中点那一秒）。
    用浮点而不是取整：12.5 秒的视频只有 6.25 这一个中点，取整成 6 会把「中点」这件事
    变成另一种规则，而且最后一窗的中点永远严格小于 ``duration``——不必再夹一次边界。
    """

    index: int
    start_sec: float
    end_sec: float
    selected_timestamp_sec: float | None = None
    selection_group: int | None = None

    @property
    def keyframe_timestamp_sec(self) -> float:
        if self.selected_timestamp_sec is not None:
            return float(self.selected_timestamp_sec)
        return (self.start_sec + self.end_sec) / 2


def plan_scenes(
    duration_sec: float,
    *,
    window_sec: float = SCENE_WINDOW_SEC,
    max_scenes: int = MAX_SCENES,
) -> tuple[SceneWindow, ...]:
    """按时长切窗。``duration_sec`` 未知（<=0）时返回空元组——**不猜、不兜底**。

    探测失败（没有 ffprobe 元数据）的视频会走到这一支：宁可一个场景也不建，也不拿
    导入时刻去编一个时长。失败是可见的（``ExtractVideoScenes`` 把原因放在返回值里），
    编出来的时长不可见。
    """
    duration = float(duration_sec)
    if duration <= 0 or window_sec <= 0 or max_scenes <= 0:
        return ()
    window = float(window_sec)
    count = math.ceil(duration / window)
    if count > max_scenes:
        # 超过了封顶就**按比例放大窗口**：场景数收敛到 max_scenes，每个场景仍然取中点，
        # 只是覆盖面变大。反过来「封顶在前 N 个窗口、剩下的丢掉」会让长视频的后半段
        # 一条语义都不进库，而且看不出来。
        window = duration / max_scenes
        count = max_scenes
    scenes: list[SceneWindow] = []
    for index in range(count):
        start = index * window
        end = min(start + window, duration)
        if end <= start:
            break
        scenes.append(SceneWindow(index=index, start_sec=start, end_sec=end))
    return tuple(scenes)
