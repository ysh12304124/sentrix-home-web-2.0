"""SVD/Katna 采样：容器没给时长、帧率还写错时，仍按解码时间轴工作。"""

from __future__ import annotations

import numpy as np
import pytest

from sentrix.infrastructure.media.svd_katna_scene_planner import _sample_video


class FakeCapture:
    """10 帧 · 30fps · 容器却说 1000fps · 没有时长。"""

    def __init__(self, *_args, **_kwargs) -> None:
        self.index = -1
        self.next_index = 0

    def isOpened(self) -> bool:
        return True

    def get(self, prop):  # noqa: ANN001, ANN201
        import cv2

        if prop == cv2.CAP_PROP_FPS:
            return 1000.0
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return 320.0
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return 240.0
        if prop == cv2.CAP_PROP_POS_MSEC:
            return max(0, self.index) * 33.0
        return 0.0

    def read(self):  # noqa: ANN201
        if self.next_index >= 10:
            return False, None
        self.index = self.next_index
        self.next_index += 1
        return True, np.zeros((240, 320, 3), dtype=np.uint8)

    def release(self) -> None:
        return None


def test_missing_duration_falls_back_to_decoded_timestamps(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sentrix.infrastructure.media.svd_katna_scene_planner.cv2.VideoCapture",
        FakeCapture,
    )

    frames, sampling, _ = _sample_video(tmp_path / "clip.webm", 0.0)

    assert frames
    assert sampling["duration_source"] == "decoded_frames"
    assert sampling["decoded_frame_count"] == 10
    assert sampling["duration_sec"] == pytest.approx(0.297)
    assert frames[-1][0] == pytest.approx(0.297)
    assert len(sampling["source_resolution"]) == 2
