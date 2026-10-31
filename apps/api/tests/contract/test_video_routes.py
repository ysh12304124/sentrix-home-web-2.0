"""视频四条路由的 HTTP 形状（S8a T504），与导入/交付两条路上的视频分支（T503/T505）。

走真容器 + 真 SQLite，只把 ffprobe/ffmpeg 两个适配器换成 conftest 里的确定性假件
（见 ``FakeVideoMetadataReader``：文件名带 ``hevc`` 才走转码那条路）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def import_video(client: TestClient, name: str = "clip.mov", payload: bytes = b"fake-mov-bytes"):
    return client.post(
        "/api/v1/media/import",
        files={"file": (name, payload, "video/quicktime")},
        data={"scope_id": "album1"},
    )


def test_importing_a_video_reports_that_it_was_probed(client: TestClient):
    """T505：视频不再静默空转——响应里必须看得出「真的探过」。

    ``video_probed`` 是这条断言的可观测面。把 ImportAssets 里那次 ``_probe_video``
    调用删掉，它立刻变 False。
    """
    body = import_video(client).json()

    assert body["media_type"] == "video"
    assert body["video_probed"] is True


def test_video_detail_returns_the_video_and_its_metadata(client: TestClient):
    """``GET /videos/{id}`` → ``{video, scenes}``（1.0 ``app.py:1448``）。

    ``metadata.duration_sec`` 走的是 ffprobe 那一份，不是文件大小之类的替身。
    """
    asset_id = import_video(client).json()["asset_id"]

    body = client.get(f"/api/v1/videos/{asset_id}").json()

    assert body["video"]["id"] == asset_id
    assert body["video"]["media_type"] == "video"
    assert body["video"]["metadata"]["duration_sec"] == pytest.approx(12.5)
    assert body["video"]["metadata"]["codec"] == "h264"
    # 原片总帧数：1.0 藏在 raw 里，PhotoBench 的关键帧分析读它（假件固定 300）。
    # 少了这条，「适配器读了 nb_frames 但路由没往外带」不会被发现。
    assert body["video"]["metadata"]["frame_count"] == 300
    # 整个视频阶段的秒数：导入时写、读回来（假 clock 不在此容器里，只断言「有值」）。
    assert body["video"]["processing_seconds"] is not None
    assert body["video"]["captured_location"] == "22.484600,114.543800"
    # S8b 起场景成型（T512）：12.5 秒的片子落在 60 秒那一个窗里，正好一个场景。
    # 这里不能只断言「非空」——场景必须带着血缘指回源片，否则「跳回原片」没落点。
    assert len(body["scenes"]) == 1
    scene = body["scenes"][0]
    assert scene["source_type"] == "video_scene"
    assert scene["source_asset_id"] == asset_id
    assert scene["source_scene_index"] == 0
    assert scene["source_start_sec"] == pytest.approx(0.0)
    assert scene["source_end_sec"] == pytest.approx(12.5)


def test_video_scenes_endpoint_returns_the_1_0_shape(client: TestClient):
    """``GET /videos/{id}/scenes`` → ``{video_asset_id, scenes}``（1.0 ``app.py:1461``）。

    键集是 1.0 的形状；S8b 起 ``scenes`` 里有东西——空数组与「场景真的建起来了」
    在响应上分得开，靠的就是这个列表的内容。
    """
    asset_id = import_video(client).json()["asset_id"]

    body = client.get(f"/api/v1/videos/{asset_id}/scenes").json()

    assert body["video_asset_id"] == asset_id
    assert len(body["scenes"]) == 1
    assert body["scenes"][0]["source_scene_index"] == 0


def test_video_detail_404s_for_non_videos_and_missing_assets(client: TestClient):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="JPEG")
    image_id = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", buffer.getvalue(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()["asset_id"]

    assert client.get(f"/api/v1/videos/{image_id}").status_code == 404
    assert client.get("/api/v1/videos/asset_nope").status_code == 404
    assert client.get("/api/v1/videos/asset_nope/scenes").status_code == 404
    assert client.get("/api/v1/video-scenes/evt_nope").status_code == 404


def test_a_playable_video_is_delivered_as_the_original(client: TestClient):
    """H.264 不转码：交付件就是原片（1.0 ``app.py:2461`` 的回落分支）。"""
    payload = b"fake-mov-bytes"
    asset_id = import_video(client, payload=payload).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/file")

    assert response.status_code == 200
    assert response.content == payload


def test_a_non_playable_video_is_delivered_as_the_browser_preview(client: TestClient):
    """非 H.264 → ``preview.mp4``；``?original=1`` 才是原片（T503 的分流）。"""
    payload = b"fake-hevc-bytes"
    asset_id = import_video(client, name="clip_hevc.mov", payload=payload).json()["asset_id"]

    preview = client.get(f"/api/v1/assets/{asset_id}/file")
    original = client.get(f"/api/v1/assets/{asset_id}/file", params={"original": "1"})

    assert preview.status_code == 200
    assert preview.content == b"fake-preview-mp4"
    assert preview.headers["content-type"] == "video/mp4"
    assert preview.headers["x-sentrix-source"] == "browser-preview"
    assert original.content == payload


def test_video_preview_route_no_longer_500s(client: TestClient):
    """``GET /assets/{id}/preview`` 对视频原先会 500——PillowPreviewEncoder 打不开视频。

    修法是走同一条视频分流（T503）。这里断言的是「不抛、且交出来的还是视频」。
    """
    asset_id = import_video(client).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/")


def test_reprocess_clears_and_rebuilds_the_derivatives(client: TestClient):
    """``POST /videos/{id}/reprocess`` → 202 + 清场 + **真重跑一次抽取链**（T514）。

    判据是**派生物的 id 变了**：重跑前先 ``cleanup_derivatives`` 清掉旧的，所以新帧
    是新行。如果只清了不重建（S8a 的行为），列表会是空的；如果没清就重跑（幂等跳过），
    列表里会是**同一个 id**——两种退化都被这一条挡住。
    """
    asset_id = import_video(client).json()["asset_id"]
    container = client.app.state.container
    before = container.video_repository.list_derived(asset_id)
    assert len(before) == 1

    response = client.post(f"/api/v1/videos/{asset_id}/reprocess")

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "asset_id": asset_id, "status": "processed"}
    after = container.video_repository.list_derived(asset_id)
    assert [item.id for item in after] != [item.id for item in before]
    assert len(after) == 1
    assert len(container.event_repository.list_video_scenes(asset_id)) == 1
    # 同步跑完，资产行上不再是 queued——响应里那个 status 说的就是这件事。
    assert container.asset_repository.get(asset_id).status.value == "processed"


def test_the_import_response_reports_the_scene_count(client: TestClient):
    """导入响应里的 ``video_scenes``（T512）：与 ``video_probed`` 同一个可观测面。

    没有它，「视频导入成功但一个场景都没建」在接口上完全看不出来——正是 S8a 修过的
    那种静默。图片一律 0。
    """
    body = import_video(client).json()

    assert body["video_scenes"] == 1


def test_reprocess_404s_on_a_missing_video(client: TestClient):
    assert client.post("/api/v1/videos/asset_nope/reprocess").status_code == 404


def test_video_scene_detail_carries_the_lineage(client: TestClient):
    """``GET /video-scenes/{id}``：场景事件 + 它指回的源片 + 它的关键帧。

    **这是「血缘列能用」那条验收的读侧**：S8a 不建场景（S8b 才建），所以这里手工种一份
    S8b 会写出来的数据，验的是读侧认得迁移 0010 的那几列。
    """
    asset_id = import_video(client, name="clip_hevc.mov").json()["asset_id"]
    database = client.app.state.container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, source_type, source_asset_id,"
            " source_scene_index, source_start_sec, source_end_sec, source_metadata_json,"
            " created_at, updated_at)"
            " VALUES ('evt_scene', 'album1', '视频场景', '视频场景', 'video_scene', ?,"
            " 2, 10.0, 15.0, ?, ?, ?)",
            (
                asset_id,
                '{"speech_segments":[{"start_sec":11.5,"end_sec":13.0,"text":"加入鸡肉"}]}',
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, created_at, updated_at, parent_asset_id, derived_kind,"
            " source_timestamp_sec, source_frame_index, source_scene_index)"
            " VALUES ('asset_frame', 'album1', 'f.webp', 'image', '/media/f', 1, 'processed',"
            " 'asset_frame', ?, ?, ?, 'video_keyframe', 12.5, 7, 2)",
            (NOW.isoformat(), NOW.isoformat(), asset_id),
        )
        # 同源片的**另一个**场景的帧：不该被带进来——按 scene_index 过滤是这一层的判据。
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, created_at, updated_at, parent_asset_id, derived_kind,"
            " source_timestamp_sec, source_frame_index, source_scene_index)"
            " VALUES ('asset_other', 'album1', 'g.webp', 'image', '/media/g', 1, 'processed',"
            " 'asset_other', ?, ?, ?, 'video_keyframe', 30.0, 8, 3)",
            (NOW.isoformat(), NOW.isoformat(), asset_id),
        )

    body = client.get("/api/v1/video-scenes/evt_scene").json()

    assert body["event"]["speech_segments"] == [{"start_sec": 11.5, "end_sec": 13.0, "text": "加入鸡肉"}]
    assert body["event"]["keyframe_timestamps_sec"] == [12.5]
    assert body["event"]["source_type"] == "video_scene"
    assert body["event"]["source_asset_id"] == asset_id
    assert body["event"]["source_scene_index"] == 2
    assert body["event"]["source_start_sec"] == pytest.approx(10.0)
    assert body["event"]["source_end_sec"] == pytest.approx(15.0)
    assert body["source_video"]["id"] == asset_id
    assert body["source_video"]["duration_sec"] == pytest.approx(12.5)
    assert [item["id"] for item in body["keyframe_assets"]] == ["asset_frame"]
    assert body["keyframe_assets"][0]["source_timestamp_sec"] == pytest.approx(12.5)
    assert body["keyframe_assets"][0]["parent_asset_id"] == asset_id


def test_video_scene_detail_404s_on_a_plain_event(client: TestClient):
    """不是 ``video_scene`` 的事件走这条路由必须是 404，而不是交出一份没有血缘的详情。"""
    database = client.app.state.container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, created_at, updated_at)"
            " VALUES ('evt_plain', 'album1', '普通事件', '家庭活动', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )

    assert client.get("/api/v1/video-scenes/evt_plain").status_code == 404
