"""The sidecar is derived from annotations and checked against immutable media."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "gt_context_builder", Path(__file__).resolve().parents[3] / "scripts/build_gt_capture_context.py"
)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@pytest.fixture
def album(tmp_path):
    root = tmp_path / "album"
    for directory in ("photos", "videos", "metadata"):
        (root / directory).mkdir(parents=True)
    (root / "photos/a.jpg").write_bytes(b"photo")
    (root / "videos/001.mp4").write_bytes(b"video")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "album_id": "album-x",
                "location_annotation_version": "review-v12",
                "photos": ["photos/a.jpg"],
                "videos": ["videos/001.mp4"],
            }
        )
    )
    (root / "metadata/image_metadata.jsonl").write_text(
        json.dumps(
            {
                "filename": "a.jpg",
                "image_id": "album3/a.jpg",
                "readable_location": "河北省赵县",
                "capture_time_evidence": {
                    "captured_at_local": "2018-01-01T12:00:00+08:00",
                    "sha256": BUILDER.file_sha(root / "photos/a.jpg"),
                },
            }
        )
    )
    source = {
        "image_id": "source/a.jpg",
        "capture_time": "2018-01-01T12:00:00",
        "location_evidence": {
            "current_online_lookups": [
                {
                    "http_status": 200,
                    "candidates": [
                        {
                            "properties": {
                                "country": "中国",
                                "state": "河北省",
                                "city": "石家庄市",
                                "name": "附近门店",
                            }
                        }
                    ],
                }
            ]
        },
    }
    (root / "metadata/video_source_images.json").write_text(
        json.dumps(
            {
                "videos": [{"video_id": "001", "images": [source]}],
            }
        )
    )
    return root


def test_builder_checks_media_and_preserves_conservative_video_scope(album):
    result = BUILDER.build(album, album)
    assert len(result["entries"]) == 2
    photo, video = result["entries"]
    assert photo["media_id"] == "album3/a.jpg" and "image:photos/a.jpg" in photo["aliases"]
    assert photo["metadata"]["readable_location"] == "河北省赵县"
    assert video["metadata"]["readable_location"] == "中国 / 河北省 / 石家庄市"
    assert "附近门店" not in str(video["metadata"])
    assert "不是轨迹" in video["metadata"]["location_note"]
    assert len(result["sources"]) == 3


def test_builder_rejects_media_mismatch_and_stale_annotation(album, tmp_path):
    other = tmp_path / "other"
    (other / "photos").mkdir(parents=True)
    (other / "photos/a.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Media content differs"):
        BUILDER.build(album, other)
    (album / "photos/a.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Annotation does not match"):
        BUILDER.build(album)


def test_source_admin_boundary_disagreement_stops_at_shared_parent():
    properties = [{"country": "中国", "state": state, "city": "相邻市"} for state in ("甲省", "乙省")]
    image = {
        "location_evidence": {
            "current_online_lookups": [
                {"http_status": 200, "candidates": [{"properties": p} for p in properties]}
            ]
        }
    }
    assert BUILDER.source_location(image) == "中国"
