"""Isolation uses real SQLite/Qdrant and deterministic model fakes; no resource deletion."""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion, MediaIsolated
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import ObservationSemantics
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.deletions import SqliteDeletionRepository


@pytest.fixture
def db(tmp_path):
    database = Database(
        tmp_path / "isolation.db",
        Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations",
    )
    database.migrate()
    return database


def asset(db, name="a", *, kind=MediaType.IMAGE, parent=None, scope="s"):
    now = datetime.now(UTC)
    path = db.path.parent / f"{name}.jpg"
    path.write_bytes(b"original retained")
    record = Asset(
        id=name,
        scope_id=scope,
        file_name=path.name,
        media_type=kind,
        path=str(path),
        mime_type="image/jpeg",
        size_bytes=17,
        status=AssetStatus.PROCESSED,
        content_sha256=name,
        captured_at=now,
        captured_location=None,
        created_at=now,
        updated_at=now,
    )
    SqliteAssetRepository(db).add(record)
    if parent:
        with db.connect() as c, c:
            c.execute(
                "UPDATE assets SET parent_asset_id=?, derived_kind='video_keyframe' WHERE id=?",
                (parent, name),
            )
    return record


def observation(db, a, name="o"):
    now = datetime.now(UTC)
    o = Observation(
        id=name,
        scope_id=a.scope_id,
        asset_id=a.id,
        source_type="image",
        captured_at=now,
        confidence=1.0,
        created_at=now,
        updated_at=now,
    )
    SqliteObservationRepository(db).add(o)
    return o


def test_request_is_atomic_durable_and_idempotent(db):
    a = asset(db)
    repo = SqliteDeletionRepository(db)
    first = repo.request(a.id, "s", "key")
    assert first == repo.request(a.id, "s", "key") == repo.request(a.id, "s", "another")
    assert first["status"] == "isolated" and first["generation"] == 1
    assert first["retry_count"] == 0 and first["error"] == ""
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_deletions").fetchone()[0] == 1
        row = c.execute("SELECT status,deletion_state,deletion_generation FROM assets").fetchone()
        assert tuple(row) == ("processed", "isolated", 1)
    assert Path(a.path).read_bytes() == b"original retained"
    restarted = Database(db.path, db.migrations_dir)
    assert restarted.migrate() == []
    assert SqliteDeletionRepository(restarted).get(first["deletion_id"], "s") == first
    assert SqliteAssetRepository(restarted).get(a.id) is None


def test_recursive_video_closure_and_unrelated_asset(db):
    root = asset(db, "v", kind=MediaType.VIDEO)
    asset(db, "frame", parent="v")
    asset(db, "nested", parent="frame")
    other = asset(db, "other")
    result = SqliteDeletionRepository(db).request(root.id, "s")
    assert set(result["asset_ids"]) == {"v", "frame", "nested"}
    assert SqliteAssetRepository(db).get(other.id) is not None
    observation(db, other)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 4


def test_scope_and_derived_requests_rejected_without_side_effect(db):
    asset(db, "v", kind=MediaType.VIDEO)
    asset(db, "frame", parent="v")
    repo = SqliteDeletionRepository(db)
    with pytest.raises(DeletionNotFound):
        repo.request("v", "wrong")
    with pytest.raises(InvalidDeletion):
        repo.request("frame", "s")
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_deletions").fetchone()[0] == 0


def test_idempotency_key_cannot_target_a_different_asset(db):
    asset(db, "a")
    asset(db, "b")
    repo = SqliteDeletionRepository(db)
    repo.request("a", "s", "key")
    with pytest.raises(InvalidDeletion):
        repo.request("b", "s", "key")
    assert SqliteAssetRepository(db).get("b") is not None


def test_simultaneous_requests_create_one_task(db):
    asset(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(
            pool.map(
                lambda _: SqliteDeletionRepository(Database(db.path, db.migrations_dir)).request("a", "s")[
                    "deletion_id"
                ],
                range(2),
            )
        )
    assert ids[0] == ids[1]


def test_new_child_cannot_be_inserted_or_reparented_after_isolation(db):
    asset(db, "v", kind=MediaType.VIDEO)
    child = asset(db, "candidate")
    SqliteDeletionRepository(db).request("v", "s")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute(
            "INSERT INTO assets "
            "(id,scope_id,file_name,media_type,path,size_bytes,status,created_at,updated_at,parent_asset_id) "
            "VALUES ('new','s','f','image','f',0,'queued','2026-11-10','2026-11-10','v')"
        )
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE assets SET parent_asset_id='v' WHERE id=?", (child.id,))
    assert SqliteAssetRepository(db).get(child.id) is not None


def test_inference_finishing_after_deletion_cannot_enrich(db):
    a = asset(db)
    o = observation(db, a)
    entered, release = Event(), Event()

    class SlowObserver:
        def observe(self, _path):
            entered.set()
            assert release.wait(5)
            return ObservationSemantics(caption="must not commit")

    worker = ObserveAsset(observer=SlowObserver(), observations=SqliteObservationRepository(db))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.observe, a, o)
        assert entered.wait(5)
        SqliteDeletionRepository(db).request(a.id, "s")
        release.set()
        with pytest.raises(MediaIsolated):
            future.result()
    with db.connect() as c:
        assert c.execute("SELECT caption FROM observations WHERE id=?", (o.id,)).fetchone()[0] == ""


