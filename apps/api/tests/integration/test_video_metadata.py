"""ffprobe 适配器的抽取口径。移植自 1.0 ``backend/tests/test_video_metadata.py``。

**用假 subprocess，不起真 ffprobe**：要钉的是「哪个标签优先、怎么写进字段」这些
纯解析口径，它们与机器上装没装 ffmpeg 无关。真子进程的调用形态（``ffprobe -v error
-print_format json -show_format -show_streams``）由下面第一条断言钉住命令行。

两例 payload 逐字取自 1.0 的测试（一台 iPhone 导出的 .mov，与一段带 location 标签的
mp4），所以 1.0 认得的两个拍摄时间/GPS 来源在 2.0 里认得一样多。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sentrix.infrastructure.media.video_metadata import FfprobeVideoMetadataReader

QUICKTIME_PAYLOAD = {
    "format": {
        "duration": "11.735",
        "tags": {
            "creation_time": "2026-08-02T06:55:56Z",
            "com.apple.quicktime.creationdate": "2026-08-02T14:55:56+0800",
            "com.apple.quicktime.location.ISO6709": "+22.4846+114.5438+005.469/",
            "com.apple.quicktime.make": "Apple",
            "com.apple.quicktime.model": "iPhone 14 Plus",
        },
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "70400/2347",
            "tags": {},
        }
    ],
}

TAGGED_MP4_PAYLOAD = {
    "format": {
        "duration": "6",
        "tags": {
            "creation_time": "2017-09-27T03:36:15.000000Z",
            "location": "+36.54452778+115.27778611+41.7909/",
        },
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 1280,
            "avg_frame_rate": "24/1",
            "tags": {},
        }
    ],
}


def completed(payload: dict, *, returncode: int = 0, stderr: str = "") -> Mock:
    return Mock(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)


def probe(payload: dict, *, path: str = "movie.mov"):
    with patch(
        "sentrix.infrastructure.media.ffprobe.subprocess.run",
        return_value=completed(payload),
    ):
        return FfprobeVideoMetadataReader().read(Path(path))


def test_calls_ffprobe_with_the_same_flags_as_1_0():
    with patch(
        "sentrix.infrastructure.media.ffprobe.subprocess.run",
        return_value=completed(QUICKTIME_PAYLOAD),
    ) as run:
        FfprobeVideoMetadataReader().read(Path("movie.mov"))

    command = run.call_args.args[0]
    assert command[:4] == ["ffprobe", "-v", "error", "-print_format"]
    assert "json" in command
    assert command[command.index("-show_format") + 1] == "-show_streams"
    assert command[-1] == str(Path("movie.mov").resolve())


def test_prefers_quicktime_creationdate_and_parses_iso6709():
    """QuickTime 的本地时区拍摄时间优先于 UTC 的 ``creation_time``——取错会整体偏一个时区。"""
    result = probe(QUICKTIME_PAYLOAD)

    assert result.captured_at == "2026-08-02T14:55:56+08:00"
    assert result.creation_source == "com.apple.quicktime.creationdate"
    assert result.latitude == pytest.approx(22.4846)
    assert result.longitude == pytest.approx(114.5438)
    assert result.captured_location == "22.484600,114.543800"
    assert result.codec == "hevc"
    assert result.device == "Apple iPhone 14 Plus"
    assert result.duration_sec == pytest.approx(11.735)
    assert (result.width, result.height) == (1920, 1080)
    assert result.fps == pytest.approx(70400 / 2347)


def test_parses_the_location_tag_used_by_tagged_mp4():
    result = probe(TAGGED_MP4_PAYLOAD, path="kling.mp4")

    assert result.captured_at == "2017-09-27T03:36:15.000000+00:00"
    assert result.creation_source == "format.creation_time"
    assert result.latitude == pytest.approx(36.54452778)
    assert result.longitude == pytest.approx(115.27778611)


def test_creation_source_falls_back_to_the_stream_tag():
    payload = {
        "format": {"duration": "1", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "vp9",
                "width": 640,
                "height": 480,
                "avg_frame_rate": "0/0",
                "tags": {"creation_time": "2020-01-02T03:04:05Z"},
            }
        ],
    }

    result = probe(payload)

    assert result.creation_source == "stream.creation_time"
    assert result.captured_at == "2020-01-02T03:04:05+00:00"
    # avg_frame_rate 是 0/0（未知），退回 r_frame_rate；两个都缺就是 0.0，不炸。
    assert result.fps == 0.0


def test_rotation_comes_from_side_data_then_the_rotate_tag():
    side_data = {
        "format": {"duration": "1", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1,
                "height": 1,
                "side_data_list": [{"rotation": -90}],
                "tags": {"rotate": "270"},
            }
        ],
    }
    rotate_tag = {
        "format": {"duration": "1", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1,
                "height": 1,
                "tags": {"rotate": "270"},
            }
        ],
    }

    assert probe(side_data).rotation == -90
    assert probe(rotate_tag).rotation == 270


def test_ffprobe_failure_raises_value_error():
    """端口契约：读不出来抛 ``ValueError``，由用例接住记进 metadata_json。"""
    with (
        patch(
            "sentrix.infrastructure.media.ffprobe.subprocess.run",
            return_value=completed({}, returncode=1, stderr="No such file or directory"),
        ),
        pytest.raises(ValueError, match="ffprobe failed"),
    ):
        FfprobeVideoMetadataReader().read(Path("gone.mp4"))


def test_ffprobe_retries_a_transient_failure_before_returning_metadata():
    with patch(
        "sentrix.infrastructure.media.ffprobe.subprocess.run",
        side_effect=[
            completed({}, returncode=1, stderr="resource temporarily unavailable"),
            completed(TAGGED_MP4_PAYLOAD),
        ],
    ) as run:
        result = FfprobeVideoMetadataReader().read(Path("retry.mp4"))

    assert run.call_count == 2
    assert result.duration_sec == pytest.approx(6)


def test_no_video_stream_raises_value_error():
    payload = {
        "format": {"duration": "3", "tags": {}},
        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
    }

    with pytest.raises(ValueError, match="no video stream"):
        probe(payload)


def test_frame_count_comes_from_the_video_stream():
    """原片总帧数取自视频流的 ``nb_frames``——1.0 唯一的读侧是 PhotoBench 的关键帧分析。

    故意让 ``nb_frames`` 与别的字段不同源（时长 3、nb_frames 90），否则这条断言在
    「永远返回 0」的实现下也是绿的。
    """
    payload = {
        "format": {"duration": "3", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 640,
                "height": 480,
                "nb_frames": "90",
                "tags": {},
            }
        ],
    }

    assert probe(payload).frame_count == 90


def test_frame_count_is_zero_when_the_container_omits_it():
    """很多容器不给 ``nb_frames``：缺了就是 0，不报错（1.0 的容错口径）。"""
    assert probe(QUICKTIME_PAYLOAD).frame_count == 0


def test_a_non_numeric_nb_frames_degrades_to_zero_instead_of_raising():
    """``nb_frames`` 是 ``"N/A"`` 这类非数字时吞掉，回落 0——照抄 1.0 ``int(frames or 0)``。"""
    payload = {
        "format": {"duration": "1", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1,
                "height": 1,
                "nb_frames": "N/A",
                "tags": {},
            }
        ],
    }

    assert probe(payload).frame_count == 0


def test_half_a_coordinate_is_not_a_gps_point():
    """ISO6709 只解析出纬度时不算一个 GPS 点——半个坐标不该冒充一个完整位置。"""
    payload = {
        "format": {"duration": "1", "tags": {}},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1, "height": 1, "tags": {}}],
    }
    metadata = probe(payload)

    assert metadata.gps() is None
    assert metadata.captured_datetime() is None
