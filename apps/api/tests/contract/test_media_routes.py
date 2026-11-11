import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.application.memories.maintain_facts import MaintainFacts
from sentrix.domain.media.models import Observation
from sentrix.domain.retrieval.scheme import IMAGE_MODEL


def jpeg_bytes(size=(64, 48), color="green") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_import_returns_201_and_asset_id(client: TestClient):
    response = client.post(
        "/api/v1/media/import",
        files={"file": ("IMG_0001.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"].startswith("asset_")
    assert body["media_type"] == "image"
    assert body["deduplicated"] is False


def test_reimport_same_bytes_is_deduplicated(client: TestClient):
    payload = jpeg_bytes()
    for _ in range(2):
        response = client.post(
            "/api/v1/media/import",
            files={"file": ("a.jpg", payload, "image/jpeg")},
            data={"scope_id": "album1"},
        )

    assert response.status_code == 201
    assert response.json()["deduplicated"] is True


def test_list_assets_for_scope(client: TestClient):
    client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    )
    client.post(
        "/api/v1/media/import",
        files={"file": ("b.jpg", jpeg_bytes(color="red"), "image/jpeg")},
        data={"scope_id": "album2"},
    )

    response = client.get("/api/v1/assets", params={"scope_id": "album1"})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_asset_file_returns_original_bytes(client: TestClient):
    payload = jpeg_bytes()
    asset_id = client.post(
        "/api/v1/media/import", files={"file": ("a.jpg", payload, "image/jpeg")}, data={"scope_id": "album1"}
    ).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/file")

    assert response.status_code == 200
    assert response.content == payload


def test_asset_preview_is_jpeg(client: TestClient):
    asset_id = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_missing_asset_is_404(client: TestClient):
    assert client.get("/api/v1/assets/asset_nope").status_code == 404
    assert client.get("/api/v1/assets/asset_nope/file").status_code == 404


def test_delete_asset_removes_it_from_library_and_is_idempotent(client: TestClient):
    asset_id = client.post(
        "/api/v1/media/import",
        files={"file": ("delete-me.jpg", jpeg_bytes(color="blue"), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()["asset_id"]

    deleted = client.delete(f"/api/v1/assets/{asset_id}")

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}/file").status_code == 404
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 200
    missing = client.delete("/api/v1/assets/unknown")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    history = client.get("/api/v1/assets/deletions", params={"scope_id": "album1"})
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    record = history.json()["items"][0]
    assert record["asset_id"] == asset_id
    assert record["file_name"] == "delete-me.jpg"
    assert record["scope_id"] == "album1"
    assert record["asset_count"] == 1
    assert record["files_done"] is True
    assert record["vectors_done"] is True
    assert record["deleted_at"].endswith("Z")
    assert client.get("/api/v1/assets/deletions", params={"scope_id": "other"}).json() == {"items": []}
    assert client.get("/api/v1/assets/deletions", params={"limit": 0}).status_code == 422


def test_import_reports_and_writes_the_visual_vector(client: TestClient):
    """导入成功必须真的留下视觉向量，并且这件事在响应里看得见。

    1.0 的同类失败（223 张图没有任何视觉向量）在接口上完全没有痕迹，只能事后
    手工清点；``indexed`` 就是补上的那个痕迹。
    """
    body = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()

    assert body["indexed"] is True

    container = client.app.state.container
    assert (
        container.vector_repository.has_vector(
            space="visual",
            source_type="asset",
            source_id=body["asset_id"],
            model_name=IMAGE_MODEL,
        )
        is True
    )


def test_reimport_does_not_duplicate_the_visual_vector(client: TestClient):
    payload = jpeg_bytes()
    for _ in range(2):
        client.post(
            "/api/v1/media/import",
            files={"file": ("a.jpg", payload, "image/jpeg")},
            data={"scope_id": "album1"},
        )

    assert client.app.state.container.vector_repository.count(space="visual") == 1


def test_the_import_route_wires_fact_maintenance(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    """导入路径必须**有**事实写入的调用方。

    ``domain/memories/facts.py`` 与 ``repositories/facts.py`` 建好之后一度零生产调用
    （1.0 也只有 ``process()`` 会调，``_commit_semantic_image`` 不调）。这里用替身
    替换用例本身，验的是装配点 ``build_import_assets`` 真的把 ``MaintainFacts`` 接上了、
    且每导入一条观察就会被调一次——**S4 的候选来源与门禁都还是空的，所以观察不到
    facts 表有行**，能观察到的只有调用本身。见 ``tests/integration/test_maintain_facts.py``。
    """
    seen: list[Observation] = []

    def record(self: MaintainFacts, observation: Observation) -> tuple[str, ...]:
        seen.append(observation)
        return ()

    monkeypatch.setattr(MaintainFacts, "maintain", record)

    response = client.post(
        "/api/v1/media/import",
        files={"file": ("IMG_0001.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    )

    assert response.status_code == 201
    assert [item.id for item in seen] == [response.json()["observation_id"]]


def test_the_import_route_creates_the_memory_space(client: TestClient):
    """装配点 ``build_import_assets`` 必须把 ``spaces`` 接上（裁决一）。

    与 ``test_the_import_route_wires_fact_maintenance`` 同一条理由：集成用例自己构造
    ``ImportAssets`` 并显式传协作者，**装配点漏了它看不出来**。这条只走 HTTP，断言的是
    「导完之后库里真有一行空间」——把 ``spaces=container.spaces`` 那一行删掉，它立刻红。

    断言不依赖人脸权重：检测器没权重就是空表，而空间行与有没有脸无关。
    """
    response = client.post(
        "/api/v1/media/import",
        files={"file": ("IMG_0001.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album-fresh"},
    )

    assert response.status_code == 201
    space = client.app.state.container.spaces.get("album-fresh")
    assert space is not None
    assert (space.name, space.kind, space.status) == ("album-fresh", "household", "active")


def test_the_album_route_creates_the_memory_space(client: TestClient, tmp_path):
    """整册导入是另一条入口（``/api/v1/imports`` → ``ImportAlbum`` → ``ImportAssets``）。

    两条入口共用 ``build_import_assets``，但它们各自的端到端形状不同：单张那条走
    ``observe=False`` 的快路径，整册这条带上观察器与批末总结。两边都钉一次，
    「整册少了空间行」这种差异才不会被两条用例各看一半放过去。
    """
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "IMG_0002.jpg").write_bytes(jpeg_bytes(color="blue"))

    response = client.post(
        "/api/v1/imports",
        json={"directory": str(photos), "scope_id": "album-batch", "limit": 10},
    )

    assert response.status_code == 201
    space = client.app.state.container.spaces.get("album-batch")
    assert space is not None
    assert space.name == "album-batch"


def test_listing_assets_without_a_scope_covers_every_album(client: TestClient) -> None:
    """**空 scope = 全部相册**（1.0 db.py:1514 的 if scope_id:）。

    这一条不是「顺手加的边界用例」：2.0 原先写成无条件的 WHERE scope_id = ?，
    于是前端的「全部相册」查的是 scope_id = ''，返回空表——**而那个空表看起来与
    「这个相册真的没照片」一模一样**。用户看到的是「打开就没图片」，数据其实好好地
    在库里（scope_id='album1'）。
    """
    for scope_id, name in (("album1", "a.jpg"), ("album2", "b.jpg")):
        response = client.post(
            "/api/v1/media/import",
            files={"file": (name, b"\xff\xd8\xff\xd9", "image/jpeg")},
            data={"scope_id": scope_id},
        )
        assert response.status_code == 201, response.text

    everything = client.get("/api/v1/assets", params={"scope_id": ""}).json()["items"]
    one_album = client.get("/api/v1/assets", params={"scope_id": "album1"}).json()["items"]

    assert len(everything) == 2, "空 scope 必须跨全部相册"
    assert len(one_album) == 1
    # 断言**两个相册都被覆盖**，而不只是条数对：条数对但两条都来自 album1 也是错的，
    # 而那种错在只看计数时看不出来。
    assert {item["file_name"] for item in everything} == {"a.jpg", "b.jpg"}


def test_asset_endpoints_carry_the_keyframe_lineage(client: TestClient):
    """``/assets`` 与 ``/assets/{id}`` 必须带回派生帧的血缘三列。

    1.0 的 ``/api/assets`` 是 ``SELECT * FROM assets``，这几列顺带就回了；PhotoBench 的
    关键帧分析靠 ``derived_kind`` / ``parent_asset_id`` 认派生物、靠
    ``source_timestamp_sec`` 算时间跨度（``benchmark_orchestrator.py:5712/5798``）。
    2.0 的窄投影漏了它们——这条同时钉住列表与单条两条路，并钉住「普通照片是 None」。
    """
    source_id = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()["asset_id"]
    database = client.app.state.container.database
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, created_at, updated_at, parent_asset_id, derived_kind,"
            " source_timestamp_sec, source_frame_index, source_scene_index)"
            " VALUES ('asset_frame', 'album1', 'f.jpg', 'image', '/media/f', 1, 'processed',"
            " 'asset_frame', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?,"
            " 'video_keyframe', 12.5, 7, 2)",
            (source_id,),
        )

    items = client.get("/api/v1/assets", params={"scope_id": "album1"}).json()["items"]
    derived = next(item for item in items if item["id"] == "asset_frame")
    assert derived["derived_kind"] == "video_keyframe"
    assert derived["parent_asset_id"] == source_id
    assert derived["source_timestamp_sec"] == pytest.approx(12.5)
    # 普通照片这三位是 None——「有血缘」与「没血缘」必须分得开。
    plain = next(item for item in items if item["id"] == source_id)
    assert plain["derived_kind"] is None
    assert plain["parent_asset_id"] is None
    assert plain["source_timestamp_sec"] is None
    # 单条走的是同一个 schema，也要填。
    single = client.get("/api/v1/assets/asset_frame").json()
    assert single["derived_kind"] == "video_keyframe"
    assert single["parent_asset_id"] == source_id