def test_isolation_blocks_new_observations_faces_vectors_and_status(db):
    a = asset(db)
    observation(db, a)
    SqliteDeletionRepository(db).request(a.id, "s")
    with pytest.raises(MediaIsolated):
        observation(db, a, "new")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute(
            "INSERT INTO face_instances(id,asset_id,observation_id,created_at) VALUES "
            "('face','a','o','2026-11-10')"
        )
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

    record = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    with pytest.raises(MediaIsolated):
        SqliteVectorRepository(db).upsert(record)
    with pytest.raises(MediaIsolated):
        SqliteAssetRepository(db).update_status("a", AssetStatus.FAILED)
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE assets SET deletion_state='active',deletion_generation=0 WHERE id='a'")


def test_shared_people_clusters_and_facts_are_not_modified(db):
    from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
    from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

    # Snapshot whole shared tables; isolation must not rewrite their records.
    a = asset(db)
    other = asset(db, "other")
    observation(db, a)
    observation(db, other, "other_o")
    with db.connect() as c, c:
        c.execute(
            "INSERT INTO entities(id,entity_type,canonical_name,created_at,updated_at) VALUES "
            "('person','person','妈妈','2026-11-10','2026-11-10')"
        )
        c.execute(
            "INSERT INTO face_clusters(id,entity_id,status,created_at,updated_at) VALUES "
            "('cluster','person','confirmed','2026-11-10','2026-11-10')"
        )
        c.execute(
            "INSERT INTO face_instances(id,asset_id,observation_id,cluster_id,created_at) "
            "VALUES ('f1','a','o','cluster','2026-11-10'),('f2','other','other_o','cluster','2026-11-10')"
        )
        c.execute(
            "INSERT INTO facts(id,scope_id,subject,predicate,object,evidence_ids_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("fact", "s", "妈妈", "活动", "游泳", '["o","other_o"]', "2026-11-10", "2026-11-10"),
        )
        before = {
            t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")]
            for t in ["entities", "face_clusters", "face_instances", "facts"]
        }
    SqliteDeletionRepository(db).request("a", "s")
    with db.connect() as c:
        after = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in before}
    assert before == after
    assert SqliteEntityRepository(db).get("person") is not None
    assert [f.id for f in SqliteFaceRepository(db).list_instances("cluster")] == ["f2"]


def test_qdrant_stale_points_hidden_and_upsert_blocked(db, tmp_path):
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    asset(db)
    index = QdrantVectorIndex(tmp_path / "qdrant", database=db)
    record = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    try:
        index.upsert([record])
        assert index.count(space="visual") == 1
        SqliteDeletionRepository(db).request("a", "s")
        assert index.search(space="visual", vector=[1.0, 0.0], limit=10) == []
        with pytest.raises(MediaIsolated):
            index.upsert([record])
        assert index.count(space="visual") == 1  # phase 2 never physically removes points
    finally:
        index.close()


