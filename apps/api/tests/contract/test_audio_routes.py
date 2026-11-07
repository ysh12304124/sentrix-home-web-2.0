"""音频的导入与交付（S8c T521/T522）。

走真容器 + 真 SQLite，只把 ffprobe 适配器换成 conftest 里的 ``FakeAudioMetadataReader``
（与视频那条路同一条理由：契约测试不把「这台机器有没有 ffprobe」变成断言的前置条件）。

**不验转写**——用户裁决②「音频不转写，只做入库与播放」，`observations` 没有
``transcript`` 列，``compose_text`` 保持七字段（``s8-deviations.md`` §1.1）。
"""

from __future__ import annotations

import dataclasses

from fastapi.testclient import TestClient

AUDIO_BYTES = b"fake-m4a-bytes"


def import_audio(
    client: TestClient, *, name: str = "clip.m4a", content_type: str = "application/octet-stream"
):
    return client.post(
        "/api/v1/media/import",
        files={"file": (name, AUDIO_BYTES, content_type)},
        data={"scope_id": "album1"},
    )


def test_importing_audio_reports_that_it_was_probed(client: TestClient):
    """T521：音频不再静默空转——响应里必须看得出「真的探过」。

    ``audio_probed`` 是这条断言的可观测面。把 ``ImportAssets`` 里那次 ``_probe_audio``
    调用删掉，它立刻变 False。
    """
    body = import_audio(client).json()

    assert body["media_type"] == "audio"
    assert body["audio_probed"] is True


def test_a_successful_probe_lands_the_metadata_on_the_asset(client: TestClient):
    """探测结果要真的写进 ``assets.metadata_json``，不能只在响应里报个数。

    ``/assets/{id}`` 的 ``captured_at`` 走的是 ffprobe 那一份（QuickTime
    ``creationdate`` 的本地时区写法原样保留），不是导入时刻——拍摄时间必须接到与 EXIF
    同一条路上。
    """
    body = import_audio(client).json()

    summary = client.get(f"/api/v1/assets/{body['asset_id']}").json()
    # 保留原始偏移，拍摄时刻不依赖服务器TZ。
    assert summary["captured_at"] == "2026-08-02T14:55:56+08:00"
    assert summary["gps"] == {"latitude": 22.4846, "longitude": 114.5438}

    container = client.app.state.container
    with container.database.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM assets WHERE id = ?", (body["asset_id"],)
        ).fetchone()
    assert '"audio_metadata"' in row["metadata_json"]


def test_the_delivery_content_type_is_playable_not_octet_stream(client: TestClient):
    """**音频这边不重复 S8a 那个 octet-stream 问题。**

    上传方给的是 ``application/octet-stream``（curl 与整册导入都是这样），照抄它会让
    ``<audio>`` 拿到一个播不了的类型。音频不转码、交出去的就是原文件，没有第二次机会——
    类型必须由后端按后缀定。``?original=1`` 与默认两条路都要对。
    """
    asset_id = import_audio(client).json()["asset_id"]

    default = client.get(f"/api/v1/assets/{asset_id}/file")
    original = client.get(f"/api/v1/assets/{asset_id}/file", params={"original": "1"})

    assert default.status_code == 200
    assert default.headers["content-type"] == "audio/mp4"
    assert default.content == AUDIO_BYTES
    assert original.status_code == 200
    assert original.headers["content-type"] == "audio/mp4"
    assert original.content == AUDIO_BYTES


def test_audio_preview_is_not_a_500(client: TestClient):
    """``/preview`` 对音频曾与视频同一个 500：它无条件走 Pillow，而 PIL 打不开录音。

    音频的「预览」就是它自己（与视频的 mp4 同一个位置），所以这里要的是 200 + 音频类型。
    """
    asset_id = import_audio(client, name="song.mp3", content_type="audio/mpeg").json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == AUDIO_BYTES


def test_a_failed_probe_still_imports_and_is_visible(client: TestClient):
    """探测失败**不阻断导入**（与 EXIF/视频同一条口径），但要在响应里看得出来。"""
    container = client.app.state.container

    class ExplodingReader:
        def read(self, path):
            raise ValueError("ffprobe failed: boom")

    client.app.state.container = dataclasses.replace(container, audio_metadata_reader=ExplodingReader())

    response = import_audio(client)

    assert response.status_code == 201
    body = response.json()
    assert body["media_type"] == "audio"
    assert body["audio_probed"] is False

    with container.database.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM assets WHERE id = ?", (body["asset_id"],)
        ).fetchone()
    assert "ffprobe failed: boom" in row["metadata_json"]


def test_audio_is_reachable_by_file_name_and_media_type(client: TestClient):
    """T522：音频能按文件名（词法 ``file`` 锚点）搜到，也能被 ``media_type=audio`` 过滤到。

    - **不带结构化条件时 metadata 通道恒空**（``retrieve_metadata.py:79``）；音频没有
      视觉向量（``IndexAssetVisual`` 对非图片短路），Observation 语义也全空——所以
      唯一可能命中的是词法 ``file`` 锚点。「文件名查询有命中」因此证明文件名进得了检索。
    - ``media_type=audio`` 是结构化条件：metadata 通道按 ``asset.media_type`` 放行
      （``retrieve_metadata.py:97``，``search.py:131`` 用 ``MediaType(media_type)``），
      融合后 ``conditions`` 再排除一次。反向（``media_type=image``）必须把它挡掉——
      没有这一半，「过滤器没生效」与「真的只有一条音频」在响应上长得一样。
    """
    import_audio(client, name="客厅录音.m4a")

    by_name = client.get("/api/v1/search", params={"q": "客厅录音"})
    assert by_name.status_code == 200
    assert [hit["asset"]["file_name"] for hit in by_name.json()["items"]] == ["客厅录音.m4a"]

    by_media = client.get("/api/v1/search", params={"q": "录音", "media_type": "audio"})
    assert by_media.status_code == 200
    assert [hit["asset"]["media_type"] for hit in by_media.json()["items"]] == ["audio"]

    excluded = client.get("/api/v1/search", params={"q": "录音", "media_type": "image"})
    assert excluded.status_code == 200
    assert excluded.json()["items"] == []
