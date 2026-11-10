"""``/api/v1/timeline``、``/api/v1/events/{id}``、``/api/v1/imports`` 的契约测试。

只验 HTTP 映射、状态码与分页边界；链路的正确性在 integration（``test_read_timeline``
/ ``test_import_pipeline``），真照片 + 真模型的验收在 tests/e2e。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def jpeg_bytes(size=(48, 32), color="blue") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def album(tmp_path: Path, *, count: int = 1) -> Path:
    root = tmp_path / "album"
    root.mkdir(exist_ok=True)
    for index in range(count):
        (root / f"IMG_{index:04d}.jpg").write_bytes(jpeg_bytes(color=(index * 40 % 255, 10, 10)))
    return root


def photos(client: TestClient, scope_id: str = "album1") -> list[dict]:
    body = client.get("/api/v1/timeline", params={"scope_id": scope_id}).json()
    # 按日列表给的是紧凑事件卡；photos 只在事件详情里。
    result: list[dict] = []
    for day in body["days"]:
        for event in day["events"]:
            detail = client.get(f"/api/v1/events/{event['id']}").json()
            result.extend(detail["photos"])
    return result


def test_timeline_is_empty_before_any_import(client: TestClient):
    response = client.get("/api/v1/timeline", params={"scope_id": "album1"})

    assert response.status_code == 200
    body = response.json()
    assert body["scope_id"] == "album1"
    assert body["total"] == 0
    assert body["days"] == []


def test_pipeline_receipt_replays_the_response_without_executing_again(client, tmp_path, monkeypatch):
    from sentrix.application.media.import_album import ProcessAlbumPipeline

    staged = client.post(
        "/api/v1/imports/assets",
        json={
            "directory": str(album(tmp_path)),
            "scope_id": "receipt-scope",
        },
    ).json()
    payload = {"scope_id": "receipt-scope", "asset_ids": staged["asset_ids"], "operation_id": "original"}
    response = client.post("/api/v1/pipeline/process", json=payload)
    assert response.status_code == 200
    receipt = client.get("/api/v1/pipeline/operations/original").json()
    assert receipt["status"] == "completed" and receipt["result"] == response.json()
    monkeypatch.setattr(ProcessAlbumPipeline, "execute", lambda *_: pytest.fail("pipeline was replayed"))
    assert client.post("/api/v1/pipeline/process", json=payload).json() == response.json()
    assert client.post("/api/v1/pipeline/process", json={**payload, "asset_ids": []}).status_code == 409


def test_finalize_requires_processed_sources_and_never_prepares_media(client, tmp_path, monkeypatch):
    from sentrix.application.media.import_album import ProcessAlbumPipeline
    from sentrix.application.media.import_assets import ImportAssets

    staged = client.post(
        "/api/v1/imports/assets",
        json={
            "directory": str(album(tmp_path)),
            "scope_id": "finish-scope",
        },
    ).json()
    payload = {"scope_id": "finish-scope", "asset_ids": staged["asset_ids"]}
    rejected = client.post("/api/v1/pipeline/finalize", json={**payload, "operation_id": "too-early"})
    assert rejected.status_code == 409
    assert client.get("/api/v1/pipeline/operations/too-early").json()["status"] == "failed"
    assert client.post("/api/v1/pipeline/process", json=payload).status_code == 200
    monkeypatch.setattr(ImportAssets, "prepare_image", lambda *_: pytest.fail("source inference repeated"))
    event_ids = []
    summarize = ProcessAlbumPipeline._summarize_events

    def record_events(self, ids):
        event_ids.extend(ids)
        return summarize(self, ids)

    monkeypatch.setattr(ProcessAlbumPipeline, "_summarize_events", record_events)
    result = client.post("/api/v1/pipeline/finalize", json={**payload, "operation_id": "recovery"})
    assert result.status_code == 200, result.text
    assert event_ids
    assert result.json()["processed"] == 0 and result.json()["skipped"] == 1
    assert result.json()["summarized"] == len(event_ids)
    assert result.json()["memory_graph"] is not None
    assert client.get("/api/v1/pipeline/operations/recovery").json()["result"] == result.json()


def test_recover_processes_only_unfinished_sources_and_receipt_survives_tracker_restart(
    client, isolated_app, tmp_path, monkeypatch,
):
    from sentrix.application.media.import_assets import ImportAssets
    from sentrix.application.media.track_pipeline import TrackPipeline
    from sentrix.domain.media.models import AssetStatus
    from sentrix.infrastructure.persistence.pipeline_receipts import SqlitePipelineReceipts

    staged = client.post("/api/v1/imports/assets", json={
        "directory": str(album(tmp_path, count=3)), "scope_id": "resume-scope",
    }).json()
    ids = staged["asset_ids"]
    assert client.post("/api/v1/pipeline/process", json={
        "scope_id": "resume-scope", "asset_ids": ids[:1],
    }).status_code == 200
    container = isolated_app.state.container
    before = container.observation_repository.list_by_asset(ids[0])
    container.asset_repository.update_status(ids[1], AssetStatus.PROCESSING)
    prepare = ImportAssets.prepare_image
    prepared = []

    def record_prepare(self, asset):
        prepared.append(asset.id)
        return prepare(self, asset)

    monkeypatch.setattr(ImportAssets, "prepare_image", record_prepare)
    response = client.post("/api/v1/pipeline/recover", json={
        "scope_id": "resume-scope", "asset_ids": ids, "operation_id": "recover-checkpoint",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["processed"] == 2 and result["skipped"] == 1 and not result["failures"]
    assert result["resumed_asset_ids"] == ids[1:2]
    assert set(prepared) == set(ids[1:]) and len(prepared) == 2
    assert container.observation_repository.list_by_asset(ids[0]) == before
    restarted = TrackPipeline(SqlitePipelineReceipts(container.database))
    assert restarted.get("recover-checkpoint")["result"] == result
    assert restarted.execute(
        "recover-checkpoint", "resume-scope", ids, "recover", lambda: pytest.fail("replayed after restart"),
    ) == result


def test_import_batch_then_timeline_has_every_photo(client: TestClient, tmp_path: Path):
    imported = client.post(
        "/api/v1/imports",
        json={"directory": str(album(tmp_path, count=2)), "scope_id": "album1"},
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["imported"] == 2
    assert body["failures"] == []

    listed = photos(client)
    assert sorted(item["file_name"] for item in listed) == ["IMG_0000.jpg", "IMG_0001.jpg"]
    # 地点展示是后端算好的字符串：没有 GPS 时是空串，不是「未知」。
    assert {item["place"] for item in listed} == {""}


def test_import_batch_reports_each_file_once_across_reruns(client: TestClient, tmp_path: Path):
    root = str(album(tmp_path))
    client.post("/api/v1/imports", json={"directory": root, "scope_id": "album1"})

    again = client.post("/api/v1/imports", json={"directory": root, "scope_id": "album1"})

    assert again.json()["imported"] == 0
    assert again.json()["deduplicated"] == 1


def test_asset_import_and_pipeline_are_two_real_phases(client: TestClient, tmp_path: Path):
    root = str(album(tmp_path, count=2))

    staged = client.post("/api/v1/imports/assets", json={"directory": root, "scope_id": "album-split"})

    assert staged.status_code == 201
    staged_body = staged.json()
    assert staged_body["imported"] == 2
    assert len(staged_body["asset_ids"]) == 2
    assert staged_body["timings"]["asset_import"] >= 0
    listed = client.get("/api/v1/assets", params={"scope_id": "album-split"}).json()["items"]
    assert {item["status"] for item in listed} == {"queued"}
    assert client.get("/api/v1/timeline", params={"scope_id": "album-split"}).json()["total"] == 0

    processed = client.post(
        "/api/v1/pipeline/process",
        json={"scope_id": "album-split", "asset_ids": staged_body["asset_ids"]},
    )

    assert processed.status_code == 200
    body = processed.json()
    assert body["processed"] == 2
    assert body["failures"] == []

    for stage in ("source_memory_prepare", "source_memory_commit", "event_summarize"):
        timings = body["timings"]
        assert timings[stage + "_wall"] >= 0
        assert timings[stage + "_end_seconds"] >= timings[stage + "_start_seconds"] >= 0
        assert (
            abs(
                timings[stage + "_wall"]
                - (timings[stage + "_end_seconds"] - timings[stage + "_start_seconds"])
            )
            <= 0.002
        )
        assert timings[stage + "_end_seconds"] <= timings["pipeline_core"] + 0.001
    assert body["timings"]["source_memory_prepare"] >= 0
    assert body["timings"]["source_memory_commit"] >= 0
    spans = body["timing_spans"]
    major = [row for row in spans if row["stage"] in {"media_processing", "event_summarize", "finalize"}]
    assert [row["stage"] for row in major] == ["media_processing", "event_summarize", "finalize"]
    assert all(row["status"] == "completed" for row in major)
    for before, after in zip(major, major[1:], strict=False):
        assert before["finished_offset_seconds"] <= after["started_offset_seconds"]
    for row in spans:
        assert row["started_at"] <= row["finished_at"]
        assert row["elapsed_seconds"] >= 0
    assert sum(row["stage"] == "source_memory_prepare" for row in spans) == 2
    assert sum(row["stage"] == "source_memory_commit" for row in spans) == 2
    # Both contracts survive integration: per-task UTC spans and direct-caller offsets.
    for stage in ("source_memory_prepare", "source_memory_commit", "event_summarize"):
        nodes = [row for row in spans if row["stage"] == stage]
        wall = max(row["finished_offset_seconds"] for row in nodes) - min(
            row["started_offset_seconds"] for row in nodes
        )
        assert body["timings"][stage + "_wall"] == round(wall, 3)
        assert (
            abs(
                body["timings"][stage + "_wall"]
                - (body["timings"][stage + "_end_seconds"] - body["timings"][stage + "_start_seconds"])
            )
            <= 0.003
        )
    assert len([row for row in spans if row["stage"] == "pipeline_total"]) == 1
    assert {row["asset_id"] for row in spans if row["stage"] == "source_memory_prepare"} == set(
        staged_body["asset_ids"]
    )
    assert body["timings"]["source_memory_prepare_wall"] >= 0

    assert body["timings"]["memory_graph"] >= 0
    assert body["timings"]["total"] >= body["timings"]["memory_graph"]
    assert body["memory_graph"]["node_count"] > 0
    assert body["memory_graph"]["causal_status"] == "no_candidates"
    assert body["memory_graph"]["causal_candidate_pairs"] == 0
    assert body["memory_graph"]["keyframe_count"] == 0
    assert body["memory_graph"]["keyframe_visual_status"] == "no_frames"
    assert body["timings"]["causal_graph"] >= 0
    listed = client.get("/api/v1/assets", params={"scope_id": "album-split"}).json()["items"]
    assert {item["status"] for item in listed} == {"processed"}
    assert client.get("/api/v1/timeline", params={"scope_id": "album-split"}).json()["total"] > 0


def test_video_keyframes_and_their_memories_wait_for_pipeline(client: TestClient, tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "clip.mov").write_bytes(b"fake-mov-bytes")

    staged = client.post("/api/v1/imports/assets", json={"directory": str(root), "scope_id": "video-split"})
    assert staged.status_code == 201
    asset_id = staged.json()["asset_ids"][0]
    before = client.get(f"/api/v1/videos/{asset_id}").json()
    assert before["video"]["metadata"]["duration_sec"] == 12.5
    assert before["scenes"] == []
    assert client.app.state.container.video_repository.list_derived(asset_id) == []  # type: ignore[attr-defined]

    processed = client.post(
        "/api/v1/pipeline/process",
        json={"scope_id": "video-split", "asset_ids": [asset_id]},
    )
    assert processed.status_code == 200
    body = processed.json()
    assert body["processed"] == 1
    assert body["video_scenes"] == 1
    for stage in (
        "total",
        "scene_planning",
        "scene_write",
        "keyframe_extraction",
        "keyframe_asset_write",
        "keyframe_memory",
    ):
        timings = body["timings"]
        assert timings["video_" + stage + "_wall"] >= 0
        assert timings["video_" + stage + "_wall"] <= timings["video_total_wall"] + 0.001
        assert timings["video_" + stage + "_end_seconds"] <= timings["pipeline_core"] + 0.001
    assert body["timings"]["video_keyframe_extraction"] >= 0
    assert body["timings"]["video_keyframe_memory"] >= 0
    span_keys = {row["stage"] for row in body["timing_spans"]}
    assert {
        "video_total",
        "video_scene_planning",
        "video_scene_write",
        "video_keyframe_extraction",
        "video_keyframe_asset_write",
        "video_keyframe_memory",
    } <= span_keys
    assert body["timings"]["video_keyframe_memory_wall"] >= 0
    assert sum(row["stage"] == "source_memory_prepare" for row in body["timing_spans"]) == 1

    assert len(client.get(f"/api/v1/videos/{asset_id}").json()["scenes"]) == 1
    assert len(client.app.state.container.video_repository.list_derived(asset_id)) == 1  # type: ignore[attr-defined]


def test_import_batch_rejects_a_missing_directory(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/v1/imports",
        json={"directory": str(tmp_path / "gone"), "scope_id": "album1"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "directory not found"


def test_import_batch_rejects_an_empty_directory_field(client: TestClient):
    response = client.post("/api/v1/imports", json={"directory": "", "scope_id": "album1"})

    assert response.status_code == 422


def test_import_batch_requires_a_directory_field(client: TestClient):
    assert client.post("/api/v1/imports", json={"scope_id": "album1"}).status_code == 422


def test_event_detail_404_for_an_unknown_event(client: TestClient):
    response = client.get("/api/v1/events/evt_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "event not found"


def test_event_detail_returns_photos_after_an_import(client: TestClient, tmp_path: Path):
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path)), "scope_id": "album1"})
    event_id = client.get("/api/v1/timeline", params={"scope_id": "album1"}).json()["days"][0]["events"][0][
        "id"
    ]

    response = client.get(f"/api/v1/events/{event_id}")

    assert response.status_code == 200
    assert response.json()["id"] == event_id
    assert len(response.json()["photos"]) == 1


def test_timeline_limit_and_offset_bounds(client: TestClient):
    assert client.get("/api/v1/timeline", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/timeline", params={"limit": 500}).status_code == 422
    assert client.get("/api/v1/timeline", params={"offset": -1}).status_code == 422


def test_place_filter_is_applied_by_the_backend(client: TestClient, tmp_path: Path):
    """地点筛选在后端：前端只把用户输入传过来，不做任何地名计算（计划 Task 61）。

    契约测试里的照片没有 GPS，所以这里先把事件的地点补上（走仓储，不走 HTTP），
    再断言筛选真的按 ``place`` / ``admin_district`` 过滤。
    """
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path)), "scope_id": "album1"})
    container = client.app.state.container  # type: ignore[attr-defined]
    event = container.event_repository.list_by_scope("album1", limit=10)[0]
    container.event_repository.fold_observation(
        event.id,
        time_start=event.time_start,
        time_end=event.time_end,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="",
        summary="",
        confidence=event.confidence,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )

    hit = client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "萧山"})
    miss = client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "清迈"})

    assert hit.status_code == 200 and miss.status_code == 200
    assert [item["id"] for day in hit.json()["days"] for item in day["events"]] == [event.id]
    assert miss.json()["days"] == []
    assert miss.json()["total"] == 0


def test_the_timeline_without_a_scope_covers_every_album(client: TestClient, tmp_path: Path) -> None:
    """**空 scope = 全部相册**，与资料库那条同一形状，但根因多一层。

    `read_timeline` 里原有一句 `target_scope = scope_id or DEFAULT_SCOPE_ID`，把空值
    **强行兜底**成 `home-default` 再去过滤——而那个空间在 2.0 的空库里根本不存在。
    表现是「时间线一个事件都没有」，而事件其实好好地在一个具体相册里。

    1.0 `db.py:1514` 的 `if scope_id:` 才是这个语义：不传 = 不按空间过滤。
    """
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path, count=3)), "scope_id": "album1"})

    everything = client.get("/api/v1/timeline", params={"scope_id": ""}).json()
    one_album = client.get("/api/v1/timeline", params={"scope_id": "album1"}).json()

    assert everything["total"] == one_album["total"] > 0, "空 scope 必须覆盖到具体相册里的事件"
    assert everything["scope_id"] == "", "回给调用方的是用户请求的范围，不是内部的兜底值"


def test_import_progress_is_queryable_during_batch_finalization(client, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sentrix.api.routes import timeline

    entered, release = threading.Event(), threading.Event()

    def blocked_insights(*args):
        entered.set()
        assert release.wait(5)
        return None

    monkeypatch.setattr(timeline, "_maybe_run_insights", blocked_insights)
    payload = {"directory": str(album(tmp_path)), "scope_id": "album1", "operation_id": "tracked"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/v1/imports", json=payload)
        try:
            assert entered.wait(5)
            response = client.get("/api/v1/imports/tracked/progress")
            assert response.status_code == 200
            fact = response.json()
            assert (fact["completed"], fact["total"], fact["imported"]) == (1, 1, 1)
            assert fact["status"] == "running" and fact["stage"] == "person_insights"
            assert fact["observed_at"] >= fact["updated_at"]
            assert client.post("/api/v1/imports", json=payload).status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 201
    final = client.get("/api/v1/imports/tracked/progress").json()
    assert final["status"] == "completed" and final["sequence"] > fact["sequence"]
    assert client.get("/api/v1/imports/unknown/progress").status_code == 404


def test_tracked_import_failure_keeps_a_truthful_terminal(client, tmp_path):
    response = client.post(
        "/api/v1/imports",
        json={
            "directory": str(tmp_path / "missing"),
            "operation_id": "bad-directory",
        },
    )
    assert response.status_code == 400
    fact = client.get("/api/v1/imports/bad-directory/progress").json()
    assert fact["status"] == "failed" and fact["total"] is None
    assert fact["completed"] == 0 and "400" in fact["error"]


def test_explicit_import_and_pipeline_accept_a_complete_large_source_list(client, tmp_path, monkeypatch):
    from sentrix.application.media.import_album import AlbumStageResult, ImportAlbum

    ids = [f"asset_large_{index}" for index in range(1533)]

    def stage_only(self, request):
        assert request.limit == 1533
        return AlbumStageResult(
            scope_id=request.scope_id, directory=request.directory, imported=len(ids), asset_ids=tuple(ids)
        )

    with monkeypatch.context() as patch:
        patch.setattr(ImportAlbum, "stage_only", stage_only)
        staged = client.post(
            "/api/v1/imports/assets",
            json={"scope_id": "large-sources", "directory": str(tmp_path), "limit": 1533},
        )
    assert staged.status_code == 201
    assert staged.json()["asset_ids"] == ids
    # 未存在的资产应逐项报错，不能在请求层截断或拒绝1533项清单。
    processed = client.post("/api/v1/pipeline/process", json={"scope_id": "large-sources", "asset_ids": ids})
    assert processed.status_code == 200
    assert processed.json()["processed"] == 0
    assert len(processed.json()["failures"]) == len(ids)


def test_legacy_import_retains_its_synchronous_batch_limit(client, tmp_path):
    response = client.post("/api/v1/imports", json={"directory": str(tmp_path), "limit": 1001})
    assert response.status_code == 422
