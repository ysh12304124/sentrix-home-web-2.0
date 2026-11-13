"""3B performs exact withdrawals, never source cleanup or model generation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_media_deletion import asset, observation
from test_media_deletion import db as db
from test_media_deletion_plans import insert, request, seed_shared

from sentrix.application.media.withdraw_evidence import encode_key
from sentrix.domain.media.deletion import DeletionNotFound, MediaIsolated
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.deletion_plans import SqliteDeletionPlanRepository
from sentrix.infrastructure.persistence.repositories.reconciliations import SqliteReconciliationRepository


def row(db, table, identity):
    with db.connect() as c:
        found = c.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    return dict(found) if found else None


def state(db, table, key):
    with db.connect() as c:
        found = c.execute(
            "SELECT * FROM media_object_states WHERE object_table=? AND object_key=?",
            (table, encode_key(key)),
        ).fetchone()
    return dict(found) if found else None


def shared(db):
    a, b = seed_shared(db)
    with db.connect() as c, c:
        c.execute("UPDATE entities SET status='confirmed',summary='旧模型画像' WHERE id IN ('p','p2')")
        c.execute("UPDATE facts SET confidence=0.99 WHERE id='fact'")
        c.execute("UPDATE face_instances SET embedding_json='[1,0]',quality=0.99 WHERE id='fa'")
        c.execute("UPDATE face_instances SET embedding_json='[0,1]',quality=0.6 WHERE id='fb'")
        c.execute(
            "UPDATE face_clusters SET "
            "member_count=2,representative_embedding_json='[1,0]',confidence=0.99 WHERE "
            "id='cluster'"
        )
        insert(
            c,
            "face_prototypes",
            id="proto",
            cluster_id="cluster",
            face_instance_id="fa",
            pose_bucket="unknown",
            embedding_json="[1,0]",
            quality=0.99,
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "event_participants",
            id="part",
            event_id="event",
            person_id="p",
            role="visible_subject",
            evidence_ids_json='["oa","ob"]',
            confidence=0.99,
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        c.execute(
            "UPDATE events SET "
            "summary='旧事件摘要',title='旧事件标题',source_metadata_json='{\"cover_asset_id\":\"a\"}' "
            "WHERE id='event'"
        )
    return a, b


def execute(db, root="a"):
    did = request(db, root)
    result = SqliteReconciliationRepository(db).execute(did, "s")
    assert result["status"] == "reconciled"
    assert result["physical_deletion_complete"] is False
    return did, result


def test_single_evidence_fact_is_withdrawn_not_deleted(db):
    shared(db)
    with db.connect() as c, c:
        c.execute("UPDATE facts SET evidence_ids_json='[\"oa\"]' WHERE id='fact'")
    did, _ = execute(db)
    fact = row(db, "facts", "fact")
    assert json.loads(fact["evidence_ids_json"]) == []
    assert fact["status"] == "retracted" and fact["confidence"] == 0.99
    assert state(db, "facts", {"id": "fact"})["validity"] == "invalid"
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM media_deletions WHERE deletion_id=?", (did,)).fetchone()[0]
            == "isolated"
        )
        assert (
            c.execute(
                "SELECT evidence_id FROM media_evidence_revocations WHERE object_table='facts'"
            ).fetchone()[0]
            == "oa"
        )


def test_partial_fact_and_relation_keep_other_evidence_and_require_review(db):
    shared(db)
    execute(db)
    assert json.loads(row(db, "facts", "fact")["evidence_ids_json"]) == ["ob"]
    assert json.loads(row(db, "relationships", "rel")["evidence_ids_json"]) == ["ob"]
    assert row(db, "facts", "fact")["confidence"] == 0.99
    assert row(db, "facts", "fact")["status"] == "pending"
    assert state(db, "facts", {"id": "fact"})["validity"] == "needs_review"
    from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository

    assert SqliteFactRepository(db).get("fact") is None


def test_two_deletions_sharing_fact_do_not_revoke_the_other_material(db):
    shared(db)
    c_asset = asset(db, "c")
    observation(db, c_asset, "oc")
    with db.connect() as c, c:
        c.execute('UPDATE facts SET evidence_ids_json=\'["oa","ob","oc"]\' WHERE id=\'fact\'')
    first = request(db, "a")
    second = request(db, "c")
    repo = SqliteReconciliationRepository(db)
    repo.execute(first, "s")
    assert json.loads(row(db, "facts", "fact")["evidence_ids_json"]) == ["ob", "oc"]
    repo.execute(second, "s")
    assert json.loads(row(db, "facts", "fact")["evidence_ids_json"]) == ["ob"]
    assert repo.execute(first, "s")["status"] == "reconciled"
    with db.connect() as c:
        assert {
            (r["deletion_id"], r["evidence_id"])
            for r in c.execute("SELECT * FROM media_evidence_revocations WHERE object_table='facts'")
        } == {(first, "oa"), (second, "oc")}


def test_people_clusters_representative_and_prototypes_are_updated(db):
    shared(db)
    _, result = execute(db)
    assert row(db, "entities", "p")["canonical_name"] == "妈妈"
    assert row(db, "face_clusters", "cluster")["member_count"] == 1
    assert json.loads(row(db, "face_clusters", "cluster")["representative_embedding_json"]) == [0, 1]
    assert row(db, "face_prototypes", "proto")["face_instance_id"] == "fb"
    assert row(db, "face_clusters", "cluster")["confidence"] == 0.6
    metrics = json.loads(state(db, "entities", {"id": "p"})["metrics_json"])
    assert metrics["valid_asset_count"] == 1 and metrics["valid_observation_count"] == 1
    from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository

    assert SqliteEntityRepository(db).get("p").summary == ""
    assert any(o["object_table"] == "entities" for o in result["objects"])


def test_event_participants_times_and_cover_use_only_remaining_observations(db):
    _, b = shared(db)
    execute(db)
    assert json.loads(row(db, "event_participants", "part")["evidence_ids_json"]) == ["ob"]
    event = row(db, "events", "event")
    assert json.loads(event["source_metadata_json"])["cover_asset_id"] == "b"
    assert event["time_start"] == b.captured_at.isoformat() == event["time_end"]
    assert event["summary"] == "旧事件摘要"  # history retained, never reused as evidence
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

    current = SqliteEventRepository(db).get("event")
    assert current is not None and current.summary == "" and current.title == "待更新事件"
    with db.connect() as c:
        assert [
            r[0]
            for r in c.execute(
                "SELECT observation_id FROM readable_event_observations WHERE event_id=?", ("event",)
            )
        ] == ["ob"]


def test_event_without_remaining_observations_is_invalid(db):
    shared(db)
    # Both inputs isolated before either reconciliation; there is no live evidence.
    request(db, "b")
    execute(db, "a")
    assert state(db, "events", {"id": "event"})["validity"] == "invalid"
    assert row(db, "events", "event")["status"] == "invalid"
    assert state(db, "event_participants", {"id": "part"})["validity"] == "invalid"
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

    assert SqliteEventRepository(db).get("event") is None


def test_indirect_historical_portraits_semantic_and_family_content_are_invalid(db):
    shared(db)
    with db.connect() as c, c:
        insert(
            c,
            "person_moments",
            id="moment",
            scope_id="s",
            person_id="p",
            cluster_id="cluster",
            event_id="event",
            observation_id="oa",
            asset_id="a",
            face_instance_id="fa",
            prompt_version="v",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        for n in [1, 2]:
            insert(
                c,
                "person_portrait_revisions",
                id=f"portrait{n}",
                scope_id="s",
                person_id="p",
                revision=n,
                portrait_text="旧画像",
                evidence_refs_json='[{"kind":"person_moment","id":"moment"}]',
                trigger_type="pipeline",
                prompt_version="v",
                status="superseded" if n == 1 else "user_locked",
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        insert(
            c,
            "semantic_profiles",
            id="profile",
            scope_id="s",
            person_id="p",
            summary_zh="无来源旧总结",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "family_person_portraits",
            id="fp",
            scope_id="s",
            entity_id="p",
            portrait_text="家庭模型画像",
            source="model",
            evidence_refs_json='["oa"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    execute(db)
    for table, identity in [
        ("person_portrait_revisions", "portrait1"),
        ("person_portrait_revisions", "portrait2"),
        ("semantic_profiles", "profile"),
        ("family_person_portraits", "fp"),
    ]:
        assert state(db, table, {"id": identity})["validity"] == "invalid"
        with db.connect() as c:
            assert (
                c.execute(f"SELECT count(*) FROM readable_{table} WHERE id=?", (identity,)).fetchone()[0] == 0
            )
    assert row(db, "person_portrait_revisions", "portrait2")["portrait_text"] == "旧画像"
    assert row(db, "person_portrait_revisions", "portrait2")["status"] == "user_locked"


def test_independent_manual_content_is_preserved_but_dependent_confirmation_reviewed(db):
    shared(db)
    with db.connect() as c, c:
        for identity, refs in [("manual", "[]"), ("confirmed_dependent", '["oa","ob"]')]:
            insert(
                c,
                "entity_properties",
                id=identity,
                entity_id="p",
                property_key=identity,
                value_json='"人工维护"',
                source="user",
                evidence_ids_json=refs,
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        insert(
            c,
            "family_person_portraits",
            id="human",
            scope_id="s",
            entity_id="p",
            portrait_text="独立人工档案",
            source="user_override",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    before = row(db, "entity_properties", "manual")
    execute(db)
    assert row(db, "entity_properties", "manual") == before
    assert state(db, "entity_properties", {"id": "manual"}) is None
    assert state(db, "family_person_portraits", {"id": "human"}) is None
    assert json.loads(row(db, "entity_properties", "confirmed_dependent")["evidence_ids_json"]) == ["ob"]
    assert state(db, "entity_properties", {"id": "confirmed_dependent"})["validity"] == "needs_review"


def test_unknown_and_cross_scope_objects_are_not_guessed_or_edited(db):
    shared(db)
    with db.connect() as c, c:
        insert(
            c,
            "facts",
            id="unknown",
            scope_id="s",
            subject="人",
            predicate="偏好",
            object="旧推断",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "facts",
            id="cross",
            scope_id="other",
            subject="人",
            predicate="偏好",
            object="跨相册推断",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    before = {identity: row(db, "facts", identity) for identity in ["unknown", "cross"]}
    execute(db)
    assert {identity: row(db, "facts", identity) for identity in before} == before
    assert state(db, "facts", {"id": "unknown"})["validity"] == "needs_review"
    assert state(db, "facts", {"id": "cross"})["reason"] == "cross_scope_review"


def test_old_model_and_rebuild_results_cannot_restore_revoked_evidence(db):
    shared(db)
    execute(db)
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE facts SET confidence=1,evidence_ids_json='[\"ob\"]' WHERE id='fact'")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE facts SET evidence_ids_json='[\"oa\",\"ob\"]' WHERE id='fact'")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE entities SET summary='旧推理晚到' WHERE id='p'")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE events SET summary='旧总结晚到' WHERE id='event'")
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        c.execute("UPDATE face_clusters SET representative_embedding_json='[1,0]' WHERE id='cluster'")
    from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

    assert SqliteFaceRepository(db).refresh_prototypes("cluster")[0].face_instance_id == "fb"


def test_repeated_execution_has_no_extra_revocations_or_step_side_effects(db):
    shared(db)
    did, first = execute(db)
    with db.connect() as c:
        snapshot = {
            t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")]
            for t in [
                "media_evidence_revocations",
                "media_reconciliation_steps",
                "media_object_states",
                "face_prototypes",
                "face_clusters",
            ]
        }
    assert SqliteReconciliationRepository(db).execute(did, "s") == first
    with db.connect() as c:
        assert {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in snapshot} == snapshot


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_failure_resume_uses_checkpoints_and_keeps_intermediate_reads_safe(db, boundary):
    shared(db)
    did = request(db)

    def fail(step, where):
        if step == "evidence" and where == boundary:
            raise RuntimeError("injected interruption")

    repo = SqliteReconciliationRepository(db, fault=fail)
    with pytest.raises(RuntimeError):
        repo.execute(did, "s")
    failed = repo.get(did, "s")
    assert failed["status"] == "failed"
    assert failed["step_index"] == (0 if boundary == "before_commit" else 1)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM readable_facts WHERE id='fact'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM readable_events WHERE id='event'").fetchone()[0] == 0
    reopened = Database(db.path, db.migrations_dir)
    result = SqliteReconciliationRepository(reopened).execute(did, "s")
    assert result["status"] == "reconciled" and result["retry_count"] == 1
    assert len(result["steps"]) == 4
    assert json.loads(row(db, "facts", "fact")["evidence_ids_json"]) == ["ob"]
    with db.connect() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM media_evidence_revocations WHERE object_table='facts'"
            ).fetchone()[0]
            == 1
        )


def test_source_rows_vectors_files_and_caches_are_not_physically_deleted(db, tmp_path):
    shared(db)
    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

    vector = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    SqliteVectorRepository(db).upsert(vector)
    cache = tmp_path / "previews" / "a-480.jpg"
    cache.parent.mkdir()
    cache.write_bytes(b"cache")
    with db.connect() as c:
        before = {
            t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")]
            for t in ["assets", "observations", "face_instances", "memory_vectors", "text_vectors"]
        }
    # Isolation itself adds state to assets; take source snapshot after it.
    did = request(db)
    with db.connect() as c:
        before["assets"] = [tuple(r) for r in c.execute("SELECT * FROM assets")]
    SqliteReconciliationRepository(db).execute(did, "s")
    with db.connect() as c:
        assert {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in before} == before
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert cache.read_bytes() == b"cache"
    for aid in ["a", "b"]:
        assert Path(row(db, "assets", aid)["path"]).read_bytes() == b"original retained"


def test_invalid_scope_cannot_modify_an_existing_execution(db):
    shared(db)
    did = request(db)

    def fail(step, where):
        if step == "evidence" and where == "after_commit":
            raise RuntimeError("stop")

    repo = SqliteReconciliationRepository(db, fault=fail)
    with pytest.raises(RuntimeError):
        repo.execute(did, "s")
    before = repo.get(did, "s")
    with pytest.raises(DeletionNotFound):
        repo.execute(did, "other")
    assert repo.get(did, "s") == before


def test_concurrent_executors_are_serialized_and_checkpoint_once(db):
    shared(db)
    did = request(db)

    def run(_):
        connection_factory = Database(db.path, db.migrations_dir)
        return SqliteReconciliationRepository(connection_factory).execute(did, "s")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(result["status"] == "reconciled" for result in pool.map(run, range(2)))
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_reconciliation_steps").fetchone()[0] == 4
        assert (
            c.execute(
                "SELECT count(*) FROM media_evidence_revocations WHERE object_table='facts'"
            ).fetchone()[0]
            == 1
        )
    assert json.loads(row(db, "facts", "fact")["evidence_ids_json"]) == ["ob"]


def test_stale_plan_is_versioned_without_overwriting_old_snapshot(db):
    shared(db)
    did = request(db)
    plans = SqliteDeletionPlanRepository(db)
    first = plans.generate(did, "s")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET file_name='renamed.jpg' WHERE id='b'")
    result = SqliteReconciliationRepository(db).execute(did, "s")
    assert result["initial_plan_id"] != first["plan_id"]
    old = plans.get(did, "s", first["version"])
    assert old["stale"] is True
    assert {k: v for k, v in old.items() if k != "stale"} == {k: v for k, v in first.items() if k != "stale"}
    assert len(result["plan_history"]) > 1


def test_semantic_claim_uses_typed_ids_and_does_not_subtract_aggregates(db):
    shared(db)
    with db.connect() as c, c:
        insert(
            c,
            "semantic_claims",
            id="claim",
            scope_id="s",
            person_id="p",
            dimension="activity",
            predicate="喜欢",
            value_text="游泳",
            support_count=17,
            evidence_ids_json=json.dumps(
                [
                    {"kind": "asset", "id": "a"},
                    {"kind": "observation", "id": "ob"},
                    {"kind": "entity", "id": "a"},
                ]
            ),
            supporting_event_ids_json='["event"]',
            confidence=0.97,
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    execute(db)
    claim = row(db, "semantic_claims", "claim")
    assert json.loads(claim["evidence_ids_json"]) == [
        {"kind": "observation", "id": "ob"},
        {"kind": "entity", "id": "a"},
    ]
    # The shared event still has B evidence. Its old aggregate conclusion is not reliable.
    assert json.loads(claim["supporting_event_ids_json"]) == ["event"]
    assert claim["support_count"] == 17 and claim["confidence"] == 0.97
    assert state(db, "semantic_claims", {"id": "claim"})["validity"] == "needs_review"
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM readable_semantic_claims").fetchone()[0] == 0


def test_event_entity_relations_require_current_typed_observation_proof(db):
    shared(db)
    with db.connect() as c, c:
        insert(
            c,
            "entities",
            id="place",
            scope_id="s",
            entity_type="place",
            canonical_name="公园",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        for oid in ["oa", "ob"]:
            insert(
                c,
                "entity_observations",
                entity_id="place",
                observation_id=oid,
                confidence=0.7,
                created_at="2026-11-10",
            )
        for relation in ["地点", "发生婚礼"]:
            insert(
                c,
                "event_entities",
                entity_id="place",
                event_id="event",
                relation=relation,
                evidence_ids_json='["oa","ob"]',
                confidence=0.99,
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
    execute(db)
    with db.connect() as c:
        visible = c.execute("SELECT * FROM readable_event_entities").fetchall()
        assert len(visible) == 1 and visible[0]["relation"] == "地点"
        assert json.loads(visible[0]["evidence_ids_json"]) == ["ob"]
        assert visible[0]["confidence"] == 0.7
        # Logical bridge revocation retains the original row for later 3C.
        assert c.execute("SELECT count(*) FROM entity_observations").fetchone()[0] == 2


def test_graph_rotation_history_and_cached_results_cannot_reintroduce_old_output(db):
    shared(db)
    with db.connect() as c, c:
        insert(c, "memory_graph_builds", scope_id="s", build_revision="g", built_at="2026-11-10")
        insert(
            c,
            "memory_graph_nodes",
            id="gn",
            scope_id="s",
            node_type="person",
            label="旧推断",
            build_revision="g",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "memory_rotation_sequences",
            sequence_id="rotation",
            scope_id="s",
            day="2026-11-10",
            event_ids_json='["event"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "agent_conversations",
            id="conversation",
            scope_id="s",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "agent_turns",
            id="turn",
            scope_id="s",
            conversation_id="conversation",
            question="谁",
            answer="旧模型结论",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "agent_conversation_messages",
            id="msg",
            conversation_id="conversation",
            scope_id="s",
            turn_id="turn",
            role="assistant",
            content="旧模型结论",
            created_at="2026-11-10",
        )
        insert(
            c,
            "agent_turn_events_v2",
            turn_id="turn",
            event_seq=1,
            attempt_no=1,
            payload_json='{"text":"旧模型结论"}',
        )
        insert(
            c,
            "agent_result_sets",
            id="rs",
            scope_id="s",
            query="谁",
            turn_id="turn",
            asset_ids_json='["a","b"]',
            created_at="2026-11-10",
        )
    execute(db)
    with db.connect() as c:
        for table in [
            "memory_graph_nodes",
            "memory_graph_builds",
            "memory_rotation_sequences",
            "agent_turn_events_v2",
            "agent_result_sets",
        ]:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
            assert c.execute(f"SELECT count(*) FROM readable_{table}").fetchone()[0] == 0
        assert (
            "旧模型"
            not in c.execute("SELECT content FROM readable_agent_conversation_messages").fetchone()[0]
        )
        assert "旧模型" not in c.execute("SELECT answer FROM readable_agent_turns").fetchone()[0]
        assert c.execute("SELECT rowid FROM readable_agent_turns").fetchone()[0] is not None
    from sentrix.infrastructure.persistence.repositories.assets import (
        SqliteAssetRepository,
        SqliteObservationRepository,
    )

    assert SqliteAssetRepository(db).get("b") is not None
    assert SqliteAssetRepository(db).get("a") is None
    assert SqliteObservationRepository(db).get("ob") is not None


def test_old_model_cannot_evade_review_by_using_new_row_identity(db):
    shared(db)
    execute(db)
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        insert(
            c,
            "facts",
            id="late-new-id",
            scope_id="s",
            subject="妈妈",
            predicate="喜欢",
            object="旧推断",
            evidence_ids_json='["ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    # Explicit source-independent manual maintenance does not require model reconstruction.
    with db.connect() as c, c:
        insert(
            c,
            "entity_properties",
            id="new-manual",
            entity_id="p",
            property_key="称呼",
            source="user",
            value_json='"妈妈"',
            evidence_ids_json="[]",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    assert row(db, "entity_properties", "new-manual") is not None


def test_healthy_media_remains_searchable_without_deleting_any_qdrant_points(db, tmp_path):
    shared(db)
    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    index = QdrantVectorIndex(tmp_path / "qdrant", database=db)
    try:
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
        execute(db)
        assert [hit.source_id for hit in index.search(space="visual", vector=[1.0, 0.0], limit=10)] == ["b"]
        assert index.count(space="visual") == 2
    finally:
        index.close()


def test_reconciliation_http_contract_and_scope_authorization(client):
    import io

    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format="JPEG")
    imported = client.post(
        "/api/v1/media/import",
        data={"scope_id": "s"},
        files={"file": ("x.jpg", stream.getvalue(), "image/jpeg")},
    )
    assert imported.status_code == 201
    aid = imported.json()["asset_id"]
    isolated = client.post(f"/api/v1/assets/{aid}/deletion", json={"scope_id": "s"})
    did = isolated.json()["deletion_id"]
    endpoint = f"/api/v1/media-deletions/{did}"
    assert client.post(endpoint + "/reconcile", json={"scope_id": "wrong"}).status_code == 404
    response = client.post(endpoint + "/reconcile", json={"scope_id": "s"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "reconciled"
    assert client.get(endpoint + "/reconciliation", params={"scope_id": "s"}).json() == response.json()
    assert client.get(endpoint, params={"scope_id": "s"}).json()["status"] == "isolated"
    assert client.get(f"/api/v1/assets/{aid}/file").status_code == 404


def test_person_counts_include_live_cluster_evidence_without_a_mention_bridge(db):
    shared(db)
    with db.connect() as c, c:
        c.execute("DELETE FROM entity_mentions WHERE observation_id='ob'")
    execute(db)
    metrics = json.loads(state(db, "entities", {"id": "p"})["metrics_json"])
    assert metrics["valid_observation_count"] == 1
    assert metrics["valid_asset_count"] == 1
