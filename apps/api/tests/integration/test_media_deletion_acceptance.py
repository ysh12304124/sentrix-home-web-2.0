"""Exercise every deletion stage through ASGI against disposable stores."""

import dataclasses
import hashlib
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient
from test_media_cleanup import managed
from test_media_deletion import asset, observation
from test_media_deletion_plans import insert
from test_media_rebuild import Model
from test_media_reconciliation import shared
from test_run_turn import ScriptedModel, final_call, tool_call

from sentrix.bootstrap.assistant_wiring import build_run_turn
from sentrix.domain.media.models import MediaType
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.assistant.chat_client import OpenAIChatModel
from sentrix.infrastructure.assistant.model_endpoint import ServedModel
from sentrix.infrastructure.assistant.turn_executor import TurnExecutor
from sentrix.infrastructure.media.cleanup_files import unlink_managed
from sentrix.infrastructure.models.legacy import BgeSidecarEmbedder
from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
from sentrix.infrastructure.vector import qdrant_index
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

TABLES = (
    "assets",
    "observations",
    "face_instances",
    "entities",
    "face_clusters",
    "events",
    "facts",
    "relationships",
    "event_observations",
    "event_participants",
    "memory_vectors",
    "text_vectors",
    "observation_search_fts",
    "observation_search_terms",
    "person_portrait_revisions",
    "family_person_portraits",
    "family_relationships",
    "semantic_profiles",
    "memory_graph_nodes",
    "memory_graph_edges",
    "memory_graph_builds",
    "memory_rotation_sequences",
    "media_deletions",
    "media_tombstones",
    "media_evidence_revocations",
    "media_cleanup_steps",
    "media_scope_epochs",
    "import_jobs",
    "video_jobs",
    "media_rebuild_jobs",
)


def integrity(db):
    with db.connect() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        versions = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        assert {f"{n:04d}" for n in range(28, 33)} <= versions


def snapshot(db, index, root):
    with db.connect() as c:
        rows = {table: [dict(r) for r in c.execute(f'SELECT * FROM "{table}"')] for table in TABLES}
    files = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for name in ("media", "previews", "video-jobs")
        for p in sorted((root / name).rglob("*"))
        if p.is_file()
    }
    return {
        "counts": {k: len(v) for k, v in rows.items()},
        "rows": rows,
        "files": files,
        "qdrant": index.deletion_inventory(),
    }


