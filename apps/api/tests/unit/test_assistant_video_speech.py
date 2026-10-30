from types import SimpleNamespace

from sentrix.application.assistant.tools import AssistantToolbox
from sentrix.domain.media.models import MediaType


def test_video_preview_exposes_scene_speech_and_keyframe_preview_is_time_aligned():
    scene = SimpleNamespace(
        source_start_sec=0.0,
        source_end_sec=39.0,
        source_metadata={
            "speech_transcript": "鸡肉和香肠",
            "speech_segments": [
                {"start_sec": 0.0, "end_sec": 10.0, "text": "加入鸡肉和香肠", "speaker": "speaker_0"},
                {"start_sec": 20.0, "end_sec": 25.0, "text": "准备番茄和青椒", "speaker": "speaker_1"},
            ],
        },
    )
    toolbox = object.__new__(AssistantToolbox)
    toolbox._events = SimpleNamespace(list_video_scenes=lambda _video_id: (scene,))

    video = SimpleNamespace(
        id="video-1", media_type=MediaType.VIDEO, parent_asset_id=None, source_timestamp_sec=None
    )
    keyframe = SimpleNamespace(
        id="frame-1", media_type=MediaType.IMAGE, parent_asset_id="video-1", source_timestamp_sec=22.0
    )

    video_speech = toolbox._speech_preview(video)["speech_evidence"]
    frame_speech = toolbox._speech_preview(keyframe)["speech_evidence"]

    assert [segment["text"] for segment in video_speech] == ["加入鸡肉和香肠", "准备番茄和青椒"]
    assert [segment["text"] for segment in frame_speech] == ["准备番茄和青椒"]
