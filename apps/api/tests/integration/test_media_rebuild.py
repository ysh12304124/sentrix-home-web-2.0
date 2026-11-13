"""3D: scripted models, real SQLite/Qdrant and disposable media only."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_media_cleanup import index as index
from test_media_cleanup import managed, prepare, run
from test_media_deletion import asset, observation
from test_media_deletion import db as db
from test_media_reconciliation import shared, state

from sentrix.domain.assistant.models import ModelReply
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository
from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository
from sentrix.infrastructure.persistence.repositories.rebuilds import RebuildBlocked, SqliteRebuildRepository


class Model:
    model_id = "test-rebuild"

    def __init__(self):
        self.calls = []
        self.hook = None
        self.fail = False

    def complete(self, messages, tools):
        data = json.loads(messages[-1].content)
        self.calls.append(data)
        if self.hook:
            hook = self.hook
            self.hook = None
            hook()
        if self.fail:
            raise RuntimeError("model unavailable")
        return ModelReply(
            content=json.dumps(
                {
                    "summary": "当前有效证据的新记忆",
                    "title": "新的事件",
                    "activity": "活动",
                    "event_type": "日常",
                    "observation_ids": [o.get("id", o.get("observation_id")) for o in data["observations"]],
                }
            ),
            finish_reason="stop",
        )


class Embedder:
    model_id = "test-text"
    dimension = 2

    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [1.0, 0.0]


def rebuild(db, index, **kw):
    return SqliteRebuildRepository(
        db, index=index, model=kw.pop("model", Model()), embedder=kw.pop("embedder", Embedder()), **kw
    )


def cleaned(db, index, shared_data=True):
    if shared_data:
        shared(db)
        managed(db, "a")
        managed(db, "b")
    else:
        a = asset(db)
        observation(db, a, "oa")
        managed(db, "a")
    did = prepare(db)
    run(db, index, did)
    return did


def test_full_image_flow_and_shared_person_summary(db, index):
    did = cleaned(db, index)
    model = Model()
    repo = rebuild(db, index, model=model)
    assert repo.request(did, "s")["state"] == "rebuilding"
    assert repo.run_once("s")
    result = repo.get(did, "s")
    assert result["state"] == "completed", result
    assert len(model.calls) == 2
    assert all([o["id"] for o in call["observations"]] == ["ob"] for call in model.calls)
    assert "旧事件摘要" not in json.dumps(model.calls, ensure_ascii=False)
    with db.connect() as c:
        assert (
            c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0]
            == "当前有效证据的新记忆"
        )
        assert c.execute("SELECT count(*) FROM readable_memory_graph_builds").fetchone()[0] == 1
        assert c.execute("SELECT paused FROM media_scope_epochs WHERE scope_id='s'").fetchone()[0] == 0
        assert json.loads(c.execute("SELECT evidence_ids_json FROM facts WHERE id='fact'").fetchone()[0]) == [
            "ob"
        ]
        assert c.execute("SELECT count(*) FROM media_evidence_revocations").fetchone()[0] > 0
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert state(db, "facts", {"id": "fact"})["validity"] == "needs_review"
    calls = len(model.calls)
    assert repo.request(did, "s")["state"] == "completed"
    assert not repo.run_once("s") and len(model.calls) == calls


def test_no_evidence_no_model_and_no_false_summary(db, index):
    did = cleaned(db, index, False)
    model = Model()
    repo = rebuild(db, index, model=model)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert model.calls == []
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_model_failure_does_not_publish_and_retry_reuses_completed_objects(db, index):
    did = cleaned(db, index)
    model = Model()
    model.fail = True
    repo = rebuild(db, index, model=model, clock=lambda: 1000)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "failed"
    with db.connect() as c:
        assert c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0] == ""
    model.fail = False
    retry = rebuild(db, index, model=model, clock=lambda: 1010)
    retry.run_once("s")
    assert retry.get(did, "s")["state"] == "completed"


def test_new_observation_during_inference_fences_old_output_and_requeues(db, index):
    did = cleaned(db, index)
    model = Model()
    model.hook = lambda: observation(db, asset(db, "new"), "new-ob")
    repo = rebuild(db, index, model=model)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "rebuilding"
    assert repo.get(did, "s")["job"]["status"] == "queued"
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"


def test_second_deletion_during_model_blocks_publish_until_cleanup(db, index):
    did = cleaned(db, index)
    model = Model()
    next_ids = []

    def delete_more():
        next_ids.append(prepare(db, "b"))

    model.hook = delete_more
    repo = rebuild(db, index, model=model)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] != "completed"
    run(db, index, next_ids[0])
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert repo.get(next_ids[0], "s")["state"] == "completed"


def test_multiple_deletions_coalesce_model_work(db, index):
    shared(db)
    managed(db, "a")
    managed(db, "b")
    casset = asset(db, "c")
    observation(db, casset, "oc")
    managed(db, "c")
    ids = [prepare(db, "a"), prepare(db, "c")]
    for did in ids:
        run(db, index, did)
    model = Model()
    repo = rebuild(db, index, model=model)
    for did in ids:
        repo.request(did, "s")
    repo.run_once("s")
    assert all(repo.get(did, "s")["state"] == "completed" for did in ids)
    assert len(model.calls) == 2


def test_after_publication_restart_skips_model_calls(db, index):
    did = cleaned(db, index)
    model = Model()

    def crash(stage):
        if stage == "after_publish":
            raise RuntimeError("crash after atomic publication")

    repo = rebuild(db, index, model=model, fault=crash, clock=lambda: 1000)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "failed"
    calls = len(model.calls)
    retry = rebuild(Database(db.path, db.migrations_dir), index, model=model, clock=lambda: 1010)
    retry.run_once("s")
    assert retry.get(did, "s")["state"] == "completed"
    assert len(model.calls) == calls


def test_cleanup_blocker_never_completes(db, index):
    shared(db)
    path = managed(db, "a")
    managed(db, "b")
    did = prepare(db)

    def fail(*args):
        raise PermissionError("cannot delete")

    result = SqliteCleanupRepository(db, index=index, remove_file=fail).execute(did, "s")
    assert result["status"] == "failed"
    repo = rebuild(db, index)
    with pytest.raises(RebuildBlocked):
        repo.request(did, "s")
    assert repo.get(did, "s")["state"] != "completed" and path.exists()


def test_locked_dependent_portrait_stays_invalid_and_manual_family_preserved(db, index):
    shared(db)
    managed(db, "a")
    managed(db, "b")
    from test_media_deletion_plans import insert

    with db.connect() as c, c:
        insert(
            c,
            "person_portrait_revisions",
            id="locked",
            scope_id="s",
            person_id="p",
            revision=1,
            portrait_text="不可恢复旧画像",
            trigger_type="manual",
            prompt_version="v",
            status="user_locked",
            evidence_refs_json='[{"kind":"observation","id":"oa"}]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "family_person_portraits",
            id="human",
            scope_id="s",
            entity_id="p",
            portrait_text="独立人工描述",
            source="user_override",
            locked=1,
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    did = prepare(db)
    run(db, index, did)
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    with db.connect() as c:
        assert (
            c.execute("SELECT portrait_text FROM family_person_portraits WHERE id='human'").fetchone()[0]
            == "独立人工描述"
        )
        assert (
            c.execute(
                "SELECT count(*) FROM readable_person_portrait_revisions WHERE person_id='p'"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute("SELECT status FROM person_portrait_revisions WHERE id='locked'").fetchone()[0]
            == "user_locked"
        )


def test_video_flow_completes_without_reviving_frames(db, index):
    from sentrix.domain.media.models import MediaType

    root = asset(db, "v", kind=MediaType.VIDEO)
    frame = asset(db, "frame", parent="v")
    observation(db, root, "ov")
    observation(db, frame, "of")
    managed(db, "v")
    managed(db, "frame")
    did = prepare(db, "v")
    run(db, index, did)
    model = Model()
    repo = rebuild(db, index, model=model)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed" and not model.calls
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 0


def test_old_normal_model_finishes_after_new_rebuild_but_cannot_publish(db, index):
    from sentrix.application.memories.summarize_event import SummarizeEvent
    from sentrix.domain.media.deletion import MediaIsolated
    from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

    shared(db)
    managed(db, "a")
    managed(db, "b")
    old = Model()
    finished = []

    def cleanup_and_rebuild():
        did = prepare(db)
        run(db, index, did)
        new = rebuild(db, index)
        new.run_once("s")
        assert new.get(did, "s")["state"] == "completed"
        finished.append(did)

    old.hook = cleanup_and_rebuild
    summarizer = SummarizeEvent(
        events=SqliteEventRepository(db),
        observations=SqliteObservationRepository(db),
        assets=SqliteAssetRepository(db),
        model=old,
        generation_guard=db.model_generation,
    )
    with pytest.raises(MediaIsolated):
        summarizer.execute("event")
    assert finished
    with db.connect() as c:
        assert (
            c.execute("SELECT summary FROM readable_events WHERE id='event'").fetchone()[0]
            == "当前有效证据的新记忆"
        )
    # Fresh work is allowed; old revoked conclusions remain immutable even then.
    with db.model_generation("s"):
        SqliteEventRepository(db).apply_summary(
            "event", title="正常新任务", summary="正常更新", event_type="日常", activity="活动"
        )
    with db.connect() as c:
        assert c.execute("SELECT summary FROM readable_events WHERE id='event'").fetchone()[0] == "正常更新"


def test_concurrent_workers_claim_once(db, index):
    did = cleaned(db, index)
    model = Model()

    def tick(_):
        return rebuild(Database(db.path, db.migrations_dir), index, model=model).run_once("s")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tick, range(2)))
    assert sum(results) == 1 and len(model.calls) == 2
    assert rebuild(db, index).get(did, "s")["state"] == "completed"


def test_expired_worker_lease_recovers_with_fence(db, index):
    did = cleaned(db, index)
    first = rebuild(db, index, clock=lambda: 1000)
    claim = first._claim("s")
    assert claim
    second = rebuild(Database(db.path, db.migrations_dir), index, clock=lambda: 1100)
    second.run_once("s")
    assert second.get(did, "s")["state"] == "completed"
    from sentrix.infrastructure.persistence.repositories.rebuilds import StaleRebuild

    with pytest.raises(StaleRebuild), db.write_transaction() as c:
        first._alive(c, claim)


def test_graph_generation_conflict_reuses_models_and_embeddings(db, index):
    did = cleaned(db, index)
    model = Model()
    embedder = Embedder()
    fired = []

    def conflict(stage):
        if stage == "before_publish" and not fired:
            fired.append(True)
            with db.connect() as c, c:
                c.execute("UPDATE media_scope_epochs SET generation=generation+1 WHERE scope_id='s'")

    repo = rebuild(db, index, model=model, embedder=embedder, fault=conflict)
    repo.run_once("s")
    assert repo.get(did, "s")["job"]["status"] == "queued"
    models = len(model.calls)
    vectors = len(embedder.calls)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert len(model.calls) == models and len(embedder.calls) == vectors


def test_legally_shared_file_is_audited_at_final_completion(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    asset(db, "b", scope="other")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    did = prepare(db)
    result = run(db, index, did)
    assert result["status"] == "storage_cleaned_with_retained"
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed" and path.exists()


def test_old_event_qdrant_projection_cleanup_failure_is_retryable(db, index, monkeypatch):
    from sentrix.domain.retrieval.ports import VectorRecord

    shared(db)
    managed(db, "a")
    managed(db, "b")
    # Seed a legacy event mirror; the current public upsert correctly rejects event sources.
    index._upsert_active(
        [
            VectorRecord(
                scope_id="s",
                space="episodic",
                source_type="event",
                source_id="event",
                model_name="old",
                vector=(1.0, 0.0),
                metadata={"observation_id": "ob"},
            )
        ]
    )
    did = prepare(db)
    run(db, index, did)
    model = Model()
    original = index.delete_exact

    def fail(*args):
        raise OSError("index offline")

    monkeypatch.setattr(index, "delete_exact", fail)
    repo = rebuild(db, index, model=model, clock=lambda: 1000)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "failed"
    calls = len(model.calls)
    monkeypatch.setattr(index, "delete_exact", original)
    retry = rebuild(db, index, model=model, clock=lambda: 1010)
    retry.run_once("s")
    assert retry.get(did, "s")["state"] == "completed" and len(model.calls) == calls
    assert index.count(space="episodic") == 0


def test_historical_invalid_content_never_returns_to_prompt(db, index):
    from test_media_deletion_plans import insert

    from sentrix.infrastructure.persistence.repositories.conversations import SqliteConversationRepository

    shared(db)
    managed(db, "a")
    managed(db, "b")
    with db.connect() as c, c:
        insert(
            c,
            "agent_conversations",
            id="conv",
            scope_id="s",
            created_at="2000-01-01",
            updated_at="2000-01-01",
        )
        insert(
            c,
            "agent_conversation_messages",
            id="message",
            conversation_id="conv",
            scope_id="s",
            role="assistant",
            content="绝不可作为新证据的旧结论",
            created_at="2000-01-01",
        )
    did = prepare(db)
    run(db, index, did)
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert all(
        "绝不可" not in m.content for m in SqliteConversationRepository(db).history_for_prompt("conv", 10)
    )


def test_real_async_worker_recovers_persistent_queue(db, index):
    import threading

    from sentrix.infrastructure.media.rebuild_worker import MediaRebuildWorker

    did = cleaned(db, index)
    repo = rebuild(db, index)
    finished = threading.Event()
    original = repo.run_once

    def tick():
        value = original()
        if repo.get(did, "s")["state"] == "completed":
            finished.set()
        return value

    repo.run_once = tick
    worker = MediaRebuildWorker(lambda: repo)
    try:
        worker.start()
        assert finished.wait(5)
    finally:
        worker.stop()
    assert rebuild(Database(db.path, db.migrations_dir), index).get(did, "s")["total_deletion_complete"]


def test_http_api_to_lifespan_worker_end_to_end(client):
    import dataclasses
    import time

    from starlette.testclient import TestClient

    model = Model()
    client.app.state.container = dataclasses.replace(client.app.state.container, chat_model=model)
    container = client.app.state.container
    database = container.database
    shared(database)
    managed(database, "a")
    managed(database, "b")
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    adapter = container.vector_index
    assert isinstance(adapter, QdrantVectorIndex)
    did = prepare(database)
    run(database, adapter, did)
    response = client.post(f"/api/v1/media-deletions/{did}/rebuild", json={"scope_id": "s"})
    assert response.status_code == 202
    assert (
        client.get(f"/api/v1/media-deletions/{did}/rebuild", params={"scope_id": "other"}).status_code == 404
    )
    with TestClient(client.app) as live:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            final = live.get(f"/api/v1/media-deletions/{did}/rebuild", params={"scope_id": "s"}).json()
            if final["state"] == "completed":
                break
            time.sleep(0.02)
        assert final["state"] == "completed", final
        assert (
            live.get(f"/api/v1/media-deletions/{did}", params={"scope_id": "s"}).json()["lifecycle_state"]
            == "completed"
        )
        assert live.get("/api/v1/assets/a/file").status_code == 404
        assert live.get("/api/v1/assets/b/file").status_code == 200


def test_healthy_new_import_restores_normal_aggregate_updates(db, index):
    did = cleaned(db, index)
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    from types import SimpleNamespace

    from sentrix.bootstrap.import_wiring import _shared_memory_writable

    assert _shared_memory_writable(SimpleNamespace(database=db), "s")
    from sentrix.domain.media.ports import ObservationSemantics

    observation_repo = SqliteObservationRepository(db)
    with db.asset_commit("b"):
        updated = observation_repo.enrich(
            "ob", semantics=ObservationSemantics(caption="新的健康观察", confidence=0.8)
        )
    assert updated is not None
    assert _shared_memory_writable(SimpleNamespace(database=db), "s")
    with db.model_generation("s"), db.connect() as c, c:
        c.execute(
            "INSERT INTO facts(id,scope_id,subject,predicate,object,evidence_ids_json,created_at,updated_at) "
            "VALUES ('new-fact','s','人物','活动','散步','[\"ob\"]','2026-11-10','2026-11-10')"
        )
    with db.connect() as c:
        assert c.execute("SELECT 1 FROM readable_facts WHERE id='new-fact'").fetchone()


def test_lost_shared_file_owner_is_a_final_safety_block(db, index):
    a = asset(db)
    observation(db, a, "oa")
    path = managed(db, "a")
    asset(db, "b", scope="other")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id='b'", (str(path),))
    did = prepare(db)
    run(db, index, did)
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path='unrelated.jpg' WHERE id='b'")
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "blocked" and path.exists()


def test_new_generation_hides_old_profile_until_refresh(db, index):
    cleaned(db, index)
    repo = rebuild(db, index)
    repo.run_once("s")
    with db.connect() as c:
        assert c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0]
    observation(db, asset(db, "c"), "oc")
    with db.connect() as c:
        assert c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0] == ""
        c.execute("UPDATE media_rebuild_jobs SET next_attempt=0")
        c.commit()
    repo.run_once("s")
    with db.connect() as c:
        assert c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0]


def test_healthy_evidence_change_never_revalidates_an_old_locked_portrait(db, index):
    did = cleaned(db, index)
    repo = rebuild(db, index)
    repo.run_once("s")
    from sentrix.infrastructure.persistence.repositories.portraits import SqlitePortraitRepository

    portraits = SqlitePortraitRepository(db)
    current = portraits.get_active("p")
    assert current
    locked = portraits.set_lock(current.id, True)
    assert locked and locked.status == "user_locked"
    from sentrix.domain.media.ports import ObservationSemantics

    SqliteObservationRepository(db).enrich(
        "ob", semantics=ObservationSemantics(caption="完全不同的新语义", confidence=0.9)
    )
    with db.connect() as c, c:
        c.execute("UPDATE media_rebuild_jobs SET next_attempt=0 WHERE scope_id='s'")
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert portraits.get(current.id) is None
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM person_portrait_revisions WHERE id=?", (current.id,)).fetchone()[0]
            == "user_locked"
        )
        assert (
            c.execute(
                "SELECT action FROM media_rebuild_dispositions "
                "WHERE object_table='person_portrait_revisions' AND object_key=json_object('id',?)",
                (current.id,),
            ).fetchone()[0]
            == "kept_invalid_old_generation"
        )


def test_existing_content_verified_vectors_are_reused_across_spaces_and_retries(db, index):
    did = cleaned(db, index)
    from sentrix.domain.media.ports import ObservationSemantics

    SqliteObservationRepository(db).enrich(
        "ob", semantics=ObservationSemantics(caption="健康照片的当前描述", confidence=0.9)
    )
    embedder = Embedder()
    repo = rebuild(db, index, embedder=embedder)
    repo.request(did, "s")
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    assert len(embedder.calls) == 2
    with db.connect() as c, c:
        count = c.execute("SELECT count(*) FROM text_vectors").fetchone()[0]
        c.execute("UPDATE media_scope_epochs SET generation=generation+1 WHERE scope_id='s'")
        c.execute("UPDATE media_rebuild_jobs SET status='queued',next_attempt=0 WHERE scope_id='s'")
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed" and len(embedder.calls) == 2
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM text_vectors").fetchone()[0] == count


def test_3b_evidence_results_and_audit_are_immutable_during_rebuild(db, index):
    shared(db)
    managed(db, "a")
    managed(db, "b")
    from test_media_deletion_plans import insert

    with db.connect() as c, c:
        insert(
            c,
            "semantic_claims",
            id="claim",
            scope_id="s",
            person_id="p",
            dimension="activity",
            predicate="偏好",
            value_text="旧模型结论",
            support_count=17,
            confidence=0.97,
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    did = prepare(db)
    run(db, index, did)
    tables = ["facts", "relationships", "entity_properties", "semantic_claims", "media_evidence_revocations"]
    with db.connect() as c:
        before = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in tables}
    repo = rebuild(db, index)
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"
    with db.connect() as c:
        assert {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in tables} == before
        assert c.execute("SELECT 1 FROM readable_semantic_claims WHERE id='claim'").fetchone() is None
        assert (
            c.execute(
                "SELECT action FROM media_rebuild_dispositions "
                "WHERE object_table='semantic_claims' AND object_key=json_object('id','claim')"
            ).fetchone()[0]
            == "invalidated_no_reliable_evidence"
        )


def test_inference_started_during_pause_cannot_acquire_permission_after_resume(db, index):
    did = cleaned(db, index)
    from sentrix.domain.media.deletion import MediaIsolated
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

    with db.model_generation("s"):
        repo = rebuild(db, index)
        repo.run_once("s")
        assert repo.get(did, "s")["state"] == "completed"
        with pytest.raises(MediaIsolated):
            SqliteEventRepository(db).apply_summary(
                "event", title="迟到的旧结果", summary="不应覆盖新版本", event_type="", activity=""
            )


def test_normal_text_index_uses_text_store_and_fences_source_revision(db, index):
    did = cleaned(db, index)
    from types import SimpleNamespace

    from sentrix.bootstrap.import_wiring import _text_indexer
    from sentrix.domain.media.deletion import MediaIsolated
    from sentrix.domain.media.ports import ObservationSemantics
    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

    observations = SqliteObservationRepository(db)
    current = observations.enrich(
        "ob", semantics=ObservationSemantics(caption="有效的新观察", confidence=0.8)
    )
    assert current
    text = SqliteTextVectorRepository(db)
    visual = SqliteVectorRepository(db)
    indexer = _text_indexer(
        SimpleNamespace(
            database=db, text_embedder=Embedder(), text_vector_repository=text, vector_repository=visual
        )
    )
    assert (
        indexer(current, None) == 2
    )  # Raw source indexing remains available while aggregate models are paused.
    with db.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM text_vectors WHERE source_type='observation'").fetchone()[0] == 2
        )
        assert (
            c.execute("SELECT count(*) FROM memory_vectors WHERE source_type='observation'").fetchone()[0]
            == 0
        )
    observations.enrich("ob", semantics=ObservationSemantics(caption="修正后的观察", confidence=0.8))
    with db.model_generation("s"), pytest.raises(MediaIsolated):
        text.upsert(
            VectorRecord(
                scope_id="s",
                space="semantic",
                source_type="observation",
                source_id="ob",
                model_name="test-text",
                vector=(1.0, 0.0),
                metadata={"asset_id": "b", "source_revision": current.revision},
            )
        )
    repo = rebuild(db, index)
    repo.request(did, "s")
    repo.run_once("s")
    assert repo.get(did, "s")["state"] == "completed"


@pytest.mark.parametrize("invalid_citation", [False, True])
def test_fenced_json_rebuild_preserves_citation_checks(db, index, invalid_citation):
    did = cleaned(db, index)

    class FencedModel(Model):
        def complete(self, messages, tools):
            reply = super().complete(messages, tools)
            payload = json.loads(reply.content)
            if invalid_citation:
                payload["observation_ids"] = ["oa"]
            return ModelReply(content="```json\n" + json.dumps(payload) + "\n```", finish_reason="stop")

    repo = rebuild(db, index, model=FencedModel())
    assert repo.run_once("s")
    result = repo.get(did, "s")
    if invalid_citation:
        assert result["state"] == "failed"
        assert "ungrounded" in result["error"]
        with db.connect() as c:
            assert c.execute("SELECT summary FROM readable_entities WHERE id='p'").fetchone()[0] == ""
    else:
        assert result["state"] == "completed"