def test_http_isolation_hides_media_and_search(client):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format="JPEG")
    r = client.post(
        "/api/v1/media/import",
        data={"scope_id": "s"},
        files={"file": ("x.jpg", stream.getvalue(), "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    aid = r.json()["asset_id"]
    c = client.app.state.container
    path = c.asset_repository.get(aid).path
    wrong = client.post(f"/api/v1/assets/{aid}/deletion", json={"scope_id": "wrong"})
    assert wrong.status_code == 404
    response = client.post(f"/api/v1/assets/{aid}/deletion", json={"scope_id": "s", "idempotency_key": "key"})
    assert response.status_code == 202, response.text
    assert response.json() == client.post(f"/api/v1/assets/{aid}/deletion", json={"scope_id": "s"}).json()
    for suffix in ["", "/file", "/preview", "/thumbnail"]:
        assert client.get(f"/api/v1/assets/{aid}{suffix}").status_code == 404
    result = client.get("/api/v1/search", params={"q": "红色", "scope_id": "s"})
    assert result.status_code == 200, result.text
    assert result.json()["items"] == []
    assert Path(path).is_file()
    task_id = response.json()["deletion_id"]
    assert client.get(f"/api/v1/media-deletions/{task_id}", params={"scope_id": "s"}).status_code == 200
    assert client.get(f"/api/v1/media-deletions/{task_id}", params={"scope_id": "wrong"}).status_code == 404


def test_cross_scope_lineage_failure_rolls_back_entire_request(db):
    asset(db, "v", kind=MediaType.VIDEO)
    asset(db, "frame", parent="v", scope="other")
    with pytest.raises(InvalidDeletion):
        SqliteDeletionRepository(db).request("v", "s")
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_deletions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM assets WHERE deletion_state='active'").fetchone()[0] == 2


def test_partial_face_commit_rolls_back_all_side_effects(db):
    asset(db)
    with pytest.raises(RuntimeError):  # noqa: SIM117 - separate nested rollback scopes
        with db.asset_commit("a"):
            with db.connect() as c, c:
                c.execute(
                    "INSERT INTO entities(id,entity_type,canonical_name,created_at,updated_at) "
                    "VALUES ('p','person','临时','2026-11-10','2026-11-10')"
                )
            raise RuntimeError("failure after pending entity creation")
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM entities").fetchone()[0] == 0
    SqliteDeletionRepository(db).request("a", "s")
    with pytest.raises(MediaIsolated), db.asset_commit("a"):
        pytest.fail("isolated asset must not enter commit body")


def test_stale_video_worker_and_restart_do_not_resume(db):
    from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository

    asset(db, "v", kind=MediaType.VIDEO)
    jobs = SqliteVideoRepository(db)
    jobs.create_job("v", "hash", "job")
    SqliteDeletionRepository(db).request("v", "s")
    assert jobs.get_job("job")["status"] == "CANCELLED"
    with pytest.raises(MediaIsolated):
        jobs.update_job("job", status="COMPLETED")
    assert jobs.list_jobs() == []
    reopened = SqliteVideoRepository(Database(db.path, db.migrations_dir))
    assert reopened.get_job("job")["status"] == "CANCELLED"


def test_storage_ownership_retains_quarantined_files(db):
    from sentrix.infrastructure.persistence.repositories.import_jobs import SqliteImportJobRepository

    a = asset(db)
    SqliteDeletionRepository(db).request(a.id, "s")
    assert a.path in SqliteImportJobRepository(db).referenced_paths()


def test_video_http_detail_and_scenes_are_hidden(client):
    db = client.app.state.container.database
    asset(db, "video", kind=MediaType.VIDEO)
    asset(db, "frame", parent="video")
    assert client.post("/api/v1/assets/frame/deletion", json={"scope_id": "s"}).status_code == 409
    assert client.post("/api/v1/assets/video/deletion", json={"scope_id": "s"}).status_code == 202
    for path in ["/videos/video", "/videos/video/scenes", "/assets/frame/file"]:
        assert client.get("/api/v1" + path).status_code == 404


def test_fulltext_rows_remain_but_no_longer_return_hits(db):
    from dataclasses import replace

    from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex

    a = asset(db)
    o = observation(db, a)
    from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository

    index = SqliteLexicalIndex(
        db,
        SqliteObservationRepository(db),
        assets=SqliteAssetRepository(db),
        places=SqliteAssetPlaceRepository(db),
    )
    index.refresh_from_observation(replace(o, caption="red apple"))
    assert index.search_fts("apple", scope_id="s", limit=10)
    SqliteDeletionRepository(db).request(a.id, "s")
    assert index.search_fts("apple", scope_id="s", limit=10) == []
    with pytest.raises(MediaIsolated):
        index.refresh_from_observation(o)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM observation_search_fts").fetchone()[0] > 0


def test_file_publication_is_blocked_but_original_and_pending_artifact_retained(db):
    from sentrix.infrastructure.persistence.media_barrier import publish_media_file

    a = asset(db)
    temporary = db.path.parent / "pending.jpg"
    target = db.path.parent / "derived.jpg"
    temporary.write_bytes(b"inference output")
    SqliteDeletionRepository(db).request(a.id, "s")
    with pytest.raises(MediaIsolated):
        publish_media_file(db, a.id, temporary, target)
    assert not target.exists()
    assert temporary.exists() and Path(a.path).exists()


def test_qdrant_publication_and_deletion_serialize(db, tmp_path, monkeypatch):
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    asset(db)
    entered, release = Event(), Event()
    index = QdrantVectorIndex(tmp_path / "q", database=db)

    def delayed(_records):
        entered.set()
        assert release.wait(5)
        return 1

    monkeypatch.setattr(index, "_upsert_active", delayed)
    record = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(index.upsert, [record])
        assert entered.wait(5)
        deletion = pool.submit(
            SqliteDeletionRepository(Database(db.path, db.migrations_dir)).request, "a", "s"
        )
        release.set()
        assert write.result() == 1
        assert deletion.result()["status"] == "isolated"
    with pytest.raises(MediaIsolated):
        index.upsert([record])


def test_shared_event_is_preserved_but_hidden_until_rebuilt(db):
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

    a = asset(db)
    b = asset(db, "b")
    observation(db, a)
    observation(db, b, "ob")
    with db.connect() as c, c:
        c.execute(
            "INSERT INTO events(id,scope_id,created_at,updated_at) VALUES ('e','s','2026-11-10','2026-11-10')"
        )
        c.execute("INSERT INTO event_observations VALUES ('e','o','2026-11-10'),('e','ob','2026-11-10')")
    SqliteDeletionRepository(db).request("a", "s")
    assert SqliteEventRepository(db).get("e") is None
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM events WHERE id='e'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM event_observations").fetchone()[0] == 2
    assert SqliteAssetRepository(db).get("b") is not None
