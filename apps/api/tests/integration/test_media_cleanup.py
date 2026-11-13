"""3C uses isolated temporary stores only. No model rebuild or legacy cleanup."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_media_deletion import asset, observation
from test_media_deletion import db as db
from test_media_deletion_plans import insert, request
from test_media_reconciliation import shared

from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion, MediaIsolated
from sentrix.domain.media.models import MediaType
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository
from sentrix.infrastructure.persistence.repositories.deletions import SqliteDeletionRepository
from sentrix.infrastructure.persistence.repositories.reconciliations import SqliteReconciliationRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex


@pytest.fixture
def index(db, tmp_path):
    adapter = QdrantVectorIndex(tmp_path / "qdrant", database=db)
    yield adapter
    adapter.close()


def managed(db, aid):
    with db.connect() as c, c:
        a = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        path = db.path.parent / "media" / f"{aid}_{a['file_name']}"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"owned source")
        c.execute("UPDATE assets SET path=? WHERE id=?", (str(path), aid))
    return path


def prepare(db, aid="a"):
    did = request(db, aid)
    SqliteReconciliationRepository(db).execute(did, "s")
    return did


def run(db, index, did):
    result = SqliteCleanupRepository(db, index=index).execute(did, "s")
    assert result["status"] in {"storage_cleaned", "storage_cleaned_with_retained"}, result["error"]
    assert result["validation"]["storage_cleaned"]
    assert result["total_deletion_complete"] is False
    return result


def test_single_image_full_cleanup_and_permanent_denial(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    preview = db.path.parent / "previews" / "a-480.jpg"
    preview.parent.mkdir()
    preview.write_bytes(b"preview")
    records = [
        VectorRecord(
            scope_id="s", space="visual", source_type="asset", source_id="a", model_name=m, vector=(1.0, 0.0)
        )
        for m in ["m1", "m2"]
    ]
    for record in records:
        SqliteVectorRepository(db).upsert(record)
    index.upsert(records)
    did = prepare(db)
    result = run(db, index, did)
    assert result["status"] == "storage_cleaned"
    assert not path.exists() and not preview.exists()
    with db.connect() as c:
        for table in [
            "assets",
            "observations",
            "memory_vectors",
            "observation_search_terms",
            "observation_search_fts",
        ]:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM media_tombstones").fetchone()[0] == 2
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert index.count(space="visual") == 0
    assert SqliteDeletionRepository(db).request("a", "s")["deletion_id"] == did
    assert SqliteCleanupRepository(db, index=index).execute(did, "s")["status"] == "storage_cleaned"
    with pytest.raises(MediaIsolated):
        asset(db, "a")
    with pytest.raises(MediaIsolated):
        index.upsert(records)


def test_shared_identity_fact_event_and_healthy_search_survive(db, index):
    shared(db)
    paths = [managed(db, aid) for aid in ["a", "b"]]
    index.upsert(
        [
            VectorRecord(
                scope_id="s",
                space="visual",
                source_type="asset",
                source_id=aid,
                model_name="m",
                vector=(1.0, 0.0),
            )
            for aid in ["a", "b"]
        ]
    )
    did = prepare(db)
    before = {}
    with db.connect() as c:
        for table in ["entities", "face_clusters", "facts", "relationships", "media_evidence_revocations"]:
            before[table] = [tuple(r) for r in c.execute(f"SELECT * FROM {table}")]
    run(db, index, did)
    with db.connect() as c:
        for table, rows in before.items():
            assert [tuple(r) for r in c.execute(f"SELECT * FROM {table}")] == rows
        assert [r[0] for r in c.execute("SELECT id FROM face_instances")] == ["fb"]
        assert c.execute("SELECT 1 FROM readable_events WHERE id='event'").fetchone()
        assert json.loads(c.execute("SELECT evidence_ids_json FROM facts WHERE id='fact'").fetchone()[0]) == [
            "ob"
        ]
    assert not paths[0].exists() and paths[1].exists()
    assert [hit.source_id for hit in index.search(space="visual", vector=[1.0, 0.0], limit=10)] == ["b"]


def test_video_multilevel_frames_and_exclusive_scene(db, index):
    root = asset(db, "v", kind=MediaType.VIDEO)
    first = asset(db, "frame", parent="v")
    nested = asset(db, "nested", parent="frame")
    observation(db, root, "ov")
    observation(db, first, "of")
    observation(db, nested, "on")
    paths = [managed(db, aid) for aid in ["v", "frame", "nested"]]
    cache = db.path.parent / "media/derived/video/v/scene.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{}")
    with db.connect() as c, c:
        insert(
            c,
            "events",
            id="scene",
            scope_id="s",
            source_asset_id="v",
            source_type="video_scene",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(c, "event_observations", event_id="scene", observation_id="of", created_at="2026-11-10")
        insert(
            c,
            "video_jobs",
            job_id="job",
            asset_id="v",
            video_hash="hash",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(c, "video_job_stages", job_id="job", stage="EXTRACT", status="COMPLETED")
    artifact = db.path.parent / "video-jobs/job/speech.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}")
    did = prepare(db, "v")
    run(db, index, did)
    assert all(not path.exists() for path in [*paths, cache, artifact])
    with db.connect() as c:
        for table in ["assets", "observations", "events", "video_jobs", "video_job_stages"]:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert c.execute("SELECT 1 FROM media_tombstones WHERE kind='event' AND object_id='scene'").fetchone()


def test_shared_file_other_scope_is_retained(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    asset(db, "b", scope="other")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    result = run(db, index, prepare(db))
    assert path.exists() and result["status"] == "storage_cleaned_with_retained"
    assert any(r["status"] == "retained_shared" for r in result["resources"])
    with db.connect() as c:
        assert c.execute("SELECT 1 FROM readable_assets WHERE id='b'").fetchone()


@pytest.mark.parametrize("kind", ["file", "qdrant"])
def test_external_failure_after_sqlite_commit_recovers_after_restart(db, index, kind, monkeypatch):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    record = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    index.upsert([record])
    did = prepare(db)

    def fail(*args, **kwargs):
        raise OSError("injected external failure")

    repo = (
        SqliteCleanupRepository(db, index=index, remove_file=fail)
        if kind == "file"
        else SqliteCleanupRepository(db, index=index)
    )
    original = index.delete_exact
    if kind == "qdrant":
        monkeypatch.setattr(index, "delete_exact", fail)
    result = repo.execute(did, "s")
    assert result["sqlite_done"] == 1 and result["status"] == "failed"
    assert any(r["kind"] == kind and r["status"] == "failed" for r in result["resources"])
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    monkeypatch.setattr(index, "delete_exact", original)
    reopened = Database(db.path, db.migrations_dir)
    run(reopened, index, did)
    assert not path.exists() and index.count(space="visual") == 0


def test_qdrant_unavailable_does_not_rollback_sqlite(db, index, monkeypatch):
    a = asset(db)
    observation(db, a, "oa")
    managed(db, "a")
    did = prepare(db)
    original = index.deletion_inventory

    def fail():
        raise OSError("offline")

    monkeypatch.setattr(index, "deletion_inventory", fail)
    result = SqliteCleanupRepository(db, index=index).execute(did, "s")
    assert result["sqlite_done"] == 1 and result["status"] == "failed"
    monkeypatch.setattr(index, "deletion_inventory", original)
    run(db, index, did)


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_sqlite_crash_boundary_and_resume(db, index, boundary):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    did = prepare(db)

    def fail(step, where):
        if step == "sqlite" and where == boundary:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        SqliteCleanupRepository(db, index=index, fault=fail).execute(did, "s")
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == (
            1 if boundary == "before_commit" else 0
        )
    assert path.exists()
    run(Database(db.path, db.migrations_dir), index, did)


@pytest.mark.parametrize("mode", ["outside", "leaf_symlink", "directory_symlink", "dataset"])
def test_paths_are_not_guessed_and_external_files_survive(db, index, tmp_path, mode):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    external = tmp_path / "external.jpg"
    external.write_bytes(b"do not delete")
    if mode == "outside":
        path = external
    elif mode == "leaf_symlink":
        path.unlink()
        path.symlink_to(external)
    elif mode == "directory_symlink":
        directory = tmp_path / "media/derived/video/a"
        directory.parent.mkdir(parents=True)
        directory.symlink_to(tmp_path, target_is_directory=True)
        path = directory / "external.jpg"
    else:
        path = tmp_path / "media/datasets/a_image.jpg"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"dataset")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id=?", (str(path), "a"))
    result = SqliteCleanupRepository(db, index=index).execute(prepare(db), "s")
    assert external.read_bytes() == b"do not delete"
    if mode == "outside":
        assert result["status"] == "storage_cleaned_with_retained"
    else:
        assert result["status"] == "blocked" and not result["validation"]["storage_cleaned"]
    if mode == "dataset":
        assert path.read_bytes() == b"dataset"


def test_concurrent_cleanup_and_no_duplicate_checkpoints(db, index):
    a = asset(db)
    observation(db, a, "oa")
    managed(db, "a")
    did = prepare(db)

    def execute(_):
        return SqliteCleanupRepository(Database(db.path, db.migrations_dir), index=index).execute(did, "s")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r["status"] == "storage_cleaned" for r in pool.map(execute, range(2)))
    with db.connect() as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            c.execute("SELECT count(*) FROM media_cleanup_steps WHERE step='sqlite:assets'").fetchone()[0]
            == 1
        )


def test_cross_scope_evidence_blocks_sqlite_and_preserves_sources(db, index):
    shared(db)
    path = managed(db, "a")
    with db.connect() as c, c:
        insert(
            c,
            "facts",
            id="cross",
            scope_id="other",
            subject="人",
            predicate="活动",
            object="游泳",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    result = SqliteCleanupRepository(db, index=index).execute(prepare(db), "s")
    assert result["status"] == "blocked" and not result["sqlite_done"] and path.exists()
    with db.connect() as c:
        assert c.execute("SELECT 1 FROM assets WHERE id='a'").fetchone()
        assert c.execute(
            "SELECT 1 FROM media_evidence_revocations WHERE object_table='facts' AND evidence_id='oa'"
        ).fetchone()


def test_requires_completed_3b_and_correct_scope(db, index):
    asset(db)
    managed(db, "a")
    did = request(db)
    with pytest.raises(InvalidDeletion):
        SqliteCleanupRepository(db, index=index).execute(did, "s")
    with pytest.raises(DeletionNotFound):
        SqliteCleanupRepository(db, index=index).execute(did, "other")


def test_upgrade_live_isolated_ledgers_without_fk_disable(db, tmp_path, index):
    old = tmp_path / "old-migrations"
    old.mkdir()
    for source in db.migrations_dir.glob("*.sql"):
        if source.name < "0031":
            shutil.copyfile(source, old / source.name)
    prior = Database(tmp_path / "prior.db", old)
    prior.migrate()
    shared(prior)
    did = prepare(prior)
    names = [
        "media_deletions",
        "media_deletion_members",
        "media_deletion_plans",
        "media_reconciliations",
        "media_reconciliation_steps",
        "media_evidence_revocations",
        "media_tombstones",
    ]
    with prior.connect() as c:
        before = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in names}
    upgraded = Database(prior.path, db.migrations_dir)
    assert upgraded.migrate() == ["0031", "0032", "0033"]
    with upgraded.connect() as c:
        after = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in names}
        assert all(row[-1] is None for row in after["media_deletions"])
        after["media_deletions"] = [row[:-1] for row in after["media_deletions"]]
        assert after == before
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert not any(
            r["table"] == "assets"
            for t in ["media_deletions", "media_deletion_members"]
            for r in c.execute(f"PRAGMA foreign_key_list({t})")
        )
    assert SqliteDeletionRepository(upgraded).get(did, "s")["status"] == "isolated"


def test_text_vectors_fts_multiple_source_types_and_model_versions(db, index):
    a = asset(db)
    o = observation(db, a, "oa")
    managed(db, "a")
    from dataclasses import replace

    from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
    from sentrix.infrastructure.persistence.repositories.assets import (
        SqliteAssetRepository,
        SqliteObservationRepository,
    )
    from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
    from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository

    SqliteLexicalIndex(
        db,
        SqliteObservationRepository(db),
        assets=SqliteAssetRepository(db),
        places=SqliteAssetPlaceRepository(db),
    ).refresh_from_observation(replace(o, caption="红色汽车", objects=("汽车",)))
    for model in ["m1", "m2"]:
        record = VectorRecord(
            scope_id="s",
            space="semantic",
            source_type="observation",
            source_id="oa",
            model_name=model,
            vector=(1.0, 0.0),
            metadata={"observation_id": "oa"},
        )
        SqliteTextVectorRepository(db).upsert(record)
        index.upsert([record])
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM observation_search_fts").fetchone()[0] > 0
        assert c.execute("SELECT count(*) FROM text_vectors").fetchone()[0] == 2
    run(db, index, prepare(db))
    with db.connect() as c:
        for table in ["observation_search_fts", "observation_search_terms", "text_vectors"]:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert index.count(space="semantic") == 0


def test_last_owner_can_clean_previously_shared_managed_file(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    b = asset(db, "b")
    observation(db, b, "ob")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    run(db, index, prepare(db, "a"))
    assert path.exists()
    run(db, index, prepare(db, "b"))
    assert not path.exists()


def test_http_cleanup_preserves_healthy_import_and_search(client):
    import io

    from PIL import Image

    def upload(color):
        stream = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(stream, format="JPEG")
        response = client.post(
            "/api/v1/media/import",
            data={"scope_id": "s"},
            files={"file": (color + ".jpg", stream.getvalue(), "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        return response.json()["asset_id"]

    aid = upload("red")
    healthy = upload("blue")
    did = client.post(f"/api/v1/assets/{aid}/deletion", json={"scope_id": "s"}).json()["deletion_id"]
    assert client.post(f"/api/v1/media-deletions/{did}/reconcile", json={"scope_id": "s"}).status_code == 200
    endpoint = f"/api/v1/media-deletions/{did}/cleanup"
    response = client.post(endpoint, json={"scope_id": "s"})
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["storage_cleaned"], response.text
    assert client.get(f"/api/v1/assets/{aid}/file").status_code == 404
    assert client.get(f"/api/v1/assets/{healthy}/file").status_code == 200
    assert client.get(endpoint, params={"scope_id": "wrong"}).status_code == 404
    assert (
        client.post(f"/api/v1/media-deletions/{did}/reconcile", json={"scope_id": "s"}).json()["status"]
        == "reconciled"
    )
    new = upload("green")
    assert client.get(f"/api/v1/assets/{new}/file").status_code == 200
    search = client.get("/api/v1/search", params={"q": "测试图", "scope_id": "s"})
    assert search.status_code == 200
    assert all(item["asset"]["id"] != aid for item in search.json()["items"])


@pytest.mark.parametrize("kind", ["file", "qdrant"])
def test_crash_after_external_action_is_idempotently_retried(db, index, kind):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    index.upsert(
        [
            VectorRecord(
                scope_id="s",
                space="visual",
                source_type="asset",
                source_id="a",
                model_name="m",
                vector=(1.0, 0.0),
            )
        ]
    )
    did = prepare(db)

    def fail(step, where):
        if step == kind and where == "after_external_action":
            raise RuntimeError("lost checkpoint")

    result = SqliteCleanupRepository(db, index=index, fault=fail).execute(did, "s")
    assert result["status"] == "failed" and result["sqlite_done"]
    run(Database(db.path, db.migrations_dir), index, did)
    assert not path.exists() and index.count(space="visual") == 0


def test_unknown_foreign_key_reference_blocks_without_cascade(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    with db.connect() as c, c:
        c.execute(
            "CREATE TABLE custom_provenance "
            "(id TEXT PRIMARY KEY, observation_id TEXT REFERENCES observations(id))"
        )
        c.execute("INSERT INTO custom_provenance VALUES ('custom','oa')")
    result = SqliteCleanupRepository(db, index=index).execute(prepare(db), "s")
    assert result["status"] == "blocked" and not result["sqlite_done"]
    assert "retained_foreign_key_reference" in result["error"] and path.exists()


def test_pending_path_cannot_be_adopted_and_old_sources_cannot_write_back(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    did = prepare(db)

    def fail(step, where):
        if step == "sqlite" and where == "after_commit":
            raise RuntimeError("stop before files")

    with pytest.raises(RuntimeError):
        SqliteCleanupRepository(db, index=index, fault=fail).execute(did, "s")
    b = asset(db, "b")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    with pytest.raises(MediaIsolated):
        observation(db, a, "late-observation")
    from sentrix.infrastructure.persistence.media_barrier import publish_media_file

    with pytest.raises(MediaIsolated):
        publish_media_file(db, "a", path.with_name("temporary-late.jpg"), path, allow_unregistered=True)
    assert path.read_bytes() == b"owned source"
    run(db, index, did)
    with db.connect() as c:
        assert c.execute("SELECT 1 FROM assets WHERE id=?", (b.id,)).fetchone()


def test_replaced_managed_file_is_not_deleted_on_resume(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    did = prepare(db)

    def fail(*args):
        raise PermissionError("denied")

    result = SqliteCleanupRepository(db, index=index, remove_file=fail).execute(did, "s")
    assert result["status"] == "failed"
    path.write_bytes(b"replacement from another source")
    result = SqliteCleanupRepository(db, index=index).execute(did, "s")
    assert result["status"] == "blocked" and path.read_bytes() == b"replacement from another source"


def test_two_concurrent_deletions_of_shared_file_recheck_current_owners(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    b = asset(db, "b")
    observation(db, b, "ob")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    ids = [prepare(db, "a"), prepare(db, "b")]

    def execute(did):
        return SqliteCleanupRepository(Database(db.path, db.migrations_dir), index=index).execute(did, "s")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, ids))
    assert all(r["validation"]["storage_cleaned"] for r in results), results
    assert not path.exists()


def test_fresh_question_retrieves_and_delivers_only_healthy_media_after_cleanup(db, index):
    from datetime import UTC, datetime

    from test_run_turn import ScriptedModel, StubToolbox, final_call, tool_call

    from sentrix.application.assistant.run_turn import RunTurn, TurnRequest
    from sentrix.application.assistant.tools import ToolOutcome
    from sentrix.domain.assistant.models import Attachment
    from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
    from sentrix.infrastructure.persistence.repositories.conversations import SqliteConversationRepository
    from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository

    shared(db)
    managed(db, "a")
    managed(db, "b")
    index.upsert(
        [
            VectorRecord(
                scope_id="s",
                space="visual",
                source_type="asset",
                source_id=aid,
                model_name="m",
                vector=(1.0, 0.0),
            )
            for aid in ["a", "b"]
        ]
    )
    run(db, index, prepare(db))

    class LiveToolbox(StubToolbox):
        def execute(self, name, arguments, *, scope_id, now, question=""):
            hits = index.search(space="visual", vector=[1.0, 0.0], limit=10)
            assert [hit.source_id for hit in hits] == ["b"]
            return ToolOutcome(
                observation={"result_set_id": "rs", "total": 1, "asset_ids": ["b"]},
                model_visible={
                    "result_set_id": "rs",
                    "total": 1,
                    "preview": [{"handle": "photo_1", "file_name": "b.jpg"}],
                },
            )

        def deliver(self, result_set_id, handles, *, scope_id, fallback_result_set_ids=()):
            assert SqliteAssetRepository(db).get("b") is not None
            assert SqliteAssetRepository(db).get("a") is None
            return ToolOutcome(
                observation={"result_set_id": "rs", "delivered": 1, "rejected": []},
                model_visible={"result_set_id": "rs", "delivered": 1, "rejected": []},
                attachments=(Attachment(handle="photo_1", asset_id="b", file_name="b.jpg"),),
            )

    conversations = SqliteConversationRepository(db)
    turns = SqliteTurnRepository(db)
    conversation = conversations.create(scope_id="s", title="新对话", created_at=datetime.now(UTC))
    model = ScriptedModel(
        [tool_call("search_memories", "search", query="照片"), final_call("这是保留的照片。", "photo_1")]
    )
    use_case = RunTurn(model=model, conversations=conversations, turns=turns, toolbox=LiveToolbox())
    turn = use_case.start(TurnRequest(conversation_id=conversation.id, question="给我看看照片"))
    result = use_case.run(turn.id)
    assert result.answer == "这是保留的照片。"
    assert [a.asset_id for a in result.attachments] == ["b"]


def test_cancelled_import_history_survives_owned_file_cleanup_and_external_source_is_protected(
    db, index, tmp_path
):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    external = tmp_path / "dataset-source.jpg"
    external.write_bytes(b"external dataset")
    with db.connect() as c, c:
        for n, source in enumerate([path, external]):
            insert(
                c,
                "import_jobs",
                id=f"import{n}",
                asset_id="a",
                scope_id="s",
                receive_seq=n,
                file_name="a.jpg",
                media_type="image",
                content_sha256="hash",
                size_bytes=10,
                status="complete",
                idempotency_key=f"key{n}",
                accepted_at="2026-11-10",
                updated_at="2026-11-10",
                original_path=str(source),
            )
    result = run(db, index, prepare(db))
    assert not path.exists() and external.read_bytes() == b"external dataset"
    assert result["status"] == "storage_cleaned_with_retained"
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM import_jobs").fetchone()[0] == 2
        assert all(r[0] == "cancelled" for r in c.execute("SELECT status FROM import_jobs"))