@pytest.mark.parametrize("case,extra", [("image", 0), ("shared", 12), ("video", 100)])
def test_full_deletion_api(isolated_app, tmp_path, monkeypatch, case, extra):
    real_model = os.environ.get("SENTRIX_ACCEPTANCE_REAL_MODEL") == "1"
    if real_model:
        assert case == "shared"
        extra = 0
    container = isolated_app.state.container
    if real_model:
        container = dataclasses.replace(
            container, text_embedder=BgeSidecarEmbedder("http://127.0.0.1:8101", timeout_s=20)
        )
    db, index, settings = container.database, container.vector_index, container.settings
    root = tmp_path.resolve()
    assert Path(os.environ["SENTRIX_DATA_DIR"]).resolve() == root == settings.data_dir.resolve()
    assert db.path.resolve() == root / "sentrix.db"
    assert index.path.resolve() == root / "qdrant"
    for name in ("media", "previews", "video-jobs"):
        (root / name).mkdir(exist_ok=True)
        assert (root / name).resolve() == root / name
    integrity(db)

    if case == "shared":
        shared(db)
        with db.connect() as c, c:
            c.execute("UPDATE observations SET caption='已撤证：金色纸船' WHERE id='oa'")
            c.execute("UPDATE observations SET caption='保留证据：妈妈在公园读书' WHERE id='ob'")
            c.execute("UPDATE events SET summary='旧摘要：妈妈在公园拿着金色纸船' WHERE id='event'")
            for table, fields in (
                (
                    "family_person_portraits",
                    dict(
                        id="human",
                        entity_id="p",
                        source="user_override",
                        locked=1,
                        portrait_text="人工维护：喜欢阅读",
                    ),
                ),
                (
                    "person_portrait_revisions",
                    dict(
                        id="locked",
                        person_id="p",
                        revision=1,
                        portrait_text="旧锁定画像：金色纸船",
                        trigger_type="manual",
                        prompt_version="v",
                        status="user_locked",
                        evidence_refs_json='[{"kind":"observation","id":"oa"}]',
                    ),
                ),
            ):
                insert(c, table, scope_id="s", created_at="2026-11-10", updated_at="2026-11-10", **fields)
            insert(c, "memory_graph_builds", scope_id="s", build_revision="old", built_at="2026-11-10")
            insert(
                c,
                "memory_graph_nodes",
                id="old-graph",
                scope_id="s",
                node_type="person",
                label="旧节点：金色纸船",
                build_revision="old",
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
            insert(
                c,
                "memory_rotation_sequences",
                sequence_id="old-rotation",
                scope_id="s",
                day="2026-11-10",
                event_ids_json='["event"]',
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        container.family_graph_repository.set_relationship(
            "s", "p", "母亲", "p2", "儿子", source="user_override"
        )
    else:
        source = asset(db, kind=MediaType.VIDEO if case == "video" else MediaType.IMAGE)
        observation(db, source, "oa")
        observation(db, asset(db, "b"), "ob")

    targets = ["a"]
    if case == "video":
        for name, parent in (("frame1", "a"), ("frame2", "a"), ("nested", "frame1")):
            observation(db, asset(db, name, parent=parent), "o-" + name)
            targets.append(name)
    healthy = ["b", "outside"]
    observation(db, asset(db, "outside", scope="other"), "outside-ob")
    for i in range(extra):
        name = f"healthy-{i}"
        observation(db, asset(db, name), f"obs-{i}")
        healthy.append(name)
    paths = {aid: managed(db, aid) for aid in targets + healthy}
    for aid, path in paths.items():
        if case == "video" and aid == "a":
            continue
        image = Image.new("RGB", (160, 96), "lightblue")
        draw = ImageDraw.Draw(image)
        draw.text((8, 8), "PARK / READING" if aid in {"a", "b"} else aid, fill="black")
        if aid == "a" and case == "shared":
            draw.polygon([(30, 70), (90, 70), (77, 84), (45, 84)], fill="gold")
        image.save(path, format="JPEG")

    ffmpeg = shutil.which("ffmpeg")
    if case == "video":
        if ffmpeg:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=64x64:r=8:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=64x64:r=8:d=1",
                    "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0",
                    "-c:v",
                    "libx264",
                    "-f",
                    "mp4",
                    str(paths["a"]),
                ],
                check=True,
                timeout=20,
            )
        with db.connect() as c, c:
            for number, frame in enumerate(("frame1", "frame2")):
                scene = f"scene-{number}"
                insert(
                    c,
                    "events",
                    id=scene,
                    scope_id="s",
                    source_asset_id="a",
                    source_type="video_scene",
                    source_scene_index=number,
                    source_start_sec=number,
                    source_end_sec=number + 1,
                    created_at="2026-11-10",
                    updated_at="2026-11-10",
                )
                insert(
                    c,
                    "event_observations",
                    event_id=scene,
                    observation_id=f"o-{frame}",
                    created_at="2026-11-10",
                )
            insert(
                c,
                "video_jobs",
                job_id="acceptance-video",
                asset_id="a",
                video_hash="synthetic-video",
                status="COMPLETED",
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        for relative in ("media/derived/video/a/scene.json", "video-jobs/acceptance-video/speech.json"):
            artifact = root / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_text("{}")
    for aid in targets:
        (root / "previews" / f"{aid}-480.jpg").write_bytes(b"acceptance-preview")
    if case == "shared":
        with db.connect() as c, c:
            c.execute("UPDATE assets SET path=? WHERE id='outside'", (str(paths["a"]),))
    records = [
        VectorRecord(
            scope_id="s",
            space="visual",
            source_type="asset",
            source_id=aid,
            model_name=model_id,
            vector=(1.0,) * 8,
        )
        for aid in targets + ["b"]
        for model_id in (container.visual_embedder.model_id, "acceptance-second-model")
    ]
    for record in records:
        SqliteVectorRepository(db).upsert(record)
    index.upsert(records)
    text_records = []
    for aid in targets + ["b"]:
        for obs in container.observation_repository.list_by_asset(aid):
            indexed = obs if obs.caption else dataclasses.replace(obs, caption=f"隔离测试素材 {aid}")
            container.lexical_index.refresh_from_observation(indexed)
            text_records.append(
                VectorRecord(
                    scope_id="s",
                    space="semantic",
                    source_type="observation",
                    source_id=obs.id,
                    model_name=container.text_embedder.model_id,
                    vector=tuple(container.text_embedder.embed_query(indexed.caption))
                    if real_model
                    else (1.0,) * 8,
                    metadata={"observation_id": obs.id, "asset_id": aid},
                )
            )
    for record in text_records:
        container.text_vector_repository.upsert(record)
    index.upsert(text_records)

    saved = root / "snapshot"
    saved.mkdir()
    before = snapshot(db, index, root)
    with db.connect() as c, sqlite3.connect(saved / "sentrix.db") as backup:
        c.backup(backup)
    index.close()
    for name in ("qdrant", "media", "previews", "video-jobs"):
        shutil.copytree(root / name, saved / name)
    with sqlite3.connect(saved / "sentrix.db") as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT count(*) FROM assets").fetchone()[0] == len(targets + healthy)
    restored_index = QdrantVectorIndex(saved / "qdrant")
    try:
        assert restored_index.deletion_inventory() == before["qdrant"]
    finally:
        restored_index.close()
    assert index.deletion_inventory() == before["qdrant"]

    metrics = {}

    def measure(name, function, *args, **kwargs):
        now = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            metrics[name] = metrics.get(name, 0.0) + time.perf_counter() - now

    for method in ("_sqlite", "_inventory", "_resources", "_verify"):
        original = getattr(SqliteCleanupRepository, method)

        def measured(self, *args, method=method, original=original, **kwargs):
            return measure(method, original, self, *args, **kwargs)

        monkeypatch.setattr(SqliteCleanupRepository, method, measured)
    original_init = SqliteCleanupRepository.__init__

    def timed_init(self, *args, **kwargs):
        kwargs["remove_file"] = lambda *args: measure("file_unlink", unlink_managed, *args)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(SqliteCleanupRepository, "__init__", timed_init)
    original_delete = index.delete_exact
    monkeypatch.setattr(index, "delete_exact", lambda *args: measure("qdrant_delete", original_delete, *args))
    model = Model()
    qa = ScriptedModel(
        [
            tool_call("search_memories", "search-before", query="妈妈在公园读书"),
            final_call("这是测试相册的检索结果。"),
            tool_call("search_memories", "search-after", query="妈妈在公园读书"),
            final_call("这是更新后的检索结果。"),
        ]
    )
    model_outputs = []
    if real_model:
        endpoint = "http://127.0.0.1:8100/v1"
        served = ServedModel(endpoint, generation_profile="qwen35_nonthinking", context_tokens=8192)
        backend = OpenAIChatModel(endpoint, served, context_tokens=8192)
        model.model_id = served.current()

    def complete(messages, tools):
        if not messages[0].content.startswith("仅依据给定原始观察生成"):
            if real_model:
                return measure("question_model", backend.complete, messages, tools)
            return qa.complete(messages, tools)
        if real_model:
            model.calls.append(json.loads(messages[-1].content))
            reply = measure("model_inference", backend.complete, messages, tools)
        else:
            reply = measure("model_inference", scripted_complete, messages, tools)
        model_outputs.append(reply.content)
        (root / "model-output.json").write_text(
            json.dumps(dict(inputs=model.calls, outputs=model_outputs), ensure_ascii=False, indent=2)
        )
        return reply

    scripted_complete = model.complete
    model.complete = complete
    isolated_app.state.container = dataclasses.replace(container, chat_model=model)

    def new_turn_executor():
        return TurnExecutor(
            turns=container.turn_repository,
            run_turn_factory=lambda: build_run_turn(isolated_app.state.container),
            broker=container.turn_event_broker,
            dependencies_ready=lambda: (True, ""),
            result_sets=container.result_set_repository,
        )

    scope = {"scope_id": "s"}

    def post(client, url, code=200):
        response = client.post(url, json=scope)
        assert response.status_code == code, response.text
        return response.json()

    def search(client):
        response = client.get("/api/v1/search", params={**scope, "q": "妈妈在公园读书"})
        assert response.status_code == 200, response.text
        return response.json()

    def question(client):
        conversation = post(client, "/api/v1/assistant/conversations", 201)
        response = client.post(
            f"/api/v1/assistant/conversations/{conversation['id']}/turns",
            json={"message": "给我看妈妈在公园读书的照片", "include_debug": True},
        )
        assert response.status_code == 202, response.text
        endpoint = f"/api/v1/assistant/turns/{response.json()['turn_id']}"
        deadline = time.monotonic() + (120 if real_model else 10)
        while time.monotonic() < deadline:
            result = client.get(endpoint).json()
            if result["result"] is not None:
                (root / f"question-{response.json()['turn_id']}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2)
                )
                return result
            time.sleep(0.025)
        pytest.fail(f"question did not reach terminal state: {result}")

    def inaccessible(client):
        for aid in targets:
            for suffix in ("", "/file", "/preview", "/thumbnail", "/poster"):
                assert client.get(f"/api/v1/assets/{aid}{suffix}").status_code == 404

    isolated_app.state.turn_executor = new_turn_executor()
    with TestClient(isolated_app) as live:
        before["search"] = search(live)
        if case == "shared":
            before["question"] = question(live)
            if not real_model:
                assert "a" in before["question"]["result"]["retrieved_candidates"], before["question"]
        started = time.perf_counter()
        deletion = measure("isolate", post, live, "/api/v1/assets/a/deletion", 202)
        assert deletion["status"] == "isolated"
        did = deletion["deletion_id"]
        url = f"/api/v1/media-deletions/{did}"
        assert post(live, "/api/v1/assets/a/deletion", 202)["deletion_id"] == did
        assert live.get(url, params={"scope_id": "other"}).status_code == 404
        inaccessible(live)
        assert all(paths[aid].exists() for aid in targets)
        if case == "video":
            for path in ("/videos/a", "/videos/a/scenes", "/video-scenes/scene-0", "/video-scenes/scene-1"):
                assert live.get("/api/v1" + path).status_code == 404
        plan = measure("plan", post, live, url + "/plan")
        reconciled = measure("reconcile", post, live, url + "/reconcile")
        assert reconciled["status"] == "reconciled"

    # Restart ASGI lifespan after reconciliation. The same durable stores are reopened.
    index.close()
    isolated_app.state.turn_executor = new_turn_executor()
    with TestClient(isolated_app) as live:
        cleanup = measure("cleanup", post, live, url + "/cleanup")
        assert cleanup["validation"]["storage_cleaned"], cleanup
        rebuild_started = time.perf_counter()
        while time.perf_counter() - rebuild_started < (150 if real_model else 15):
            status = live.get(url + "/rebuild", params=scope).json()
            if status["state"] in {"completed", "failed", "blocked"}:
                (root / "last-rebuild.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
                break
            time.sleep(0.025)
        assert status["state"] == "completed", status
        metrics["worker_wait_after_cleanup"] = time.perf_counter() - rebuild_started
        metrics["lifecycle_with_restart"] = time.perf_counter() - started
        inaccessible(live)
        for aid in ("b", "outside"):
            assert live.get(f"/api/v1/assets/{aid}/file").status_code == 200
        assert live.get(url + "/plan", params=scope).status_code == 200
        assert live.get(url + "/reconciliation", params=scope).status_code == 200
        assert post(live, url + "/rebuild", 202)["state"] == "completed"
        after = snapshot(db, index, root)
        after["search"] = search(live)
        if case == "shared":
            after["question"] = question(live)
            candidates = after["question"]["result"]["retrieved_candidates"]
            assert not set(targets) & set(candidates), after["question"]
            if not real_model:
                assert "b" in candidates, after["question"]
        assert not set(targets) & {item["asset"]["id"] for item in after["search"]["items"]}

    integrity(db)
    assert {r["id"] for r in after["rows"]["assets"]} == set(healthy)
    assert not set(targets) & {str(r["payload"]["source_id"]) for r in after["qdrant"]}
    assert not set(targets) & {r["source_id"] for r in after["rows"]["memory_vectors"]}
    deleted_obs = {r["id"] for r in before["rows"]["observations"] if r["asset_id"] in targets}
    assert not deleted_obs & {r["source_id"] for r in after["rows"]["text_vectors"]}
    assert not deleted_obs & {str(r["payload"]["source_id"]) for r in after["qdrant"]}
    assert not deleted_obs & {r["observation_id"] for r in after["rows"]["observation_search_fts"]}
    assert all((aid == "a" and case == "shared") == paths[aid].exists() for aid in targets)
    assert not after["files"].keys() & {f"previews/{aid}-480.jpg" for aid in targets}
    if case == "video":
        assert not after["files"].keys() & {
            "media/derived/video/a/scene.json",
            "video-jobs/acceptance-video/speech.json",
        }
    if case == "shared":
        assert after["rows"]["face_clusters"][0]["member_count"] == 1
        assert json.loads(after["rows"]["facts"][0]["evidence_ids_json"]) == ["ob"]
        assert after["rows"]["family_person_portraits"][0]["portrait_text"] == "人工维护：喜欢阅读"
        assert any(event["id"] == "event" for event in after["rows"]["events"])
        assert after["rows"]["family_relationships"] == before["rows"]["family_relationships"]
        assert not any(row["id"] == "old-graph" for row in after["rows"]["memory_graph_nodes"])
        with db.connect() as c:
            assert (
                c.execute(
                    "SELECT count(*) FROM readable_person_portrait_revisions WHERE id='locked'"
                ).fetchone()[0]
                == 0
            )
        assert "金色纸船" not in json.dumps(model.calls, ensure_ascii=False)
        assert (
            after["files"][str(paths["a"].relative_to(root))]
            == before["files"][str(paths["a"].relative_to(root))]
        )
    evidence = dict(
        before=before,
        after=after,
        metrics_seconds=metrics,
        plan=plan,
        reconciliation=reconciled,
        cleanup=cleanup,
        rebuild=status,
        model=dict(id=model.model_id, inputs=model.calls, outputs=model_outputs),
        generated_mp4=bool(case == "video" and ffmpeg),
        peak_process_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    (root / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, default=str, indent=2))
    index.close()


@pytest.mark.parametrize("fault", ["directory_lock", "file_permission"])
def test_cleanup_api_recovers_from_external_failure(isolated_app, tmp_path, monkeypatch, fault):
    container = isolated_app.state.container
    db, index = container.database, container.vector_index
    assert db.path.resolve().parent == tmp_path.resolve() == index.path.resolve().parent
    integrity(db)
    observation(db, asset(db), "oa")
    path = managed(db, "a")
    owner = QdrantVectorIndex(index.path)
    scope = {"scope_id": "s"}
    now = time.monotonic()
    monkeypatch.setattr(qdrant_index, "monotonic", lambda: now)
    with TestClient(isolated_app) as live:
        response = live.post("/api/v1/assets/a/deletion", json=scope)
        assert response.status_code == 202
        url = "/api/v1/media-deletions/" + response.json()["deletion_id"]
        assert live.post(url + "/plan", json=scope).status_code == 200
        assert live.post(url + "/reconcile", json=scope).json()["status"] == "reconciled"
        try:
            if fault == "directory_lock":
                index.close()
                assert owner.status().available
            else:
                path.parent.chmod(0o500)
            result = live.post(url + "/cleanup", json=scope).json()
            assert result["sqlite_done"] and result["status"] == "failed", result
            assert not result["validation"]["storage_cleaned"]
            assert live.get(url + "/rebuild", params=scope).json()["state"] != "completed"
        finally:
            if fault == "directory_lock":
                owner.close()
            else:
                path.parent.chmod(0o700)
    now += 10
    with TestClient(isolated_app) as live:
        result = live.post(url + "/cleanup", json=scope).json()
        assert result["validation"]["storage_cleaned"], result
        assert not path.exists()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            final = live.get(url + "/rebuild", params=scope).json()
            if final["state"] == "completed":
                break
            time.sleep(0.025)
        assert final["state"] == "completed", final
    integrity(db)
    index.close()


def test_cross_scope_dependency_blocks_final_completion_through_api(isolated_app, tmp_path):
    container = isolated_app.state.container
    db, index = container.database, container.vector_index
    assert db.path.parent.resolve() == tmp_path.resolve()
    integrity(db)
    shared(db)
    path = managed(db, "a")
    managed(db, "b")
    with db.connect() as c, c:
        insert(
            c,
            "facts",
            id="foreign-fact",
            scope_id="other",
            subject="妈妈",
            predicate="喜欢",
            object="金色纸船",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    try:
        with TestClient(isolated_app) as live:
            scope = {"scope_id": "s"}
            did = live.post("/api/v1/assets/a/deletion", json=scope).json()["deletion_id"]
            url = "/api/v1/media-deletions/" + did
            assert live.post(url + "/plan", json=scope).status_code == 200
            assert live.post(url + "/reconcile", json=scope).json()["status"] == "reconciled"
            result = live.post(url + "/cleanup", json=scope).json()
            assert result["status"] == "blocked" and not result["sqlite_done"], result
            assert live.post(url + "/rebuild", json=scope).status_code == 409
            assert live.get(url, params=scope).json()["lifecycle_state"] != "completed"
            assert path.exists()
            assert live.get("/api/v1/assets/a/file").status_code == 404
            assert live.get("/api/v1/assets/b/file").status_code == 200
            with db.connect() as c:
                assert (
                    c.execute("SELECT evidence_ids_json FROM facts WHERE id='foreign-fact'").fetchone()[0]
                    == '["oa","ob"]'
                )
            (tmp_path / "blocked.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        integrity(db)
    finally:
        index.close()


@pytest.mark.parametrize("kind", [MediaType.IMAGE, MediaType.VIDEO])
def test_library_delete_runs_after_request_without_browser(isolated_app, kind):
    container = isolated_app.state.container
    isolated_app.state.container = dataclasses.replace(container, chat_model=Model())
    db = container.database
    observation(db, asset(db, "library-target", kind=kind), "library-observation")
    if kind == MediaType.VIDEO:
        observation(db, asset(db, "library-frame", parent="library-target"), "library-frame-observation")
    observation(db, asset(db, "healthy"), "healthy-observation")
    observation(db, asset(db, "manual", scope="manual-scope"), "manual-observation")
    path = managed(db, "library-target")
    Image.new("RGB", (32, 32), "red").save(path, format="JPEG")
    api = "/api/v1"
    # Submit without lifespan, as if the service stopped right after committing isolation.
    client = TestClient(isolated_app)
    request = {"scope_id": "s", "library": True}
    first = client.post(f"{api}/assets/library-target/deletion", json=request)
    assert first.status_code == 202, first.text
    did = first.json()["deletion_id"]
    assert client.post(f"{api}/assets/library-target/deletion", json=request).json()["deletion_id"] == did
    assert client.get(f"{api}/assets/library-target").status_code == 404
    assert path.exists()
    assert client.post(f"{api}/assets/manual/deletion", json={"scope_id": "manual-scope"}).status_code == 202
    assert client.get(f"{api}/media-deletions", params={"scope_id": "other"}).json() == []
    with TestClient(isolated_app) as running:
        for _ in range(100):
            tasks = running.get(f"{api}/media-deletions").json()
            assert len(tasks) == 1
            if tasks[0]["lifecycle_state"] == "completed":
                break
            time.sleep(0.1)
        assert tasks[0]["lifecycle_state"] == "completed", tasks
        assert running.get(f"{api}/assets/healthy").status_code == 200
    assert not path.exists()
    with db.connect() as c:
        assert c.execute("SELECT id FROM assets WHERE id='manual'").fetchone()
        assert not c.execute(
            "SELECT id FROM assets WHERE id IN ('library-target','library-frame')"
        ).fetchone()
    integrity(db)
