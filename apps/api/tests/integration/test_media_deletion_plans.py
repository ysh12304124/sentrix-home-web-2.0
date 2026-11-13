"""Phase 3A is analysis-only: real snapshots, typed provenance, permanent denial IDs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from test_media_deletion import asset, observation
from test_media_deletion import db as db

from sentrix.domain.media.deletion import DeletionNotFound, MediaIsolated
from sentrix.domain.media.models import MediaType
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.media_barrier import publish_media_file
from sentrix.infrastructure.persistence.repositories.deletion_plans import (
    SqliteDeletionPlanRepository,
)
from sentrix.infrastructure.persistence.repositories.deletions import SqliteDeletionRepository


def request(db, root="a", scope="s"):
    return str(SqliteDeletionRepository(db).request(root, scope)["deletion_id"])


def find(plan, table, identity):
    return next(i for i in plan["impact"]["items"] if i["table"] == table and identity in i["key"].values())


def insert(c, table, **values):
    c.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def seed_shared(db):
    a = asset(db)
    b = asset(db, "b")
    observation(db, a, "oa")
    observation(db, b, "ob")
    with db.connect() as c, c:
        insert(c, "memory_spaces", id="s", name="s", created_at="2026-11-10", updated_at="2026-11-10")
        insert(
            c,
            "entities",
            id="p",
            scope_id="s",
            entity_type="person",
            canonical_name="妈妈",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "entities",
            id="p2",
            scope_id="s",
            entity_type="person",
            canonical_name="爸爸",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "face_clusters",
            id="cluster",
            scope_id="s",
            entity_id="p",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        for aid, oid, fid in [("a", "oa", "fa"), ("b", "ob", "fb")]:
            insert(
                c,
                "face_instances",
                id=fid,
                asset_id=aid,
                observation_id=oid,
                cluster_id="cluster",
                created_at="2026-11-10",
            )
            insert(
                c,
                "entity_mentions",
                id="mention" + fid,
                entity_id="p",
                observation_id=oid,
                face_instance_id=fid,
                created_at="2026-11-10",
            )
        insert(c, "events", id="event", scope_id="s", created_at="2026-11-10", updated_at="2026-11-10")
        for oid in ["oa", "ob"]:
            insert(c, "event_observations", event_id="event", observation_id=oid, created_at="2026-11-10")
        insert(
            c,
            "facts",
            id="fact",
            scope_id="s",
            subject="妈妈",
            predicate="活动",
            object="游泳",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "relationships",
            id="rel",
            scope_id="s",
            subject_entity_id="p",
            predicate="配偶",
            object_entity_id="p2",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    return a, b


def test_image_plan_is_immutable_idempotent_and_restorable(db):
    a = asset(db)
    observation(db, a)
    did = request(db)
    repo = SqliteDeletionPlanRepository(db)
    first = repo.generate(did, "s")
    assert first == repo.generate(did, "s") == repo.get(did, "s")
    assert find(first, "assets", "a")["category"] == "exclusive"
    assert find(first, "observations", "o")["category"] == "exclusive"
    assert first["impact"]["asset_ids"] == ["a"]
    assert first["impact"]["files"][0]["exists"]
    assert first["impact"]["automatic_deletion_allowed"] is False
    restarted = SqliteDeletionPlanRepository(Database(db.path, db.migrations_dir))
    assert restarted.get(did, "s") == first
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_deletion_plans").fetchone()[0] == 1
    assert Path(a.path).read_bytes() == b"original retained"


def test_video_plan_uses_durable_multilevel_member_closure(db):
    asset(db, "v", kind=MediaType.VIDEO)
    f = asset(db, "f", parent="v")
    g = asset(db, "g", parent="f")
    observation(db, f, "of")
    observation(db, g, "og")
    did = request(db, "v")
    plan = SqliteDeletionPlanRepository(db).generate(did, "s")
    assert plan["impact"]["asset_ids"] == ["f", "g", "v"]
    assert {i["key"]["id"] for i in plan["impact"]["items"] if i["table"] == "observations"} == {"of", "og"}


def test_shared_person_and_cluster_are_not_classified_exclusive(db):
    seed_shared(db)
    did = request(db)
    plan = SqliteDeletionPlanRepository(db).generate(did, "s")
    for table, identity in [("entities", "p"), ("face_clusters", "cluster")]:
        item = find(plan, table, identity)
        assert item["category"] == "shared"
        assert item["remaining_valid_evidence_count"] >= 1
        assert item["remaining_valid_asset_count"] == 1
        assert not item["automatic_deletion_allowed"]


def test_multievidence_fact_relationship_and_substring_collision(db):
    seed_shared(db)
    b = asset(db, "collision")
    observation(db, b, "oa_suffix")
    with db.connect() as c, c:
        insert(
            c,
            "facts",
            id="unrelated",
            scope_id="elsewhere",
            subject="x",
            predicate="p",
            object="v",
            evidence_ids_json='["oa_suffix"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    for table, identity in [("facts", "fact"), ("relationships", "rel")]:
        item = find(plan, table, identity)
        assert item["category"] == "shared"
        assert item["remaining_valid_evidence_count"] == 1
        assert item["target_evidence_count"] == 1
        assert {e["id"] for e in item["evidence"] if e["role"] == "evidence"} == {"oa", "ob"}
    assert not any(
        i["table"] == "facts" and i["key"].get("id") == "unrelated" for i in plan["impact"]["items"]
    )


def test_indirect_portrait_history_family_and_unknown_sources(db):
    seed_shared(db)
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
            prompt_version="v1",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        for version in [1, 2]:
            insert(
                c,
                "person_portrait_revisions",
                id=f"portrait{version}",
                scope_id="s",
                person_id="p",
                revision=version,
                portrait_text="历史画像",
                themes_json="[]",
                evidence_refs_json='[{"kind":"person_moment","id":"moment"}]',
                trigger_type="pipeline",
                prompt_version="v1",
                status="superseded" if version == 1 else "active",
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        insert(
            c,
            "family_relationships",
            id="familyrel",
            scope_id="s",
            subject_entity_id="p",
            object_entity_id="p2",
            predicate="配偶",
            inverse_predicate="配偶",
            source="model",
            evidence_refs_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "family_person_portraits",
            id="familyportrait",
            scope_id="s",
            entity_id="p",
            portrait_text="家庭画像",
            source="model",
            evidence_refs_json='["oa"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "facts",
            id="unknown",
            scope_id="s",
            subject="某人",
            predicate="喜好",
            object="未知",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "entity_properties",
            id="prop",
            entity_id="p",
            property_key="favorite",
            value_json='"游泳"',
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    for version in [1, 2]:
        item = find(plan, "person_portrait_revisions", f"portrait{version}")
        assert item["indirect"] and item["target_evidence_count"] == 1
        assert item["remaining_valid_evidence_count"] == 0
    assert find(plan, "family_relationships", "familyrel")["remaining_valid_evidence_count"] == 1
    assert find(plan, "family_person_portraits", "familyportrait")["category"] == "derived"
    assert find(plan, "entity_properties", "prop")["remaining_valid_evidence_count"] == 1
    unknown = find(plan, "facts", "unknown")
    assert unknown["category"] == "unknown" and not unknown["evidence_count_complete"]


def test_cross_scope_references_are_reported_not_modified(db):
    a = asset(db)
    observation(db, a, "oa")
    other = asset(db, "b", scope="other")
    observation(db, other, "ob")
    with db.connect() as c, c:
        insert(
            c,
            "facts",
            id="cross",
            scope_id="other",
            subject="x",
            predicate="p",
            object="v",
            evidence_ids_json='["oa","ob"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    did = request(db)
    plan = SqliteDeletionPlanRepository(db).generate(did, "s")
    item = find(plan, "facts", "cross")
    assert item["cross_scope"] and item["remaining_valid_evidence_count"] == 1
    with pytest.raises(DeletionNotFound):
        SqliteDeletionPlanRepository(db).generate(did, "other")
    with db.connect() as c:
        assert (
            c.execute("SELECT evidence_ids_json FROM facts WHERE id='cross'").fetchone()[0] == '["oa","ob"]'
        )


def test_no_business_rows_are_modified_by_planning(db):
    seed_shared(db)
    did = request(db)

    def snapshot():
        with db.connect() as c:
            return SqliteDeletionPlanRepository._snapshot(c)

    before = snapshot()
    SqliteDeletionPlanRepository(db).generate(did, "s")
    assert snapshot() == before


def test_concurrent_update_is_serialized_and_invalidates_plan(db):
    seed_shared(db)
    did = request(db)
    entered, release = Event(), Event()

    def inventory():
        entered.set()
        assert release.wait(5)
        return []

    repo = SqliteDeletionPlanRepository(db, qdrant_inventory=inventory)

    def writer():
        peer = Database(db.path, db.migrations_dir)
        with peer.connect() as c, c:
            c.execute(
                "UPDATE entities SET summary='concurrently updated', updated_at='2026-11-11' WHERE id='p'"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        generating = pool.submit(repo.generate, did, "s")
        assert entered.wait(5)
        updating = pool.submit(writer)
        release.set()
        first = generating.result()
        updating.result()
    assert repo.get(did, "s")["stale"]
    second = repo.generate(did, "s")
    assert second["version"] == 2 and second["plan_id"] != first["plan_id"]
    assert repo.generate(did, "s") == second
    assert repo.get(did, "s", 1)["stale"]


def test_permanent_tombstone_blocks_absent_asset_old_tasks(db, tmp_path):
    # Represent a post-3B state using an identity that never had a source row.
    # The test does not DELETE any business data.
    with db.connect() as c, c:
        insert(
            c,
            "media_tombstones",
            kind="asset",
            object_id="gone",
            scope_id="s",
            deletion_id="historical",
            generation=1,
            created_at="2026-11-10",
        )
        insert(
            c,
            "media_tombstones",
            kind="observation",
            object_id="gone_obs",
            scope_id="s",
            deletion_id="historical",
            generation=1,
            created_at="2026-11-10",
        )
    with pytest.raises(MediaIsolated):
        asset(db, "gone")
    with pytest.raises(MediaIsolated):
        asset(db, "new", parent="gone")
    temporary = tmp_path / "staging.mp4"
    temporary.write_bytes(b"output")
    with pytest.raises(MediaIsolated):
        publish_media_file(db, "gone", temporary, tmp_path / "published.mp4", allow_unregistered=True)
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        insert(
            c,
            "facts",
            id="stale",
            scope_id="s",
            subject="x",
            predicate="p",
            object="v",
            evidence_ids_json='["gone_obs"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

    with pytest.raises(MediaIsolated):
        SqliteVectorRepository(db).upsert(
            VectorRecord(
                scope_id="s",
                space="semantic",
                source_type="observation",
                source_id="gone_obs",
                model_name="m",
                vector=(1.0,),
            )
        )
    reopened = Database(db.path, db.migrations_dir)
    with pytest.raises(MediaIsolated), reopened.connect() as c, c:
        c.execute("UPDATE media_tombstones SET generation=0 WHERE object_id='gone'")


def test_plan_api_does_not_unisolate_or_delete(client):
    db = client.app.state.container.database
    a = asset(db)
    observation(db, a)
    did = request(db)
    url = f"/api/v1/media-deletions/{did}/plan"
    response = client.post(url, json={"scope_id": "s"})
    assert response.status_code == 200, response.text
    assert response.json()["impact"]["analysis_only"]
    assert client.get(url, params={"scope_id": "s"}).json() == response.json()
    assert client.get(url, params={"scope_id": "wrong"}).status_code == 404
    assert client.get("/api/v1/assets/a/file").status_code == 404
    assert Path(a.path).exists()


def test_graph_rotation_history_fulltext_vectors_and_file_locators(db, tmp_path):
    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    a, b = seed_shared(db)
    with db.connect() as c, c:
        insert(
            c,
            "memory_graph_nodes",
            id="asset:a",
            scope_id="s",
            node_type="asset",
            attributes_json='{"asset_id":"a"}',
            build_revision="build1",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "memory_graph_nodes",
            id="entity:p",
            scope_id="s",
            node_type="entity",
            attributes_json='{"entity_id":"p"}',
            build_revision="build1",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "memory_graph_edges",
            id="edge",
            scope_id="s",
            source_id="asset:a",
            target_id="entity:p",
            relation="depicts",
            build_revision="build1",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "memory_graph_builds",
            scope_id="s",
            build_revision="build1",
            node_count=2,
            edge_count=1,
            built_at="2026-11-10",
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
        insert(c, "memory_rotation_viewed", sequence_id="rotation", event_id="event", viewed_at="2026-11-10")
        insert(
            c,
            "agent_result_sets",
            id="results",
            scope_id="s",
            asset_ids_json='["a","b"]',
            created_at="2026-11-10",
        )
        insert(
            c,
            "observation_search_terms",
            id="term",
            scope_id="s",
            asset_id="a",
            observation_id="oa",
            field_type="caption",
            normalized_value="妈妈",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "observation_search_fts",
            tokens="妈妈",
            scope_id="s",
            asset_id="a",
            observation_id="oa",
            field_type="caption",
        )
    vector = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m/1", vector=(1.0, 0.0)
    )
    SqliteVectorRepository(db).upsert(vector)
    index = QdrantVectorIndex(tmp_path / "qdrant", database=db)
    try:
        index.upsert([vector])
        plan = SqliteDeletionPlanRepository(db, qdrant_inventory=index.deletion_inventory).generate(
            request(db), "s"
        )
        for table, identity in [
            ("memory_graph_nodes", "asset:a"),
            ("memory_graph_edges", "edge"),
            ("memory_rotation_sequences", "rotation"),
            ("agent_result_sets", "results"),
        ]:
            assert find(plan, table, identity)
        assert len(plan["impact"]["indexes"]) == 2
        sqlite_vector = next(v for v in plan["impact"]["vectors"] if v["store"] == "sqlite")
        qdrant_vector = next(v for v in plan["impact"]["vectors"] if v["store"] == "qdrant")
        assert sqlite_vector["collection"] == qdrant_vector["collection"]
        assert sqlite_vector["point_id"] == qdrant_vector["point_id"]
        assert sqlite_vector["source_type"] == "asset" and sqlite_vector["source_id"] == "a"
        assert qdrant_vector["dimensions"] == {"visual": 2}
        assert index.count(space="visual") == 1
    finally:
        index.close()


def test_external_orphans_and_unknown_points_are_not_silently_ignored(db):
    a = asset(db)
    observation(db, a)
    orphan = {
        "collection": "old",
        "point_id": "orphan",
        "dimensions": {"visual": 2},
        "payload": {"scope_id": "s", "source_type": "asset", "source_id": "a"},
    }
    unknown = {"collection": "old", "point_id": "unknown", "payload": {"scope_id": "s"}}
    plan = SqliteDeletionPlanRepository(db, qdrant_inventory=lambda: [orphan, unknown]).generate(
        request(db), "s"
    )
    assert {r["point_id"] for r in plan["impact"]["vectors"]} == {"orphan", "unknown"}
    assert next(v for v in plan["impact"]["vectors"] if v["point_id"] == "unknown")["category"] == "unknown"


def test_json_kind_is_not_guessed_from_id_and_unknown_fields_are_preserved(db):
    from sentrix.application.media.analyze_deletion import DependencyAnalysis

    snapshot = {
        "assets": [{"id": "a", "scope_id": "s", "deletion_state": "isolated", "_key": {"id": "a"}}],
        "observations": [{"id": "oa", "asset_id": "a", "scope_id": "s", "_key": {"id": "oa"}}],
        "person_portrait_revisions": [
            {
                "id": "wrongkind",
                "scope_id": "s",
                "evidence_refs_json": '[{"kind":"person_moment","id":"oa"}]',
                "_key": {"id": "wrongkind"},
            },
            {
                "id": "malformed",
                "scope_id": "s",
                "evidence_refs_json": "[bad json",
                "_key": {"id": "malformed"},
            },
        ],
    }
    items = DependencyAnalysis(snapshot, {"a"}, "s").analyze()
    wrong = next(i for i in items if i["key"].get("id") == "wrongkind")
    assert wrong["target_evidence_count"] == 0 and wrong["category"] == "unknown"
    malformed = next(i for i in items if i["key"].get("id") == "malformed")
    assert malformed["source_evidence_fields"]["evidence_refs_json"]["raw"] == "[bad json"


def test_other_isolated_evidence_is_not_counted_as_remaining_valid(db):
    seed_shared(db)
    did = request(db)
    request(db, "b")
    plan = SqliteDeletionPlanRepository(db).generate(did, "s")
    fact = find(plan, "facts", "fact")
    assert fact["remaining_valid_evidence_count"] == 0
    assert next(e for e in fact["evidence"] if e["id"] == "ob")["other_isolated_asset_ids"] == ["b"]


def test_shared_paths_symlinks_and_external_paths_are_review_only(db, tmp_path):
    a = asset(db)
    asset(db, "b")
    with db.connect() as c, c:
        c.execute("UPDATE assets SET path=? WHERE id=?", (a.path, "b"))
    preview = tmp_path / "previews" / "a-480.jpg"
    preview.parent.mkdir()
    preview.symlink_to(Path(a.path))
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    original = next(f for f in plan["impact"]["files"] if f["path"] == a.path)
    assert original["category"] == "shared" and set(original["owner_asset_ids"]) == {"a", "b"}
    link = next(f for f in plan["impact"]["files"] if f["path"] == str(preview))
    assert link["symlink"] and not link["managed"] and not link["automatic_deletion_allowed"]


def test_concurrent_plan_generation_has_one_durable_version(db):
    a = asset(db)
    observation(db, a)
    did = request(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        plans = list(
            pool.map(
                lambda _: SqliteDeletionPlanRepository(Database(db.path, db.migrations_dir)).generate(
                    did, "s"
                ),
                range(2),
            )
        )
    assert plans[0] == plans[1]
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM media_deletion_plans").fetchone()[0] == 1


def test_tombstones_backfill_existing_phase2_without_business_modifications(tmp_path):
    from shutil import copyfile

    from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository

    phase2 = tmp_path / "phase2_migrations"
    phase2.mkdir()
    current = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    for script in current.glob("*.sql"):
        if script.name < "0029":
            copyfile(script, phase2 / script.name)
    database = Database(tmp_path / "legacy.db", phase2)
    database.migrate()
    a = asset(database)
    observation(database, a)
    did = request(database)
    with database.connect() as c:
        original = tuple(c.execute("SELECT * FROM assets WHERE id='a'").fetchone())
    upgraded = Database(database.path, current)
    assert upgraded.migrate() == ["0029", "0030", "0031", "0032", "0033"]
    with upgraded.connect() as c:
        assert tuple(c.execute("SELECT * FROM assets WHERE id='a'").fetchone()) == original
        assert {(r["kind"], r["object_id"]) for r in c.execute("SELECT * FROM media_tombstones")} == {
            ("asset", "a"),
            ("observation", "o"),
        }
    assert SqliteDeletionPlanRepository(upgraded).generate(did, "s")["version"] == 1
    assert SqliteObservationRepository(upgraded).get("o") is None


def test_external_failure_is_explicit_and_plan_is_recoverable(db):
    a = asset(db)
    observation(db, a)
    did = request(db)

    def unavailable():
        raise RuntimeError("external index unavailable")

    repo = SqliteDeletionPlanRepository(db, qdrant_inventory=unavailable)
    plan = repo.generate(did, "s")
    assert plan["impact"]["coverage"]["qdrant"] == "unavailable"
    assert any("qdrant_inventory_unavailable" in w for w in plan["impact"]["warnings"])
    assert repo.get(did, "s") == plan
    assert repo.generate(did, "s") == plan


def test_typed_tombstone_evidence_and_missing_source_ids_cannot_be_reused(db):
    from datetime import UTC, datetime

    from sentrix.domain.media.models import Observation
    from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository

    a = asset(db)
    with db.connect() as c, c:
        insert(
            c,
            "media_tombstones",
            kind="observation",
            object_id="retired",
            scope_id="s",
            deletion_id="historical",
            generation=1,
            created_at="2026-11-10",
        )
    now = datetime.now(UTC)
    with pytest.raises(MediaIsolated):
        SqliteObservationRepository(db).add(
            Observation(
                id="retired",
                scope_id="s",
                asset_id=a.id,
                source_type="image",
                captured_at=now,
                confidence=1.0,
                created_at=now,
                updated_at=now,
            )
        )
    with pytest.raises(MediaIsolated), db.connect() as c, c:
        insert(
            c,
            "facts",
            id="stale",
            scope_id="s",
            subject="x",
            predicate="p",
            object="v",
            evidence_ids_json='[{"kind":"observation","id":"retired"}]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )


def test_permanent_scope_barrier_prevents_graph_rebuild_without_asset_rows(db):
    from sentrix.infrastructure.persistence.repositories.memory_graph import SqliteMemoryGraphRepository

    with db.connect() as c, c:
        insert(
            c,
            "media_tombstones",
            kind="asset",
            object_id="absent",
            scope_id="s",
            deletion_id="historical",
            generation=1,
            created_at="2026-11-10",
        )
    with pytest.raises(MediaIsolated):
        SqliteMemoryGraphRepository(db).replace_scope("s", nodes=[], edges=[], build_revision="stale")


def test_cross_scope_family_runs_and_graph_groups_are_included(db):
    seed_shared(db)
    with db.connect() as c, c:
        insert(c, "memory_spaces", id="other", name="other", created_at="2026-11-10", updated_at="2026-11-10")
        for scope in ["s", "other"]:
            insert(
                c,
                "family_scope_groups",
                scope_id=scope,
                graph_id="family-group",
                created_at="2026-11-10",
                updated_at="2026-11-10",
            )
        insert(
            c,
            "family_analysis_runs",
            id="run",
            scope_id="other",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "family_person_portraits",
            id="foreign-portrait",
            scope_id="other",
            entity_id="p",
            portrait_text="跨相册画像",
            source="model",
            inference_run_id="run",
            evidence_refs_json='["oa"]',
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    assert find(plan, "family_person_portraits", "foreign-portrait")["cross_scope"]
    assert find(plan, "family_analysis_runs", "run")["cross_scope"]
    assert find(plan, "family_scope_groups", "other")["cross_scope"]


def test_numeric_qdrant_point_identity_is_preserved(db, tmp_path):
    from qdrant_client import models

    from sentrix.domain.retrieval.ports import VectorRecord
    from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

    a = asset(db)
    observation(db, a)
    index = QdrantVectorIndex(tmp_path / "qdrant", database=db)
    vector = VectorRecord(
        scope_id="s", space="visual", source_type="asset", source_id="a", model_name="m", vector=(1.0, 0.0)
    )
    try:
        index.upsert([vector])
        location = index.locator("visual", "asset", "a", "m", 2)
        index._get_client().upsert(
            collection_name=location["collection"],
            points=[
                models.PointStruct(
                    id=7,
                    vector={"visual": [1.0, 0.0]},
                    payload={
                        "scope_id": "s",
                        "space": "visual",
                        "source_type": "asset",
                        "source_id": "a",
                        "model_name": "m",
                    },
                )
            ],
        )
        plan = SqliteDeletionPlanRepository(db, qdrant_inventory=index.deletion_inventory).generate(
            request(db), "s"
        )
        point = next(v for v in plan["impact"]["vectors"] if v.get("point_id") == 7)
        assert point["point_id_type"] == "integer"
        assert index.count(space="visual") == 2
    finally:
        index.close()


def test_cross_scope_graph_dependencies_include_build_record(db):
    a = asset(db)
    observation(db, a)
    with db.connect() as c, c:
        insert(
            c,
            "memory_graph_nodes",
            id="foreign-node",
            scope_id="other",
            node_type="asset",
            attributes_json='{"asset_id":"a"}',
            build_revision="g1",
            created_at="2026-11-10",
            updated_at="2026-11-10",
        )
        insert(
            c,
            "memory_graph_builds",
            scope_id="other",
            build_revision="g1",
            node_count=1,
            edge_count=0,
            built_at="2026-11-10",
        )
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    assert find(plan, "memory_graph_nodes", "foreign-node")["cross_scope"]
    assert find(plan, "memory_graph_builds", "other")["cross_scope"]


def test_cache_path_shared_via_metadata_is_not_exclusive(db, tmp_path):
    asset(db)
    asset(db, "b")
    cache = tmp_path / "previews" / "shared.mp4"
    cache.parent.mkdir()
    cache.write_bytes(b"preview")
    with db.connect() as c, c:
        for aid in ["a", "b"]:
            c.execute(
                "UPDATE assets SET metadata_json=? WHERE id=?",
                (json.dumps({"browser_preview_path": str(cache)}), aid),
            )
    plan = SqliteDeletionPlanRepository(db).generate(request(db), "s")
    entry = next(f for f in plan["impact"]["files"] if f["path"] == str(cache))
    assert entry["category"] == "shared"
    assert set(entry["owner_asset_ids"]) == {"a", "b"}
    assert not entry["automatic_deletion_allowed"]
