from types import SimpleNamespace

from sentrix.infrastructure.media.svd_scene_planner import SVDLowRankScenePlanner


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
        "sentrix.infrastructure.media.svd_scene_planner.SVDLowRankKeyframeExtractor",
        Extractor,
    )
    video_path = tmp_path / "video.mp4"
    planner = SVDLowRankScenePlanner(tmp_path / "derived")

    scenes = planner(video_path, "video-1", 10.0)

    assert seen["call"] == (video_path, "video-1", tmp_path / "derived" / "video-1")
    assert len(scenes) == 1
    assert scenes[0].keyframe_timestamp_sec == 6.7
    assert (scenes[0].start_sec, scenes[0].end_sec) == (2.0, 8.0)
