from types import SimpleNamespace

from sentrix.infrastructure.media.svd_scene_planner import SVDLowRankScenePlanner
from sentrix.infrastructure.media.svd_keyframe import SVDLowRankKeyframeExtractor


def test_svd_keyframes_become_selected_scene_timestamps(tmp_path, monkeypatch):
    seen = {}

    class Extractor:
        def extract(self, video_path, video_id, output_path):
            seen["call"] = (video_path, video_id, output_path)
            return SimpleNamespace(
                scenes=[
                    SimpleNamespace(
                        start_sec=2.0,
                        end_sec=8.0,
                        keyframes=[SimpleNamespace(timestamp_sec=6.7)],
                    )
                ]
            )

    monkeypatch.setattr(
        "sentrix.infrastructure.media.svd_scene_planner.SVDLowRankKeyframeExtractor", Extractor
    )
    video_path = tmp_path / "video.mp4"
    output_root = tmp_path / "derived"
    planner = SVDLowRankScenePlanner(output_root)

    scenes = planner(video_path, "video-1", 10.0)

    assert seen["call"] == (video_path, "video-1", output_root / "video-1")
    assert len(scenes) == 1
    assert scenes[0].keyframe_timestamp_sec == 6.7
    assert (scenes[0].start_sec, scenes[0].end_sec) == (2.0, 8.0)


def test_container_defaults_to_svd_lowrank(tmp_path, monkeypatch):
    from sentrix.bootstrap.container import Container
    from sentrix.config import Settings

    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SENTRIX_VIDEO_KEYFRAME_ALGORITHM", raising=False)
    container = Container.build(Settings())

    assert isinstance(container.video_scene_planner, SVDLowRankScenePlanner)


def test_temporal_nms_keeps_strongest_frame_within_minimum_gap():
    extractor = SVDLowRankKeyframeExtractor(
        {"keyframe_min_gap_seconds": 8.0, "coverage_interval_seconds": 0.0}
    )

    selected = extractor.temporal_nms(
        [0, 1, 2, 3],
        scores=__import__("numpy").array([0.2, 0.8, 0.5, 0.9]),
        timestamps=__import__("numpy").array([0.0, 4.0, 9.0, 18.0]),
    )

    assert selected == [1, 3]


def test_temporal_nms_adds_sparse_coverage_for_quiet_spans():
    import numpy as np

    extractor = SVDLowRankKeyframeExtractor(
        {"keyframe_min_gap_seconds": 8.0, "coverage_interval_seconds": 16.0}
    )
    timestamps = np.arange(0.0, 65.0, 0.5)

    selected = extractor.temporal_nms([], np.zeros(len(timestamps)), timestamps)

    assert len(selected) >= 4
    assert np.max(np.diff(timestamps[selected])) <= 16.0
