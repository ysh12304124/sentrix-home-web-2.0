"""Atomic media and memory deletion. Shared memories keep their surviving evidence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from sentrix.domain.media.ports import MediaDeletionRecord
from sentrix.infrastructure.persistence.database import Database


def _ids(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in connection.execute(sql, params).fetchall())


def _slots(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


class SqliteMediaDeletionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def delete_asset(self, asset_id: str) -> bool:
        with self._database.connect() as db, db:
            # Lock before reading lineage so concurrent import cannot append a
            # keyframe after the children have been enumerated.
            db.execute("BEGIN IMMEDIATE")
            root = db.execute(
                "SELECT scope_id,parent_asset_id,file_name,media_type FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if root is None:
                return False
            if root[1]:
                raise ValueError("请删除源视频，不能单独删除派生关键帧")
            job = db.execute("SELECT status FROM video_jobs WHERE asset_id=?", (asset_id,)).fetchone()
            if job and job[0] in {"QUEUED", "PROCESSING", "RETRY_WAIT"}:
                raise ValueError("视频仍在处理，请先停止处理任务")
            scope = str(root[0])
            assets = (asset_id, *_ids(db, "SELECT id FROM assets WHERE parent_asset_id=?", (asset_id,)))
            a = _slots(assets)
            paths = _ids(db, f"SELECT path FROM assets WHERE id IN ({a})", assets)
            observations = _ids(db, f"SELECT id FROM observations WHERE asset_id IN ({a})", assets)
            o = _slots(observations)
            affected_events = set(_ids(db, f"SELECT id FROM events WHERE source_asset_id IN ({a})", assets))
            if observations:
                affected_events.update(
                    _ids(
                        db,
                        f"SELECT DISTINCT event_id FROM event_observations WHERE observation_id IN ({o})",
                        observations,
                    )
                )
            events: set[str] = set()
            for event_id in affected_events:
                remaining = db.execute(
                    "SELECT COUNT(*) FROM event_observations WHERE event_id=?"
                    + (f" AND observation_id NOT IN ({o})" if observations else ""),
                    (event_id, *observations),
                ).fetchone()[0]
                if not remaining:
                    events.add(event_id)
            removed_refs = set((*assets, *observations, *events))
            removed_refs.update(
                f"{kind}:{source}"
                for kind, group in (
                    ("asset", assets),
                    ("observation", observations),
                    ("event", tuple(events)),
                )
                for source in group
            )
            faces = _ids(db, f"SELECT id FROM face_instances WHERE asset_id IN ({a})", assets)
            f = _slots(faces)
            removed_refs.update(faces)
            removed_refs.update(f"face_instance:{face_id}" for face_id in faces)
            clusters = (
                _ids(
                    db,
                    f"SELECT DISTINCT cluster_id FROM face_instances WHERE id IN ({f}) "
                    "AND cluster_id IS NOT NULL",
                    faces,
                )
                if faces
                else ()
            )
            vector_sources = tuple(
                (kind, source)
                for kind, group in (
                    ("asset", assets),
                    ("observation", observations),
                    ("face_instance", faces),
                    ("event", tuple(events)),
                )
                for source in group
            )
            # Strip deleted observations from evidence lists, keeping shared memories.
            changed_facts: set[str] = set()
            deleted_facts: set[str] = set()
            deleted_relationships: set[str] = set()
            for table, key in (
                ("facts", "id"),
                ("relationships", "id"),
                ("event_participants", "id"),
                ("event_entities", "rowid"),
                ("entity_properties", "id"),
                ("entity_revisions", "id"),
            ):
                for row in db.execute(f"SELECT {key},evidence_ids_json FROM {table}").fetchall():
                    old = json.loads(row[1] or "[]")
                    if not isinstance(old, list):
                        continue
                    new = [item for item in old if not isinstance(item, str) or item not in removed_refs]
                    if new == old:
                        continue
                    if table == "facts" and not new:
                        db.execute("DELETE FROM facts WHERE id=?", (row[0],))
                        deleted_facts.add(str(row[0]))
                    elif table in {"relationships", "event_participants", "event_entities"} and not new:
                        db.execute(f"DELETE FROM {table} WHERE {key}=?", (row[0],))
                        if table == "relationships":
                            deleted_relationships.add(str(row[0]))
                    elif table == "entity_properties" and not new:
                        db.execute(
                            "UPDATE entity_properties SET evidence_ids_json='[]',"
                            "status=CASE WHEN source='user' THEN status ELSE 'retracted' END "
                            "WHERE id=?",
                            (row[0],),
                        )
                    else:
                        db.execute(
                            f"UPDATE {table} SET evidence_ids_json=? WHERE {key}=?", (json.dumps(new), row[0])
                        )
                    if table == "facts":
                        changed_facts.add(str(row[0]))
            # Other derived memory families also keep evidence provenance as JSON
            # string lists. Removing an asset must not leave their old references.
            for table, key, column in (
                ("semantic_profiles", "id", "evidence_ids_json"),
                ("semantic_claims", "id", "evidence_ids_json"),
                ("family_person_memberships", "id", "evidence_refs_json"),
                ("family_relationships", "id", "evidence_refs_json"),
                ("family_person_portraits", "id", "evidence_refs_json"),
                ("person_portrait_revisions", "id", "evidence_refs_json"),
            ):
                for row in db.execute(
                    f"SELECT {key},{column} FROM {table} WHERE scope_id=?", (scope,)
                ).fetchall():
                    old = json.loads(row[1] or "[]")
                    if not isinstance(old, list):
                        continue
                    new = [item for item in old if not isinstance(item, str) or item not in removed_refs]
                    if old == new:
                        continue
                    if table == "semantic_profiles" and not new:
                        db.execute("DELETE FROM semantic_profiles WHERE id=?", (row[0],))
                        continue
                    db.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (json.dumps(new), row[0]))
                    if not new and table in {
                        "family_person_memberships",
                        "family_relationships",
                        "family_person_portraits",
                    }:
                        db.execute(f"UPDATE {table} SET state='retracted' WHERE id=? AND locked=0", (row[0],))
                    elif not new and table == "semantic_claims":
                        db.execute(
                            "UPDATE semantic_claims SET status='retracted' "
                            "WHERE id=? AND confidence_source!='user'",
                            (row[0],),
                        )
                    elif not new and table == "person_portrait_revisions":
                        db.execute(
                            "UPDATE person_portrait_revisions SET status='retracted' "
                            "WHERE id=? AND trigger_type NOT LIKE 'user%'",
                            (row[0],),
                        )
            if events:
                for row in db.execute(
                    "SELECT id,supporting_event_ids_json FROM semantic_claims WHERE scope_id=?",
                    (scope,),
                ).fetchall():
                    old_events = json.loads(row[1] or "[]")
                    new_events = [event_id for event_id in old_events if event_id not in events]
                    if new_events != old_events:
                        db.execute(
                            "UPDATE semantic_claims SET supporting_event_ids_json=? WHERE id=?",
                            (json.dumps(new_events), row[0]),
                        )
            if faces:
                for table in (
                    "person_appearance_evidence",
                    "person_moments",
                    "face_prototypes",
                    "entity_mentions",
                ):
                    db.execute(f"DELETE FROM {table} WHERE face_instance_id IN ({f})", faces)
            if observations:
                for table in (
                    "person_appearance_evidence",
                    "person_moments",
                    "entity_mentions",
                    "entity_observations",
                    "event_observations",
                    "observation_search_terms",
                    "observation_search_fts",
                ):
                    db.execute(f"DELETE FROM {table} WHERE observation_id IN ({o})", observations)
                db.execute(
                    f"DELETE FROM text_vectors WHERE source_type='observation' AND source_id IN ({o})",
                    observations,
                )
                db.execute(
                    f"DELETE FROM memory_vectors WHERE source_type='observation' AND source_id IN ({o})",
                    observations,
                )
            if faces:
                db.execute(
                    f"DELETE FROM memory_vectors WHERE source_type='face_instance' AND source_id IN ({f})",
                    faces,
                )
                db.execute(
                    f"DELETE FROM text_vectors WHERE source_type='face_instance' AND source_id IN ({f})",
                    faces,
                )
                db.execute(f"DELETE FROM face_instances WHERE id IN ({f})", faces)
            for cluster_id in clusters:
                remaining = db.execute(
                    "SELECT embedding_json FROM face_instances WHERE cluster_id=? "
                    "ORDER BY quality DESC LIMIT 1",
                    (cluster_id,),
                ).fetchone()
                count = db.execute(
                    "SELECT COUNT(*) FROM face_instances WHERE cluster_id=?", (cluster_id,)
                ).fetchone()[0]
                if count:
                    db.execute(
                        "UPDATE face_clusters SET member_count=?,representative_embedding_json=? WHERE id=?",
                        (count, remaining[0], cluster_id),
                    )
                else:
                    # A user-confirmed identity is independent of one media item.
                    db.execute(
                        "UPDATE face_clusters SET member_count=0,representative_embedding_json='[]' "
                        "WHERE id=?",
                        (cluster_id,),
                    )
                    db.execute("DELETE FROM face_clusters WHERE id=? AND status!='confirmed'", (cluster_id,))
            real_events = tuple(events)
            if real_events:
                e = _slots(real_events)
                for table in ("person_moments", "event_participants", "event_entities", "event_observations"):
                    db.execute(f"DELETE FROM {table} WHERE event_id IN ({e})", real_events)
                db.execute(
                    f"DELETE FROM text_vectors WHERE source_type='event' AND source_id IN ({e})", real_events
                )
                db.execute(
                    f"DELETE FROM memory_vectors WHERE source_type='event' AND source_id IN ({e})",
                    real_events,
                )
                db.execute(f"DELETE FROM events WHERE id IN ({e})", real_events)
            surviving_events = tuple(affected_events - events)
            for event_id in surviving_events:
                db.execute(
                    "UPDATE events SET source_asset_id=NULL,source_type=NULL,source_scene_index=NULL,"
                    "source_start_sec=NULL,source_end_sec=NULL,source_metadata_json='{}' "
                    f"WHERE id=? AND source_asset_id IN ({a})",
                    (event_id, *assets),
                )
            db.execute(f"DELETE FROM memory_vectors WHERE source_type='asset' AND source_id IN ({a})", assets)
            db.execute(f"DELETE FROM text_vectors WHERE source_type='asset' AND source_id IN ({a})", assets)
            db.execute(f"DELETE FROM asset_places WHERE asset_id IN ({a})", assets)
            if observations:
                db.execute(f"DELETE FROM observations WHERE id IN ({o})", observations)
            db.execute(f"DELETE FROM assets WHERE id IN ({a})", assets)
            node_ids = tuple(
                f"{kind}:{source}"
                for kind, group in (
                    ("asset", assets),
                    ("observation", observations),
                    ("event", real_events),
                    ("fact", tuple(deleted_facts)),
                )
                for source in group
            )
            if observations:
                # Observation facts have digest IDs, so use the provenance attribute.
                node_ids += _ids(
                    db,
                    "SELECT id FROM memory_graph_nodes WHERE scope_id=? AND node_type='fact' "
                    f"AND json_extract(attributes_json,'$.observation_id') IN ({o})",
                    (scope, *observations),
                )
            if node_ids:
                n = _slots(node_ids)
                affected_entities: set[str] = set()
                for edge in db.execute(
                    "SELECT source_id,target_id FROM memory_graph_edges WHERE scope_id=? "
                    f"AND (source_id IN ({n}) OR target_id IN ({n}))",
                    (scope, *node_ids, *node_ids),
                ):
                    affected_entities.update(
                        node_id for node_id in edge if str(node_id).startswith("entity:")
                    )
                db.execute(
                    "DELETE FROM memory_graph_edges WHERE scope_id=? "
                    f"AND (source_id IN ({n}) OR target_id IN ({n}))",
                    (scope, *node_ids, *node_ids),
                )
                db.execute(
                    f"DELETE FROM memory_graph_nodes WHERE scope_id=? AND id IN ({n})", (scope, *node_ids)
                )
                for event_id in surviving_events:
                    row = db.execute(
                        "SELECT attributes_json FROM memory_graph_nodes WHERE id=?", (f"event:{event_id}",)
                    ).fetchone()
                    if row:
                        attrs = json.loads(row[0])
                        attrs["observation_ids"] = [
                            item for item in attrs.get("observation_ids", []) if item not in observations
                        ]
                        attrs["participant_entity_ids"] = list(
                            _ids(
                                db,
                                "SELECT DISTINCT person_id FROM event_participants WHERE event_id=?",
                                (event_id,),
                            )
                        )
                        attrs["event_entity_ids"] = list(
                            _ids(
                                db,
                                "SELECT DISTINCT entity_id FROM event_entities WHERE event_id=?",
                                (event_id,),
                            )
                        )
                        if attrs.get("source_asset_id") in assets:
                            attrs["source_asset_id"] = ""
                            attrs["source_type"] = None
                            attrs["source_scene_index"] = None
                        db.execute(
                            "UPDATE memory_graph_nodes SET attributes_json=? WHERE id=?",
                            (json.dumps(attrs), f"event:{event_id}"),
                        )
                for fact_id in changed_facts - deleted_facts:
                    row = db.execute("SELECT evidence_ids_json FROM facts WHERE id=?", (fact_id,)).fetchone()
                    node = db.execute(
                        "SELECT attributes_json FROM memory_graph_nodes WHERE id=?", (f"fact:{fact_id}",)
                    ).fetchone()
                    if row and node:
                        attrs = json.loads(node[0])
                        attrs["evidence_ids"] = json.loads(row[0])
                        db.execute(
                            "UPDATE memory_graph_nodes SET attributes_json=? WHERE id=?",
                            (json.dumps(attrs), f"fact:{fact_id}"),
                        )
                # Remove stale evidence payloads and edges whose only support
                # was this media. Relationship edges refer to the source row ID.
                for edge in db.execute(
                    "SELECT id,source_id,target_id,relation,attributes_json "
                    "FROM memory_graph_edges WHERE scope_id=?",
                    (scope,),
                ).fetchall():
                    attrs = json.loads(edge[4])
                    if (
                        edge[3] == "event_has_entity"
                        and str(edge[1]).startswith("event:")
                        and str(edge[2]).startswith("entity:")
                        and str(edge[1])[6:] in surviving_events
                    ):
                        event_id = str(edge[1])[6:]
                        entity_id = str(edge[2])[7:]
                        supported = db.execute(
                            "SELECT 1 FROM event_participants WHERE event_id=? AND person_id=? "
                            "UNION SELECT 1 FROM event_entities WHERE event_id=? AND entity_id=?",
                            (event_id, entity_id, event_id, entity_id),
                        ).fetchone()
                        if not supported:
                            db.execute("DELETE FROM memory_graph_edges WHERE id=?", (edge[0],))
                            affected_entities.add(str(edge[2]))
                            continue
                    if attrs.get("relationship_id") in deleted_relationships:
                        db.execute("DELETE FROM memory_graph_edges WHERE id=?", (edge[0],))
                        continue
                    old = attrs.get("evidence_ids")
                    if not isinstance(old, list):
                        continue
                    new = [item for item in old if not isinstance(item, str) or item not in removed_refs]
                    if new == old:
                        continue
                    if not new:
                        db.execute("DELETE FROM memory_graph_edges WHERE id=?", (edge[0],))
                    else:
                        attrs["evidence_ids"] = new
                        db.execute(
                            "UPDATE memory_graph_edges SET attributes_json=? WHERE id=?",
                            (json.dumps(attrs), edge[0]),
                        )
                for entity_id in affected_entities:
                    if not db.execute(
                        "SELECT 1 FROM memory_graph_edges WHERE scope_id=? "
                        "AND (source_id=? OR target_id=?) LIMIT 1",
                        (scope, entity_id, entity_id),
                    ).fetchone():
                        db.execute(
                            "DELETE FROM memory_graph_nodes WHERE scope_id=? AND id=?", (scope, entity_id)
                        )
                db.execute(
                    "DELETE FROM memory_graph_nodes WHERE scope_id=? AND node_type IN "
                    "('concept','object_hub','relation_hub','place_context','date_context') "
                    "AND id NOT IN (SELECT source_id FROM memory_graph_edges WHERE scope_id=? "
                    "UNION SELECT target_id FROM memory_graph_edges WHERE scope_id=?)",
                    (scope, scope, scope),
                )
                db.execute(
                    "UPDATE memory_graph_builds SET "
                    "node_count=(SELECT COUNT(*) FROM memory_graph_nodes WHERE scope_id=?), "
                    "edge_count=(SELECT COUNT(*) FROM memory_graph_edges WHERE scope_id=?) WHERE scope_id=?",
                    (scope, scope, scope),
                )
            db.execute(
                "INSERT INTO media_cleanup_jobs"
                "(asset_id,asset_ids_json,paths_json,sources_json,scope_id,file_name,media_type,deleted_at) "
                "VALUES(?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(asset_id) DO UPDATE SET asset_ids_json=excluded.asset_ids_json, "
                "paths_json=excluded.paths_json,sources_json=excluded.sources_json,scope_id=excluded.scope_id,"
                "file_name=excluded.file_name,media_type=excluded.media_type,deleted_at=excluded.deleted_at,"
                "files_done=0,vectors_done=0,last_error=''",
                (
                    asset_id,
                    json.dumps(assets),
                    json.dumps(paths),
                    json.dumps(vector_sources),
                    scope,
                    str(root[2]),
                    str(root[3]),
                ),
            )
            return True

    def list_deletions(self, *, scope_id: str | None, limit: int) -> Sequence[MediaDeletionRecord]:
        with self._database.connect() as db:
            rows = db.execute(
                "SELECT asset_id,scope_id,file_name,media_type,deleted_at,asset_ids_json,"
                "files_done,vectors_done,attempts,last_error FROM media_cleanup_jobs "
                "WHERE (? IS NULL OR scope_id=?) ORDER BY deleted_at DESC, rowid DESC LIMIT ?",
                (scope_id, scope_id, limit),
            ).fetchall()
        return tuple(
            MediaDeletionRecord(
                asset_id=str(row[0]),
                scope_id=str(row[1]),
                file_name=str(row[2]),
                media_type=str(row[3]),
                deleted_at=str(row[4]),
                asset_count=len(json.loads(row[5])),
                files_done=bool(row[6]),
                vectors_done=bool(row[7]),
                attempts=int(row[8]),
                last_error=str(row[9]),
            )
            for row in rows
        )

    def pending_cleanup(
        self, asset_id: str | None = None
    ) -> Sequence[tuple[str, tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...], bool, bool]]:
        with self._database.connect() as db:
            rows = db.execute(
                "SELECT asset_id,asset_ids_json,paths_json,sources_json,files_done,vectors_done "
                "FROM media_cleanup_jobs WHERE (files_done=0 OR vectors_done=0) "
                "AND (? IS NULL OR asset_id=?) ORDER BY updated_at LIMIT 100",
                (asset_id, asset_id),
            ).fetchall()
        return tuple(
            (
                str(r[0]),
                tuple(json.loads(r[1])),
                tuple(json.loads(r[2])),
                tuple((str(pair[0]), str(pair[1])) for pair in json.loads(r[3])),
                bool(r[4]),
                bool(r[5]),
            )
            for r in rows
        )

    def mark_cleanup(self, asset_id: str, *, files_done: bool, vectors_done: bool, error: str) -> None:
        with self._database.connect() as db, db:
            db.execute(
                "UPDATE media_cleanup_jobs SET files_done=?,vectors_done=?,last_error=?,"
                "attempts=attempts+1,updated_at=datetime('now') WHERE asset_id=?",
                (int(files_done), int(vectors_done), error, asset_id),
            )

    def was_deleted(self, asset_id: str) -> bool:
        with self._database.connect() as db:
            return (
                db.execute("SELECT 1 FROM media_cleanup_jobs WHERE asset_id=?", (asset_id,)).fetchone()
                is not None
            )
