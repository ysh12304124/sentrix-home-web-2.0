"""关键帧抽取：命令行形状、产物位置、失败判据（S8b T511）。

用假 subprocess：要钉的是「seek 用输入侧」「只取一帧」「产物落在哪」「失败了炸不炸」，
这些与机器上有没有 ffmpeg 无关（体例同 ``test_video_transcode.py``）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sentrix.infrastructure.media.video_keyframes import FfmpegVideoKeyframeExtractor

PATCH_TARGET = "sentrix.infrastructure.media.video_keyframes.subprocess.run"


def _extractor(tmp_path: Path) -> FfmpegVideoKeyframeExtractor:
    return FfmpegVideoKeyframeExtractor(tmp_path)


def test_the_target_is_the_scene_slot_under_the_derived_video_root(tmp_path: Path):
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"jpg")
        return Mock(returncode=0, stdout="", stderr="")

    with patch(PATCH_TARGET, side_effect=fake_run):
        result = _extractor(tmp_path).extract(source, "asset_1", 3, 42.5)

    assert result == tmp_path / "derived" / "video" / "asset_1" / "scenes" / "003" / "kf_0001.jpg"
    assert result.is_file()


def test_the_command_seeks_before_the_input_and_takes_exactly_one_frame(tmp_path: Path):
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"jpg")
        return Mock(returncode=0, stdout="", stderr="")

    with patch(PATCH_TARGET, side_effect=fake_run) as run:
        _extractor(tmp_path).extract(source, "asset_1", 0, 30.0)

    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    # ``-ss`` 必须在 ``-i`` 之前：输入侧 seek。放到后面 ffmpeg 会整段解码到该秒，
    # 一个几小时的录像就是几十分钟的等待——命令仍然正确，只是慢得不可用。
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "30.000"
    assert command[command.index("-frames:v") + 1] == "1"


def test_a_tail_timestamp_never_rounds_past_the_last_frame(tmp_path: Path):
    """最后一帧的 PTS 是 23.063761；直接格式化会把 -ss 舍入到 23.064 并丢帧。"""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    last_frame = 23.063761430779746

    def fake_run(command, **kwargs):
        seek = float(command[command.index("-ss") + 1])
        if seek <= last_frame:
            Path(command[-1]).write_bytes(b"jpg")
            return Mock(returncode=0, stdout="", stderr="")
        # ffmpeg 越过最后一帧时就是这种形态：退出 0，但不写文件。
        return Mock(returncode=0, stdout="", stderr="")

    with patch(PATCH_TARGET, side_effect=fake_run):
        result = _extractor(tmp_path).extract(source, "asset_1", 2, last_frame)

    assert result.is_file()


def test_failure_raises_with_the_ffmpeg_stderr(tmp_path: Path):
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    with (
        patch(PATCH_TARGET, return_value=Mock(returncode=1, stdout="", stderr="boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _extractor(tmp_path).extract(source, "asset_1", 0, 0.0)


def test_a_zero_exit_without_the_output_file_is_still_a_failure(tmp_path: Path):
    """ffmpeg 退出 0 但产物不在，同样是失败——只看退出码会交出一个不存在的路径。"""
    source = tmp_path / "in.mov"
    source.write_bytes(b"x")

    with (
        patch(PATCH_TARGET, return_value=Mock(returncode=0, stdout="", stderr="")),
        pytest.raises(RuntimeError),
    ):
        _extractor(tmp_path).extract(source, "asset_1", 0, 0.0)
