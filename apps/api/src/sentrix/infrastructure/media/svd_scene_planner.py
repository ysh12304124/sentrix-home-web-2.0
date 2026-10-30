"""Adapter from the original CPU SVD extractor to Web2 scene windows."""

from __future__ import annotations

from pathlib import Path

from sentrix.domain.media.scenes import SceneWindow
from sentrix.infrastructure.media.svd_keyframe import SVDLowRankKeyframeExtractor


class SVDLowRankScenePlanner:
    """Run the original SVD low-rank algorithm without an external checkout."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        self._extractor = SVDLowRankKeyframeExtractor()

    def __call__(self, video_path: Path, video_id: str, duration_sec: float) -> tuple[SceneWindow, ...]:
        output = self._output_root / video_id
        result = self._extractor.extract(video_path, video_id, output)
        windows: list[SceneWindow] = []
        for scene in result.scenes:
            for keyframe in scene.keyframes:
                timestamp = min(max(0.0, float(keyframe.timestamp_sec)), float(duration_sec))
                start = min(max(0.0, float(scene.start_sec)), timestamp)
                end = min(float(duration_sec), max(float(scene.end_sec), timestamp))
                windows.append(
                    SceneWindow(
                        index=len(windows),
                        start_sec=start,
                        end_sec=end,
                        selected_timestamp_sec=timestamp,
                    )
                )
        if not windows:
            raise RuntimeError(f"Original SVD extractor selected no keyframes for {video_path}")
        return tuple(windows)
