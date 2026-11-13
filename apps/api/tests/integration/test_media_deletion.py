"""Deletion preserves shared evidence and retries external cleanup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sentrix.application.media.delete_asset import DeleteAsset
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.repositories.media_deletion import SqliteMediaDeletionRepository


class FailingVectors:
    def __init__(self) -> None:
        self.fail = True
        self.deleted: tuple[tuple[str, str], ...] = ()

    def delete_sources(self, sources: tuple[tuple[str, str], ...]) -> None:
        if self.fail:
            raise RuntimeError("index unavailable")
        self.deleted = sources


def test_delete_video_cascades_keyframes_but_preserves_shared_memory(
    migrations_ready_db, tmp_path: Path
) -> None:
    db = migrations_ready_db
    storage = FileSystemMediaStorage(tmp_path / "media")
    paths = {id_: storage.store(id_, f"{id_}.jpg", b"image") for id_ in ("video", "frame", "other")}
    with db.connect() as conn, conn:
        conn.execute("INSERT INTO memory_spaces(id,name,created_at,updated_at) VALUES('s','s','now','now')")
        for id_, media_type, parent in (
            ("video", "video", None),
            ("frame", "image", "video"),
            ("other", "image", None),
        ):
            conn.execute(
                "INSERT INTO assets"
                "(id,scope_id,file_name,media_type,path,parent_asset_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (id_, "s", id_, media_type, str(paths[id_]), parent, "now", "now"),
            )
        for id_, asset in (("ov", "video"), ("of", "frame"), ("oo", "other")):
            conn.execute(
                "INSERT INTO observations(id,scope_id,asset_id,source_type,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (id_, "s", asset, "image", "now", "now"),
            )
        for id_, source in (("shared", "video"), ("only", "video")):
            conn.execute(
                "INSERT INTO events(id,scope_id,source_asset_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (id_, "s", source, "now", "now"),
            )
        for event, obs in (("shared", "of"), ("shared", "oo"), ("only", "of")):
            conn.execute(
                "INSERT INTO event_observations(event_id,observation_id,created_at) VALUES(?,?,?)",
                (event, obs, "now"),
            )
        for id_, evidence in (("shared", ["of", "oo"]), ("only", ["of"])):
            conn.execute(
                "INSERT INTO facts"
                "(id,scope_id,subject,predicate,object,evidence_ids_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (id_, "s", "a", id_, "b", json.dumps(evidence), "now", "now"),
            )
        conn.execute(
            "INSERT INTO entities(id,scope_id,entity_type,canonical_name,created_at,updated_at) "
            "VALUES('person','s','person','A','now','now')"
        )
        conn.execute(
            "INSERT INTO event_participants"
            "(id,event_id,person_id,role,evidence_ids_json,created_at,updated_at) "
            "VALUES('participant','shared','person','participant','[\"of\"]','now','now')"
        )
        conn.execute(
            "INSERT INTO face_clusters(id,scope_id,created_at,updated_at) VALUES('cluster','s','now','now')"
        )
        conn.execute(
            "INSERT INTO face_instances(id,asset_id,observation_id,cluster_id,created_at) "
            "VALUES('face','frame','of','cluster','now')"
        )
        conn.execute(
            "INSERT INTO face_prototypes(id,cluster_id,face_instance_id,created_at,updated_at) "
            "VALUES('proto','cluster','face','now','now')"
        )
        conn.execute(
            "INSERT INTO entity_mentions(id,entity_id,observation_id,face_instance_id,created_at) "
            "VALUES('mention','person','of','face','now')"
        )
        conn.execute(
            "INSERT INTO person_moments"
            "(id,scope_id,person_id,cluster_id,event_id,observation_id,asset_id,face_instance_id,"
            "prompt_version,created_at,updated_at) "
            "VALUES('moment','s','person','cluster','only','of','frame','face','v1','now','now')"
        )
        conn.execute(
            "INSERT INTO observation_search_terms"
            "(id,observation_id,asset_id,scope_id,field_type,normalized_value,created_at,updated_at) "
            "VALUES('term','of','frame','s','caption','a','now','now')"
        )
        conn.execute(
            "INSERT INTO observation_search_fts(tokens,scope_id,field_type,asset_id,observation_id) "
            "VALUES('a','s','caption','frame','of')"
        )
        for source_type, source_id in (("asset", "frame"), ("face_instance", "face")):
            conn.execute(
                "INSERT INTO memory_vectors"
                "(id,scope_id,space,source_type,source_id,model_name,"
                "dimension,vector_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'m',2,'[1,0]','now','now')",
                (source_id, "s", "visual", source_type, source_id),
            )
        for id_, kind, attrs in (
            ("asset:video", "asset", {}),
            ("asset:frame", "asset", {}),
            ("asset:other", "asset", {}),
            ("event:shared", "event", {"source_asset_id": "video", "observation_ids": ["of", "oo"]}),
            ("fact:shared", "fact", {"evidence_ids": ["of", "oo"]}),
            ("entity:person", "entity", {}),
        ):
            conn.execute(
                "INSERT INTO memory_graph_nodes"
                "(id,scope_id,node_type,attributes_json,build_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (id_, "s", kind, json.dumps(attrs), "rev", "now", "now"),
            )
        conn.execute(
            "INSERT INTO memory_graph_edges"
            "(id,scope_id,source_id,target_id,relation,build_revision,created_at,updated_at) "
            "VALUES('edge','s','asset:frame','asset:other','image_similar','rev','now','now')"
        )
        conn.execute(
            "INSERT INTO memory_graph_edges"
            "(id,scope_id,source_id,target_id,relation,build_revision,created_at,updated_at) "
            "VALUES('edge2','s','event:shared','entity:person','event_has_entity','rev','now','now')"
        )

    repository = SqliteMediaDeletionRepository(db)
    vectors = FailingVectors()
    use_case = DeleteAsset(repository, storage, vectors)  # type: ignore[arg-type]
    result = use_case.execute("video")
    assert result is not None and result.warnings
    with db.connect() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert {r[0] for r in conn.execute("SELECT id FROM assets")} == {"other"}
        assert {r[0] for r in conn.execute("SELECT id FROM events")} == {"shared"}
        assert conn.execute("SELECT source_asset_id FROM events WHERE id='shared'").fetchone()[0] is None
        assert json.loads(
            conn.execute("SELECT evidence_ids_json FROM facts WHERE id='shared'").fetchone()[0]
        ) == ["oo"]
        assert {r[0] for r in conn.execute("SELECT id FROM memory_graph_nodes")} == {
            "asset:other",
            "event:shared",
            "fact:shared",
        }
        assert not conn.execute("SELECT 1 FROM memory_graph_edges").fetchone()
        for table in (
            "face_instances",
            "face_clusters",
            "face_prototypes",
            "entity_mentions",
            "person_moments",
            "observation_search_terms",
            "observation_search_fts",
            "memory_vectors",
            "event_participants",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not paths["video"].exists() and not paths["frame"].exists() and paths["other"].exists()
    assert repository.pending_cleanup("video")
    pending_record = repository.list_deletions(scope_id="s", limit=10)[0]
    assert pending_record.asset_id == "video"
    assert pending_record.asset_count == 2
    assert pending_record.file_name == "video"
    assert pending_record.files_done and not pending_record.vectors_done
    vectors.fail = False
    assert use_case.retry("video") == ()
    assert not repository.pending_cleanup("video")
    complete_record = repository.list_deletions(scope_id="s", limit=10)[0]
    assert complete_record.files_done and complete_record.vectors_done
    assert complete_record.attempts >= pending_record.attempts
    assert repository.list_deletions(scope_id="other", limit=10) == ()
    assert ("asset", "frame") in vectors.deleted


def test_file_cleanup_removes_managed_derivatives_but_keeps_external_source(tmp_path: Path) -> None:
    storage = FileSystemMediaStorage(tmp_path / "media")
    owned = storage.store("video", "source.mp4", b"video")
    preview = tmp_path / "media" / "derived" / "video" / "video" / "preview.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview")
    external = tmp_path / "dataset" / "source.mp4"
    external.parent.mkdir()
    external.write_bytes(b"original")

    skipped = storage.delete_assets(("video",), (str(owned), str(external)))

    assert skipped == (str(external),)
    assert not owned.exists() and not preview.exists()
    assert external.read_bytes() == b"original"


def test_active_video_job_blocks_deletion_without_partial_changes(migrations_ready_db) -> None:
    db = migrations_ready_db
    with db.connect() as conn, conn:
        conn.execute(
            "INSERT INTO assets(id,file_name,media_type,path,created_at,updated_at) "
            "VALUES('active','a.mp4','video','/tmp/a.mp4','now','now')"
        )
        conn.execute(
            "INSERT INTO video_jobs(job_id,asset_id,video_hash,status,created_at,updated_at) "
            "VALUES('job','active','hash','PROCESSING','now','now')"
        )

    with pytest.raises(ValueError, match="仍在处理"):
        SqliteMediaDeletionRepository(db).delete_asset("active")

    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM assets WHERE id='active'").fetchone()
        assert not conn.execute("SELECT 1 FROM media_cleanup_jobs").fetchone()


def test_confirmed_identity_survives_without_deleted_face_embedding(migrations_ready_db) -> None:
    db = migrations_ready_db
    with db.connect() as conn, conn:
        conn.execute(
            "INSERT INTO assets(id,file_name,media_type,path,created_at,updated_at) "
            "VALUES('photo','a.jpg','image','/tmp/a.jpg','now','now')"
        )
        conn.execute(
            "INSERT INTO observations(id,asset_id,source_type,created_at,updated_at) "
            "VALUES('obs','photo','image','now','now')"
        )
        conn.execute(
            "INSERT INTO face_clusters"
            "(id,status,member_count,representative_embedding_json,created_at,updated_at) "
            "VALUES('confirmed','confirmed',1,'[1,0]','now','now')"
        )
        conn.execute(
            "INSERT INTO face_instances(id,asset_id,observation_id,cluster_id,created_at) "
            "VALUES('face','photo','obs','confirmed','now')"
        )

    assert SqliteMediaDeletionRepository(db).delete_asset("photo")

    with db.connect() as conn:
        row = conn.execute(
            "SELECT member_count,representative_embedding_json FROM face_clusters WHERE id='confirmed'"
        ).fetchone()
        assert tuple(row) == (0, "[]")
        assert not conn.execute("SELECT 1 FROM face_instances").fetchone()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
