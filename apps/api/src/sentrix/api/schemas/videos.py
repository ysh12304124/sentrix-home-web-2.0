"""视频端点的响应模型（S8a T504）。

形状取自 1.0 ``backend/app.py:1448-1481`` 四条路由的返回，**但去掉了 2.0 拿不到的键**：
1.0 的 ``video`` 是整行 asset（含它那一大包 metadata_json），这里只列视频真正有的那几位
＋ 探测结果；``event`` 那一侧 1.0 还挂着 observations / facts / entities / event_revisions，
2.0 没有通用的事件详情读侧（``event_revisions`` 表都不存在），一个不留恒空的占位键。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VideoMetadataPayload(BaseModel):
    """ffprobe 探测结果的投影。字段与 ``domain.media.models.VideoMetadata`` 一一对应。"""

    captured_at: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    captured_location: str | None = None
    duration_sec: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""
    rotation: int = 0
    device: str = ""
    creation_source: str = ""
    # 原片总帧数（S8a 补）：1.0 藏在整包 ``raw`` 里，PhotoBench 的关键帧分析读它。
    # 缺了就是 0（很多容器不给 ``nb_frames``）。
    frame_count: int = 0


class VideoPayload(BaseModel):
    """1.0 ``GET /api/videos/{id}`` 的 ``video`` 字段。"""

    id: str
    scope_id: str
    file_name: str
    media_type: str
    mime_type: str | None
    size_bytes: int
    status: str
    captured_at: str | None
    captured_location: str | None
    gps: dict[str, float] | None
    # 没探测过（老库、或导入时没接探测链）时为 None。
    metadata: VideoMetadataPayload | None = None
    browser_preview_path: str = ""
    browser_preview_error: str = ""
    probe_error: str = ""
    # 整个视频处理阶段的秒数（探测 + 图片流水线 + 场景抽取），1.0 写在
    # ``metadata_json.video_processing_seconds``（``processor.py:205`` / ``:420``），
    # PhotoBench 的关键帧分析读它算 ``realtime_factor``（``benchmark_orchestrator.py:5726``）。
    # 没处理过（老库 / 导入时没接视频链）时为 None。
    processing_seconds: float | None = None


class SpeechSegmentPayload(BaseModel):
    """带视频时间轴的 ASR 片段。"""

    start_sec: float
    end_sec: float
    text: str


class VideoScenePayload(BaseModel):
    """一个视频场景事件。血缘那几位（``source_*``）来自迁移 0010。"""

    id: str
    scope_id: str
    title: str
    event_type: str
    time_start: str | None
    time_end: str | None
    place: str
    activity: str
    summary: str
    confidence: float
    status: str
    source_type: str = ""
    source_asset_id: str = ""
    source_scene_index: int | None = None
    source_start_sec: float | None = None
    source_end_sec: float | None = None
    speech_segments: list[SpeechSegmentPayload] = Field(default_factory=list)
    keyframe_timestamps_sec: list[float] = Field(default_factory=list)


class KeyframeAssetPayload(BaseModel):
    """一张派生关键帧。**它本来就是普通 image 资产**（1.0 ``processor.py:188``），
    这里只带出「指回原片」的那几列。"""

    id: str
    file_name: str
    media_type: str
    status: str
    captured_at: str | None
    captured_location: str | None
    parent_asset_id: str | None
    derived_kind: str | None
    source_timestamp_sec: float | None
    source_frame_index: int | None
    source_scene_index: int | None


class SourceVideoPayload(BaseModel):
    """场景事件指回的源视频。1.0 ``db.py:2364`` 的原样一小份，外加时长。"""

    id: str
    file_name: str
    media_type: str
    mime_type: str | None
    status: str
    captured_at: str | None
    captured_location: str | None
    size_bytes: int
    duration_sec: float = 0.0


class VideoDetailResponse(BaseModel):
    video: VideoPayload
    scenes: list[VideoScenePayload]


class VideoScenesResponse(BaseModel):
    video_asset_id: str
    scenes: list[VideoScenePayload]


class VideoSceneDetailResponse(BaseModel):
    """``GET /video-scenes/{id}``。

    **``source_video`` / ``keyframe_assets`` 放在 ``event`` 的旁边，而不是像 1.0 那样
    塞进 ``event`` 里面。** 1.0 那个嵌套是 ``get_event`` 顺手给行加键的产物；摊平之后
    列表端点（``/videos/{id}/scenes``）用的同一个 ``VideoScenePayload`` 不必背上两个
    它永远为空的字段。
    """

    event: VideoScenePayload
    keyframe_assets: list[KeyframeAssetPayload]
    source_video: SourceVideoPayload | None


class VideoReprocessResponse(BaseModel):
    accepted: bool
    asset_id: str
    status: str
