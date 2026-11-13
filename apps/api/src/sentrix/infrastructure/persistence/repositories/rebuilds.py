"""Leased, coalesced memory rebuilds. Inference outside SQLite; fenced atomic publication."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentrix.application.media.analyze_deletion import Row, digest
from sentrix.application.media.withdraw_evidence import encode_key
from sentrix.application.memories.build_graph import BuildMemoryGraph
from sentrix.application.memories.summarize_event import _parse_json_object
from sentrix.application.retrieval.index_observation_text import compose_text
from sentrix.domain.assistant.models import ChatMessage, ChatRole
from sentrix.domain.assistant.ports import ChatModel
from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion
from sentrix.domain.retrieval.ports import TextEmbedder, VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.cleanups import TERMINAL, SqliteCleanupRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.memory_graph import SqliteMemoryGraphRepository
from sentrix.infrastructure.persistence.repositories.participants import SqliteEventParticipantRepository
from sentrix.infrastructure.persistence.repositories.relationships import SqliteRelationshipRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

FINISHED = {"storage_cleaned", "storage_cleaned_with_retained"}


class StaleRebuild(RuntimeError):
    pass


class RebuildBlocked(InvalidDeletion):
    pass


class SqliteRebuildRepository:
    def __init__(
        self,
        database: Database,
        *,
        model: ChatModel | None = None,
        embedder: TextEmbedder | None = None,
        index: QdrantVectorIndex | None = None,
        data_dir: Path | None = None,
        fault: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.model = model
        self.embedder = embedder
        self.index = index
        self.data_dir = data_dir or database.path.parent
        self.fault = fault
        self.clock = clock
        self.cleanup = SqliteCleanupRepository(database, index=index, data_dir=self.data_dir)

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def request(self, did: str, scope: str) -> Row:
        with self.database.write_transaction() as c:
            task = c.execute(
                "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?", (did, scope)
            ).fetchone()
            if task is None:
                raise DeletionNotFound("deletion task not found in scope")
            self._prerequisite(c, did, scope)
            lifecycle = c.execute(
                "SELECT * FROM media_deletion_lifecycle WHERE deletion_id=?", (did,)
            ).fetchone()
            if lifecycle["state"] != "completed":
                c.execute(
                    "INSERT INTO media_rebuild_jobs(scope_id,updated_at) VALUES (?,?)"
                    " ON CONFLICT(scope_id) DO UPDATE SET status=CASE WHEN status='ru"
                    "nning' THEN status ELSE 'queued' END,next_attempt=0,updated_at=e"
                    "xcluded.updated_at",
                    (scope, self.now()),
                )
                c.execute(
                    "INSERT OR IGNORE INTO media_rebuild_members(deletion_id,scope_id) VALUES (?,?)",
                    (did, scope),
                )
                c.execute(
                    "UPDATE media_deletion_lifecycle SET state='rebuilding',error='',"
                    "updated_at=? WHERE deletion_id=?",
                    (self.now(), did),
                )
        return self.get(did, scope)

    def get(self, did: str, scope: str) -> Row:
        with self.database.connect() as c:
            task = c.execute(
                "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?", (did, scope)
            ).fetchone()
            if not task:
                raise DeletionNotFound("deletion task not found in scope")
            state = dict(
                c.execute("SELECT * FROM media_deletion_lifecycle WHERE deletion_id=?", (did,)).fetchone()
            )
            job = c.execute("SELECT * FROM media_rebuild_jobs WHERE scope_id=?", (scope,)).fetchone()
            state["job"] = dict(job) if job else None
            state["validation"] = json.loads(state.pop("validation_json"))
            state["total_deletion_complete"] = state["state"] == "completed"
            state["display_state"] = {
                "isolated": "删除中",
                "cleaning": "删除中",
                "rebuilding": "记忆更新中",
                "completed": "已完成",
                "failed": "处理失败",
                "blocked": "处理失败",
            }[state["state"]]
            return state

    def _prerequisite(self, c: sqlite3.Connection, did: str, scope: str) -> None:
        task = c.execute(
            "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?", (did, scope)
        ).fetchone()
        run = c.execute("SELECT * FROM media_reconciliations WHERE deletion_id=?", (did,)).fetchone()
        cleaned = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (did,)).fetchone()
        if not task or not run or run["status"] != "reconciled" or run["step_index"] != 4:
            raise RebuildBlocked("3B reconciliation incomplete")
        if not cleaned or cleaned["status"] not in FINISHED or not cleaned["sqlite_done"]:
            raise RebuildBlocked("3C cleanup incomplete or blocked")
        if not json.loads(cleaned["validation_json"]).get("storage_cleaned"):
            raise RebuildBlocked("3C validation missing")
        members = list(c.execute("SELECT * FROM media_deletion_members WHERE deletion_id=?", (did,)))
        if not members:
            raise RebuildBlocked("missing durable deletion closure")
        for member in members:
            if not c.execute(
                "SELECT 1 FROM media_tombstones WHERE kind='asset' AND object_id="
                "? AND deletion_id=? AND scope_id=? AND generation=?",
                (member["asset_id"], did, scope, member["generation"]),
            ).fetchone():
                raise RebuildBlocked("missing valid tombstone")
        for resource in c.execute("SELECT * FROM media_cleanup_resources WHERE deletion_id=?", (did,)):
            if resource["status"] not in TERMINAL:
                raise RebuildBlocked("unresolved external cleanup resource")
        if (
            c.execute(
                "SELECT count(*) FROM media_reconciliation_steps WHERE deletion_id=?", (did,)
            ).fetchone()[0]
            != 4
        ):
            raise RebuildBlocked("3B checkpoint proof incomplete")
        manifest = json.loads(cleaned["manifest_json"])
        if (
            Path(manifest["source_versions"]["location_config"]["data_dir"]).resolve()
            != Path(self.data_dir).resolve()
        ):
            raise RebuildBlocked("managed storage configuration changed")
        self.cleanup._tombstones(c, did, scope, manifest)
        for kind, table in [
            ("asset", "assets"),
            ("observation", "observations"),
            ("face_instance", "face_instances"),
        ]:
            for identity in manifest["targets"][kind]:
                if c.execute(f"SELECT 1 FROM {table} WHERE id=?", (identity,)).fetchone():
                    raise RebuildBlocked("deleted source still exists")
        if c.execute(
            "SELECT 1 FROM media_object_states WHERE (scope_id=? OR last_dele"
            "tion_id=?) AND reason='cross_scope_review'",
            (scope, did),
        ).fetchone():
            raise RebuildBlocked("cross-scope dependencies require explicit resolution")

    def _scope_ready(self, c: sqlite3.Connection, scope: str) -> list[str]:
        ids = [r[0] for r in c.execute("SELECT deletion_id FROM media_deletions WHERE scope_id=?", (scope,))]
        for did in ids:
            self._prerequisite(c, did, scope)
        return ids

    def _snapshot(self, c: sqlite3.Connection, scope: str) -> Row:
        def rows(table: str, where: str = "scope_id=?", params: tuple[Any, ...] | None = None) -> list[Row]:
            return [
                dict(r)
                for r in c.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY 1", params or (scope,))
            ]

        assets = rows("readable_assets")
        obs = rows("readable_observations")
        if len(assets) > 100000 or len(obs) > 100000:
            raise RebuildBlocked("scope exceeds validated projection read limit")
        oids = {o["id"] for o in obs}
        entities = rows("entities")
        for e in entities:
            e.pop("summary", None)
            e.pop("confidence", None)
            e.pop("updated_at", None)
        bridges = {}
        for table in ["entity_mentions", "entity_observations", "event_observations"]:
            bridges[table] = [
                dict(r) for r in c.execute(f"SELECT * FROM readable_{table}") if r["observation_id"] in oids
            ]
        faces = [
            dict(r)
            for r in c.execute(
                "SELECT f.*,fc.entity_id FROM readable_face_instances f JOIN face"
                "_clusters fc ON fc.id=f.cluster_id"
            )
            if r["observation_id"] in oids or r["asset_id"] in {a["id"] for a in assets}
        ]
        event_ids = {r["event_id"] for r in bridges["event_observations"]}
        events = [
            dict(r)
            for r in c.execute("SELECT * FROM events WHERE scope_id=? ORDER BY id", (scope,))
            if r["id"] in event_ids
        ]
        for e in events:
            for key in [
                "title",
                "summary",
                "activity",
                "event_type",
                "confidence",
                "source_metadata_json",
                "aggregation_score",
                "aggregation_breakdown_json",
                "updated_at",
                "revision",
            ]:
                e.pop(key, None)
        states = rows("media_object_states")
        # Scope graph invalidation is coarse; only affected identities need model calls.
        affected = {s["object_table"] + ":" + s["object_key"] for s in states}
        people = {json.loads(s["object_key"]).get("id") for s in states if s["object_table"] == "entities"}
        profile_ids = {
            json.loads(s["object_key"]).get("id") for s in states if s["object_table"] == "semantic_profiles"
        }
        profile_people = {r["person_id"] for r in rows("semantic_profiles") if r["id"] in profile_ids}
        people |= profile_people
        locks = rows("person_portrait_revisions", "scope_id=? AND status='user_locked'")
        manuals = rows(
            "family_person_portraits",
            "scope_id=? AND (locked=1 OR source IN ('user','manual','user_override'))",
        )
        manual_props = [
            dict(r)
            for r in c.execute(
                "SELECT p.* FROM readable_entity_properties p JOIN entities e ON "
                "e.id=p.entity_id WHERE e.scope_id=? AND p.source IN ('user','man"
                "ual','user_override')",
                (scope,),
            )
        ]
        # Bodies of old summaries, portraits, claims and graph nodes never enter model input.
        return {
            "scope_id": scope,
            "assets": assets,
            "observations": obs,
            "entities": entities,
            "faces": faces,
            "events": events,
            **bridges,
            "affected": sorted(affected),
            "people": sorted(p for p in people if p),
            "locks": [
                {k: r[k] for k in ["id", "person_id", "revision", "status", "updated_at"]} for r in locks
            ],
            "manuals": manuals,
            "manual_properties": manual_props,
        }

    def _claim(self, scope: str | None) -> Row | None:
        with self.database.write_transaction() as c:
            query = (
                "SELECT * FROM media_rebuild_jobs WHERE (status IN ('queued','fai"
                "led') OR (status='running' AND lease_until<?)) AND next_attempt<"
                "=?"
            )
            params: list[Any] = [self.clock(), self.clock()]
            if scope:
                query += " AND scope_id=?"
                params.append(scope)
            job = c.execute(query + " ORDER BY updated_at LIMIT 1", params).fetchone()
            if not job:
                return None
            scope = job["scope_id"]
            try:
                if job["publish_done"]:
                    for member in c.execute(
                        "SELECT deletion_id FROM media_rebuild_members WHERE scope_id=?", (scope,)
                    ).fetchall():
                        self.cleanup._verify(member[0], scope)
                ids = self._scope_ready(c, scope)
            except RebuildBlocked as error:
                self._failure(c, scope, "failed" if job["publish_done"] else "blocked", str(error))
                return None
            epoch = c.execute(
                "SELECT generation FROM media_scope_epochs WHERE scope_id=?", (scope,)
            ).fetchone()
            if not epoch:
                raise RebuildBlocked("scope generation missing")
            snapshot = self._snapshot(c, scope)
            fingerprint = digest(snapshot)
            published = bool(
                job["publish_done"] and job["generation"] == epoch[0] and job["snapshot_hash"] == fingerprint
            )
            fence = job["fence"] + 1
            c.execute(
                "UPDATE media_rebuild_jobs SET status='running',generation=?,fenc"
                "e=?,lease_until=?,publish_done=?,retry_count=retry_count+?,snaps"
                "hot_hash=?,affected_json=?,error='',updated_at=? WHERE scope_id="
                "?",
                (
                    epoch[0],
                    fence,
                    self.clock() + 90,
                    int(published),
                    int(job["status"] in {"failed", "blocked", "running"}),
                    fingerprint,
                    json.dumps(snapshot["affected"]),
                    self.now(),
                    scope,
                ),
            )
            for did in ids:
                c.execute(
                    "INSERT OR IGNORE INTO media_rebuild_members(deletion_id,scope_id) VALUES (?,?)",
                    (did, scope),
                )
            return {
                "scope": scope,
                "generation": epoch[0],
                "fence": fence,
                "snapshot": snapshot,
                "snapshot_hash": fingerprint,
                "ids": ids,
                "published": published,
            }

    def _alive(self, c: sqlite3.Connection, claim: Row) -> None:
        row = c.execute(
            "SELECT j.*,e.generation AS current FROM media_rebuild_jobs j JOI"
            "N media_scope_epochs e ON e.scope_id=j.scope_id WHERE j.scope_id"
            "=?",
            (claim["scope"],),
        ).fetchone()
        if (
            not row
            or row["fence"] != claim["fence"]
            or row["current"] != claim["generation"]
            or row["status"] != "running"
        ):
            raise StaleRebuild("source generation or lease fence changed")
        self._scope_ready(c, claim["scope"])
        if digest(self._snapshot(c, claim["scope"])) != claim["snapshot_hash"]:
            raise StaleRebuild("source snapshot changed")
        c.execute(
            "UPDATE media_rebuild_jobs SET lease_until=? WHERE scope_id=?",
            (self.clock() + 90, claim["scope"]),
        )

    def _generate(self, claim: Row, table: str, key: Row, evidence: list[Row], identity: Row) -> Row:
        if not evidence:
            return {"summary": "", "observation_ids": []}
        raw = {
            "kind": table,
            "identity": identity,
            "observations": [
                {
                    k: o[k]
                    for k in [
                        "id",
                        "asset_id",
                        "caption",
                        "ocr_text",
                        "place",
                        "activity",
                        "objects_json",
                        "clothing_json",
                        "captured_at",
                    ]
                }
                for o in evidence
            ],
        }
        version = digest(
            {
                "input": raw,
                "prompt": "deletion-rebuild-v1",
                "model": getattr(self.model, "model_id", getattr(self.model, "model", "unknown")),
            }
        )
        with self.database.write_transaction() as c:
            self._alive(c, claim)
            cached = c.execute(
                "SELECT payload_json FROM media_rebuild_cache WHERE scope_id=? AN"
                "D object_table=? AND object_key=? AND evidence_hash=?",
                (claim["scope"], table, encode_key(key), version),
            ).fetchone()
            if cached:
                return dict(json.loads(cached[0]))
        if self.model is None:
            raise RuntimeError("rebuild model unavailable")
        reply = self.model.complete(
            (
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="仅依据给定原始观察生成简体中文记忆。禁止引用旧摘要、猜测身份或家庭关系。返回JSON：summary、title、event_t"
                    "ype、activity、observation_ids（仅使用输入id，非空）。实体画像不得把共现当亲属关系。",
                ),
                ChatMessage(role=ChatRole.USER, content=json.dumps(raw, ensure_ascii=False)),
            ),
            (),
        )
        result: Row = _parse_json_object(reply.content) or {}
        refs = result.get("observation_ids") if isinstance(result, dict) else None
        if (
            not isinstance(refs, list)
            or not refs
            or not set(refs) <= {o["id"] for o in evidence}
            or not isinstance(result.get("summary"), str)
            or not result["summary"].strip()
        ):
            raise ValueError("model returned ungrounded or empty rebuild")
        if table == "events" and (not isinstance(result.get("title"), str) or not result["title"].strip()):
            raise ValueError("event title missing")
        result["cited_observation_ids"] = refs
        result["observation_ids"] = sorted(o["id"] for o in evidence)
        with self.database.write_transaction() as c:
            self._alive(c, claim)
            c.execute(
                "INSERT OR IGNORE INTO media_rebuild_cache VALUES (?,?,?,?,?,?)",
                (
                    claim["scope"],
                    table,
                    encode_key(key),
                    version,
                    json.dumps(result, ensure_ascii=False),
                    self.now(),
                ),
            )
        return dict(result)

    def run_once(self, scope: str | None = None) -> bool:
        claim = self._claim(scope)
        if claim is None:
            return False
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(15):
                with self.database.write_transaction() as c:
                    c.execute(
                        "UPDATE media_rebuild_jobs SET lease_until=? WHERE scope_id=? AND"
                        " fence=? AND status='running'",
                        (self.clock() + 90, claim["scope"], claim["fence"]),
                    )

        keeper = threading.Thread(target=heartbeat, name="media-rebuild-lease", daemon=True)
        keeper.start()
        try:
            if not claim["published"]:
                prepared = self._prepare_outputs(claim)
                if self.fault:
                    self.fault("before_publish")
                self._publish(claim, prepared)
                if self.fault:
                    self.fault("after_publish")
            self._finish(claim)
        except Exception as error:
            with self.database.write_transaction() as c:
                current = c.execute(
                    "SELECT fence FROM media_rebuild_jobs WHERE scope_id=?", (claim["scope"],)
                ).fetchone()
                if current and current[0] == claim["fence"]:
                    self._failure(
                        c,
                        claim["scope"],
                        "queued"
                        if isinstance(error, StaleRebuild)
                        else "blocked"
                        if isinstance(error, RebuildBlocked)
                        else "failed",
                        f"{type(error).__name__}: {error}",
                    )
            return True
        finally:
            stopped.set()
            keeper.join(timeout=1)
        return True

    def _failure(self, c: sqlite3.Connection, scope: str, status: str, error: str) -> None:
        c.execute(
            "UPDATE media_rebuild_jobs SET status=?,error=?,lease_until=0,nex"
            "t_attempt=?,updated_at=? WHERE scope_id=?",
            (
                status,
                error[:1800],
                0 if status == "queued" else self.clock() + (30 if status == "blocked" else 5),
                self.now(),
                scope,
            ),
        )
        c.execute(
            "UPDATE media_deletion_lifecycle SET state=?,error=?,updated_at=?"
            " WHERE deletion_id IN (SELECT deletion_id FROM media_rebuild_mem"
            "bers WHERE scope_id=?) AND state!='completed'",
            ("rebuilding" if status == "queued" else status, error[:1800], self.now(), scope),
        )

    def _prepare_outputs(self, claim: Row) -> Row:
        snapshot = claim["snapshot"]
        obs = {o["id"]: o for o in snapshot["observations"]}
        event_refs: dict[str, set[str]] = {}
        for r in snapshot["event_observations"]:
            event_refs.setdefault(r["event_id"], set()).add(r["observation_id"])
        event_ids = {
            json.loads(key.split(":", 1)[1])["id"]
            for key in snapshot["affected"]
            if key.startswith("events:")
        }
        events = {e["id"]: dict(e) for e in snapshot["events"]}
        # Healthy imports paused during deletion receive deterministic new event identities.
        linked = set().union(*event_refs.values()) if event_refs else set()
        for oid, o in obs.items():
            if oid not in linked:
                eid = "rebuilt_event_" + uuid.uuid5(uuid.NAMESPACE_URL, claim["scope"] + ":" + oid).hex
                events[eid] = {
                    "id": eid,
                    "scope_id": claim["scope"],
                    "place": o["place"],
                    "time_start": o["captured_at"],
                    "time_end": o["captured_at"],
                    "new": True,
                }
                event_ids.add(eid)
                event_refs[eid] = {oid}
        event_outputs = {}
        for eid in sorted(event_ids):
            if eid not in events:
                continue
            evidence = [obs[oid] for oid in sorted(event_refs.get(eid, set()))]
            assets_by_id = {a["id"]: a for a in snapshot["assets"]}
            times = [
                datetime.fromisoformat(
                    str(assets_by_id[o["asset_id"]].get("captured_at") or o["captured_at"])
                )
                for o in evidence
                if assets_by_id[o["asset_id"]].get("captured_at") or o["captured_at"]
            ]
            if len({t.utcoffset() is None for t in times}) > 1:
                raise RebuildBlocked("event capture-time semantics require correction")
            events[eid]["time_start"] = min(times).isoformat() if times else None
            events[eid]["time_end"] = max(times).isoformat() if times else None
            places = {o["place"] for o in evidence if o["place"]}
            events[eid]["place"] = next(iter(places)) if len(places) == 1 else ""
            visible = {
                r["entity_id"]
                for r in snapshot["entity_mentions"]
                if r["observation_id"] in event_refs.get(eid, set())
            }
            events[eid]["participants"] = [
                {"entity_id": e["id"], "name": e["canonical_name"]}
                for e in snapshot["entities"]
                if e["id"] in visible and e["entity_type"] == "person" and e["status"] == "confirmed"
            ]
            event_outputs[eid] = {
                "identity": events[eid],
                "result": self._generate(claim, "events", {"id": eid}, evidence, events[eid]),
            }
        entity_refs: dict[str, set[str]] = {}
        for r in snapshot["entity_mentions"] + snapshot["entity_observations"]:
            entity_refs.setdefault(r["entity_id"], set()).add(r["observation_id"])
        for f in snapshot["faces"]:
            if f["entity_id"] and f["observation_id"] in obs:
                entity_refs.setdefault(f["entity_id"], set()).add(f["observation_id"])
        entity_outputs = {}
        for entity in snapshot["entities"]:
            if entity["id"] not in snapshot["people"]:
                continue
            evidence = [obs[oid] for oid in sorted(entity_refs.get(entity["id"], set()))]
            entity_outputs[entity["id"]] = {
                "identity": entity,
                "result": self._generate(claim, "entities", {"id": entity["id"]}, evidence, entity),
            }
        vectors = []
        source_obs = SqliteObservationRepository(self.database)
        if self.embedder is None and (obs or event_outputs):
            raise RuntimeError("rebuild text embedder unavailable")
        texts = []
        for oid in obs:
            record = source_obs.get(oid)
            if record:
                texts.extend(
                    [
                        (
                            space,
                            "observation",
                            oid,
                            compose_text(record),
                            {
                                "asset_id": record.asset_id,
                                "event_id": next(
                                    (eid for eid in sorted(event_refs) if oid in event_refs[eid]), ""
                                ),
                            },
                        )
                        for space in ["semantic", "episodic"]
                    ]
                )
        for eid, out in event_outputs.items():
            if out["result"]["observation_ids"]:
                texts.append(
                    (
                        "episodic",
                        "event",
                        eid,
                        out["result"]["summary"],
                        {"observation_id": out["result"]["observation_ids"][0]},
                    )
                )
        for space, kind, identity, text, metadata in texts:
            if not text:
                continue
            assert self.embedder is not None
            text_hash = digest(text)
            with self.database.connect() as c:
                existing = c.execute(
                    "SELECT * FROM text_vectors WHERE scope_id=? AND space=? AND sour"
                    "ce_type=? AND source_id=? AND model_name=?",
                    (claim["scope"], space, kind, identity, self.embedder.model_id),
                ).fetchone()
            if existing and json.loads(existing["metadata_json"]).get("text_digest") == text_hash:
                vector = tuple(float(v) for v in json.loads(existing["vector_json"]))
            else:
                cache_key = encode_key(
                    {
                        "model": self.embedder.model_id,
                        "dimension": self.embedder.dimension,
                    }
                )
                with self.database.write_transaction() as c:
                    self._alive(c, claim)
                    cached = c.execute(
                        "SELECT payload_json FROM media_rebuild_cache WHERE scope_id=? AN"
                        "D object_table='text_vector' AND object_key=? AND evidence_hash="
                        "?",
                        (claim["scope"], cache_key, text_hash),
                    ).fetchone()
                if cached:
                    vector = tuple(float(v) for v in json.loads(cached[0])["vector"])
                else:
                    vector = tuple(float(v) for v in self.embedder.embed_query(text))
                    if vector and len(vector) == self.embedder.dimension:
                        with self.database.write_transaction() as c:
                            self._alive(c, claim)
                            c.execute(
                                "INSERT OR IGNORE INTO media_rebuild_cache VALUES (?, 'text_vecto"
                                "r', ?, ?, ?, ?)",
                                (
                                    claim["scope"],
                                    cache_key,
                                    text_hash,
                                    json.dumps({"vector": vector}),
                                    self.now(),
                                ),
                            )
            if not vector or len(vector) != self.embedder.dimension:
                raise RuntimeError("invalid rebuild embedding")
            vectors.append(
                VectorRecord(
                    scope_id=claim["scope"],
                    space=space,
                    source_type=kind,
                    source_id=identity,
                    model_name=self.embedder.model_id,
                    vector=vector,
                    metadata={
                        **metadata,
                        "text_digest": text_hash,
                        "rebuild_generation": claim["generation"],
                        **({"source_revision": obs[identity]["revision"]} if kind == "observation" else {}),
                    },
                )
            )
        return {
            "events": event_outputs,
            "entities": entity_outputs,
            "vectors": vectors,
            "event_refs": event_refs,
            "entity_refs": entity_refs,
        }

    def _valid(self, c: sqlite3.Connection, table: str, key: Row, claim: Row) -> None:
        c.execute(
            "UPDATE media_object_states SET validity='valid',summary_valid=1,"
            "generation=generation+1,reason='rebuilt_from_current_evidence',u"
            "pdated_at=? WHERE object_table=? AND object_key=?",
            (self.now(), table, encode_key(key)),
        )
        c.execute(
            "INSERT INTO media_rebuild_publications VALUES (?,?,?,?) ON CONFL"
            "ICT(object_table,object_key) DO UPDATE SET generation=excluded.g"
            "eneration,scope_id=excluded.scope_id",
            (table, encode_key(key), claim["scope"], claim["generation"]),
        )

    def _publish(self, claim: Row, prepared: Row) -> None:
        with self.database.write_transaction() as c:
            self._alive(c, claim)
            with self.database.reconciliation(c):
                self._publish_summaries(c, claim, prepared)
                vector_repo = SqliteTextVectorRepository(self.database)
                for vector in prepared["vectors"]:
                    vector_repo.upsert(vector)
                # Remove obsolete event projection models, never raw observation/source vectors.
                for eid in prepared["events"]:
                    keep = {self.embedder.model_id} if self.embedder else set()
                    for row in c.execute(
                        "SELECT * FROM text_vectors WHERE scope_id=? AND source_type='event' AND source_id=?",
                        (claim["scope"], eid),
                    ).fetchall():
                        if row["model_name"] not in keep:
                            c.execute("DELETE FROM text_vectors WHERE id=?", (row["id"],))
                observations = SqliteObservationRepository(self.database)
                lexical = SqliteLexicalIndex(
                    self.database,
                    observations,
                    assets=SqliteAssetRepository(self.database),
                    places=SqliteAssetPlaceRepository(self.database),
                )
                for obs in observations.list_scope(claim["scope"], limit=100000):
                    lexical.refresh_from_observation(obs)
                self._graph(c, claim, prepared)
                if claim["snapshot"]["entities"]:
                    run_id = "family_rebuild_" + digest([claim["scope"], claim["generation"]])
                    stats = {
                        "people": len(prepared["entities"]),
                        "valid_observations": len(claim["snapshot"]["observations"]),
                        "event_watermark": c.execute(
                            "SELECT count(*) FROM readable_events WHERE scope_id=?", (claim["scope"],)
                        ).fetchone()[0],
                        "relationships": c.execute(
                            "SELECT count(*) FROM readable_family_relationships WHERE scope_id=?",
                            (claim["scope"],),
                        ).fetchone()[0],
                    }
                    c.execute(
                        "INSERT OR IGNORE INTO family_analysis_runs(id,scope_id,status,cu"
                        "rrent_stage,config_json,stats_json,created_at,updated_at,complet"
                        "ed_at) VALUES (?,?,'completed','deletion_rebuild',?,?,?,?,?)",
                        (
                            run_id,
                            claim["scope"],
                            json.dumps({"input_mode": "remaining_evidence_only"}),
                            json.dumps(stats),
                            self.now(),
                            self.now(),
                            self.now(),
                        ),
                    )
                for rotation in c.execute(
                    "SELECT * FROM memory_rotation_sequences WHERE scope_id=?", (claim["scope"],)
                ).fetchall():
                    valid = {
                        r[0]
                        for r in c.execute(
                            "SELECT id FROM readable_events WHERE scope_id=?", (claim["scope"],)
                        )
                    }
                    ids = [eid for eid in json.loads(rotation["event_ids_json"]) if eid in valid]
                    c.execute(
                        "UPDATE memory_rotation_sequences SET event_ids_json=?,position=?"
                        ",updated_at=? WHERE sequence_id=?",
                        (
                            json.dumps(ids),
                            min(rotation["position"], max(0, len(ids) - 1)),
                            self.now(),
                            rotation["sequence_id"],
                        ),
                    )
                    c.execute(
                        "DELETE FROM memory_rotation_viewed WHERE sequence_id=? AND event"
                        "_id NOT IN (SELECT id FROM readable_events)",
                        (rotation["sequence_id"],),
                    )
                    self._valid(
                        c, "memory_rotation_sequences", {"sequence_id": rotation["sequence_id"]}, claim
                    )
                for state in c.execute(
                    "SELECT * FROM media_object_states WHERE scope_id=?", (claim["scope"],)
                ).fetchall():
                    action = (
                        "rebuilt"
                        if state["validity"] == "valid" and state["summary_valid"]
                        else "invalidated_no_reliable_evidence"
                    )
                    if state["object_table"] == "person_portrait_revisions" and any(
                        lock["id"] == json.loads(state["object_key"]).get("id")
                        for lock in claim["snapshot"]["locks"]
                    ):
                        action = "locked_content_remains_invalid"
                    c.execute(
                        "INSERT INTO media_rebuild_dispositions VALUES (?,?,?,?,?,?) ON C"
                        "ONFLICT(scope_id,object_table,object_key) DO UPDATE SET generati"
                        "on=excluded.generation,action=excluded.action,evidence_json=excl"
                        "uded.evidence_json",
                        (
                            claim["scope"],
                            state["object_table"],
                            state["object_key"],
                            claim["generation"],
                            action,
                            json.dumps({"basis": "remaining_readable_sources_only"}),
                        ),
                    )
                # Publication itself can create validated structural bridges; capture its final epoch.
                epoch = c.execute(
                    "SELECT generation FROM media_scope_epochs WHERE scope_id=?", (claim["scope"],)
                ).fetchone()[0]
                claim["generation"] = epoch
                c.execute(
                    "UPDATE media_rebuild_publications SET generation=? WHERE scope_id=? AND generation=?",
                    (epoch, claim["scope"], claim["generation"]),
                )
                c.execute(
                    "UPDATE media_rebuild_dispositions SET generation=? WHERE scope_id=?",
                    (epoch, claim["scope"]),
                )
                for stale in c.execute(
                    "SELECT * FROM media_rebuild_publications WHERE scope_id=? AND generation!=?",
                    (claim["scope"], epoch),
                ).fetchall():
                    c.execute(
                        "INSERT INTO media_rebuild_dispositions VALUES (?,?,?,?,?,?) ON C"
                        "ONFLICT(scope_id,object_table,object_key) DO UPDATE SET generati"
                        "on=excluded.generation,action=excluded.action,evidence_json=excl"
                        "uded.evidence_json",
                        (
                            claim["scope"],
                            stale["object_table"],
                            stale["object_key"],
                            epoch,
                            "kept_invalid_old_generation",
                            json.dumps({"old_generation": stale["generation"]}),
                        ),
                    )
                claim["snapshot_hash"] = digest(self._snapshot(c, claim["scope"]))
                c.execute(
                    "UPDATE media_rebuild_jobs SET publish_done=1,generation=?,snapsh"
                    "ot_hash=?,lease_until=? WHERE scope_id=? AND fence=?",
                    (epoch, claim["snapshot_hash"], self.clock() + 90, claim["scope"], claim["fence"]),
                )

    def _publish_summaries(self, c: sqlite3.Connection, claim: Row, prepared: Row) -> None:
        scope = claim["scope"]
        now = self.now()
        for eid, out in prepared["events"].items():
            result = out["result"]
            identity = out["identity"]
            if not result["observation_ids"]:
                continue
            if identity.get("new"):
                c.execute(
                    "INSERT INTO events(id,scope_id,title,summary,event_type,activity"
                    ",place,time_start,time_end,created_at,updated_at) VALUES (?,?,?,"
                    "?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        scope,
                        result["title"],
                        result["summary"],
                        result.get("event_type", ""),
                        result.get("activity", ""),
                        identity.get("place", ""),
                        identity.get("time_start"),
                        identity.get("time_end"),
                        now,
                        now,
                    ),
                )
                for oid in result["observation_ids"]:
                    c.execute(
                        "INSERT OR IGNORE INTO event_observations(event_id,observation_id"
                        ",created_at) VALUES (?,?,?)",
                        (eid, oid, now),
                    )
            else:
                c.execute(
                    "UPDATE events SET title=?,summary=?,event_type=?,activity=?,time"
                    "_start=?,time_end=?,place=?,revision=revision+1,updated_at=? WHE"
                    "RE id=? AND scope_id=?",
                    (
                        result["title"],
                        result["summary"],
                        result.get("event_type", ""),
                        result.get("activity", ""),
                        identity.get("time_start"),
                        identity.get("time_end"),
                        identity.get("place", ""),
                        now,
                        eid,
                        scope,
                    ),
                )
            self._valid(c, "events", {"id": eid}, claim)
        for pid, out in prepared["entities"].items():
            result = out["result"]
            refs = result["observation_ids"]
            if not refs:
                c.execute("UPDATE entities SET summary='' WHERE id=? AND scope_id=?", (pid, scope))
                continue
            c.execute(
                "UPDATE entities SET summary=?,updated_at=? WHERE id=? AND scope_id=?",
                (result["summary"], now, pid, scope),
            )
            self._valid(c, "entities", {"id": pid}, claim)
            existing = c.execute("SELECT * FROM semantic_profiles WHERE person_id=?", (pid,)).fetchone()
            profile_id = (
                existing["id"]
                if existing
                else "profile_" + uuid.uuid5(uuid.NAMESPACE_URL, scope + ":" + pid).hex
            )
            c.execute(
                "INSERT INTO semantic_profiles(id,scope_id,person_id,summary_zh,e"
                "vidence_ids_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?) O"
                "N CONFLICT(person_id) DO UPDATE SET summary_zh=excluded.summary_"
                "zh,activity_summary_zh='',place_summary_zh='',appearance_summary"
                "_zh='',preference_summary_zh='',patterns_json='[]',event_memory_"
                "json='[]',evidence_ids_json=excluded.evidence_ids_json,revision="
                "semantic_profiles.revision+1,updated_at=excluded.updated_at",
                (profile_id, scope, pid, result["summary"], json.dumps(refs), now, now),
            )
            self._valid(c, "semantic_profiles", {"id": profile_id}, claim)
            if out["identity"]["entity_type"] == "person" and not any(
                lock["person_id"] == pid for lock in claim["snapshot"]["locks"]
            ):
                revision = c.execute(
                    "SELECT COALESCE(MAX(revision),0)+1 FROM person_portrait_revisions WHERE person_id=?",
                    (pid,),
                ).fetchone()[0]
                portrait_id = (
                    "rebuilt_portrait_"
                    + uuid.uuid5(uuid.NAMESPACE_URL, scope + ":" + pid + ":" + digest(result)).hex
                )
                if not c.execute(
                    "SELECT 1 FROM person_portrait_revisions WHERE id=?", (portrait_id,)
                ).fetchone():
                    c.execute(
                        "INSERT INTO person_portrait_revisions(id,scope_id,person_id,revi"
                        "sion,portrait_text,evidence_refs_json,trigger_type,model_name,pr"
                        "ompt_version,created_at,updated_at) VALUES (?,?,?,?,?,?,'deletio"
                        "n_rebuild','rebuild-model','deletion-rebuild-v1',?,?)",
                        (
                            portrait_id,
                            scope,
                            pid,
                            revision,
                            result["summary"],
                            json.dumps([{"kind": "observation", "id": oid} for oid in refs]),
                            now,
                            now,
                        ),
                    )
                self._valid(c, "person_portrait_revisions", {"id": portrait_id}, claim)
            if out["identity"]["entity_type"] == "person" and not any(
                m["entity_id"] == pid for m in claim["snapshot"]["manuals"]
            ):
                fid = (
                    "rebuilt_family_"
                    + uuid.uuid5(uuid.NAMESPACE_URL, scope + ":" + pid + ":" + digest(result)).hex
                )
                c.execute(
                    "INSERT OR IGNORE INTO family_person_portraits(id,scope_id,entity"
                    "_id,portrait_text,evidence_refs_json,source,created_at,updated_a"
                    "t) VALUES (?,?,?,?,?,'model',?,?)",
                    (
                        fid,
                        scope,
                        pid,
                        result["summary"],
                        json.dumps([{"kind": "observation", "id": oid} for oid in refs]),
                        now,
                        now,
                    ),
                )
                self._valid(c, "family_person_portraits", {"id": fid}, claim)

    def _graph(self, c: sqlite3.Connection, claim: Row, prepared: Row) -> None:
        scope = claim["scope"]
        generation = c.execute(
            "SELECT generation FROM media_scope_epochs WHERE scope_id=?", (scope,)
        ).fetchone()[0]
        input_generation = claim["generation"]
        claim["generation"] = generation
        c.execute(
            "UPDATE media_rebuild_publications SET generation=? WHERE scope_id=? AND generation=?",
            (generation, scope, input_generation),
        )
        graph = SqliteMemoryGraphRepository(self.database)
        # The existing graph vocabulary and retrieval contracts are preserved.
        # No causal model is configured; all input repositories expose only current sources.
        BuildMemoryGraph(
            assets=SqliteAssetRepository(self.database),
            observations=SqliteObservationRepository(self.database),
            events=SqliteEventRepository(self.database),
            participants=SqliteEventParticipantRepository(self.database),
            entities=SqliteEntityRepository(self.database),
            facts=SqliteFactRepository(self.database),
            relationships=SqliteRelationshipRepository(self.database),
            graph=graph,
            vectors=SqliteVectorRepository(self.database),
        ).execute(scope)
        for table in ["memory_graph_nodes", "memory_graph_edges"]:
            for row in c.execute(f"SELECT id FROM {table} WHERE scope_id=?", (scope,)).fetchall():
                self._valid(c, table, {"id": row[0]}, claim)
        self._valid(c, "memory_graph_builds", {"scope_id": scope}, claim)

    def _finish(self, claim: Row) -> None:
        # Refresh 3C proof, including external storage; never accept an old cached success.
        self._external_projections(claim)
        for did in claim["ids"]:
            self.cleanup._verify(did, claim["scope"])
            with self.database.connect() as c:
                for resource in c.execute(
                    "SELECT * FROM media_cleanup_resources WHERE deletion_id=? AND status='retained_shared'",
                    (did,),
                ):
                    locator = json.loads(resource["locator_json"])
                    locator["deletion_id"] = did
                    if Path(locator["path"]).exists() and not self.cleanup._shared_file(c, locator):
                        raise RebuildBlocked("retained shared file has no current owner")
        with self.database.write_transaction() as c:
            self._alive(c, claim)
            scope = claim["scope"]
            generation = claim["generation"]
            if c.execute("PRAGMA foreign_key_check").fetchone():
                raise RebuildBlocked("foreign key violation")
            graph = c.execute(
                "SELECT 1 FROM readable_memory_graph_builds WHERE scope_id=?", (scope,)
            ).fetchone()
            if not graph:
                raise RebuildBlocked("current memory graph unavailable")
            missing = c.execute(
                "SELECT 1 FROM media_object_states s LEFT JOIN media_rebuild_disp"
                "ositions d ON d.scope_id=s.scope_id AND d.object_table=s.object_"
                "table AND d.object_key=s.object_key AND d.generation=? WHERE s.s"
                "cope_id=? AND d.object_key IS NULL",
                (generation, scope),
            ).fetchone()
            if missing:
                raise RebuildBlocked("missing derived object disposition")
            for record in c.execute(
                "SELECT * FROM media_rebuild_index_cleanup WHERE scope_id=? AND g"
                "eneration=? AND status!='done'",
                (scope, generation),
            ).fetchall():
                locator = json.loads(record["locator_json"])
                assert self.index is not None
                self.index.delete_exact(locator)
                c.execute(
                    "UPDATE media_rebuild_index_cleanup SET status='done',error='' WH"
                    "ERE scope_id=? AND generation=? AND resource_key=?",
                    (scope, generation, record["resource_key"]),
                )
            # Opening the normal write gate is itself a new epoch. Inference
            # started while paused must never acquire write permission retroactively.
            published_generation = generation + 1
            c.execute(
                "UPDATE media_scope_epochs SET generation=?,paused=0 WHERE scope_id=?",
                (published_generation, scope),
            )
            c.execute(
                "UPDATE media_rebuild_publications SET generation=? WHERE scope_id=? AND generation=?",
                (published_generation, scope, generation),
            )
            c.execute(
                "UPDATE media_rebuild_dispositions SET generation=? WHERE scope_id=? AND generation=?",
                (published_generation, scope, generation),
            )
            generation = published_generation
            claim["generation"] = generation
            validation = {
                "safe": True,
                "generation": generation,
                "storage_revalidated": True,
                "graph_valid": True,
                "remaining_sources_only": True,
                "unresolved_safety_blocks": False,
            }
            c.execute("UPDATE media_scope_epochs SET paused=0 WHERE scope_id=?", (scope,))
            c.execute(
                "UPDATE scope_postprocess SET next_attempt_at=NULL WHERE scope_id"
                "=? AND processing_generation IS NULL",
                (scope,),
            )
            c.execute(
                "UPDATE media_rebuild_jobs SET status='completed',error='',lease_"
                "until=0,validation_json=?,updated_at=? WHERE scope_id=? AND fenc"
                "e=?",
                (json.dumps(validation), self.now(), scope, claim["fence"]),
            )
            c.execute("UPDATE media_rebuild_members SET generation=? WHERE scope_id=?", (generation, scope))
            for did in claim["ids"]:
                c.execute(
                    "UPDATE media_deletion_lifecycle SET state='completed',generation"
                    "=?,error='',validation_json=?,completed_at=COALESCE(completed_at"
                    ",?),updated_at=? WHERE deletion_id=?",
                    (generation, json.dumps(validation), self.now(), self.now(), did),
                )

    def _external_projections(self, claim: Row) -> None:
        if self.index is None:
            raise RuntimeError("Qdrant proof unavailable")
        with self.database.connect() as c:
            events = {
                json.loads(r[0])["id"]
                for r in c.execute(
                    "SELECT object_key FROM media_rebuild_publications WHERE scope_id"
                    "=? AND object_table='events'",
                    (claim["scope"],),
                )
            }
        for point in self.index.deletion_inventory():
            payload = point.get("payload", {})
            if (
                not isinstance(payload, dict)
                or payload.get("source_type") != "event"
                or payload.get("source_id") not in events
            ):
                continue
            if payload.get("scope_id") != claim["scope"] or not point.get("namespace_owned"):
                raise RebuildBlocked("ambiguous shared event projection")
            key = digest([point["collection"], point["point_id"]])
            with self.database.write_transaction() as c:
                self._alive(c, claim)
                c.execute(
                    "INSERT OR IGNORE INTO media_rebuild_index_cleanup VALUES (?,?,?,?,'pending','')",
                    (claim["scope"], claim["generation"], key, json.dumps(point)),
                )
                record = c.execute(
                    "SELECT * FROM media_rebuild_index_cleanup WHERE scope_id=? AND g"
                    "eneration=? AND resource_key=?",
                    (claim["scope"], claim["generation"], key),
                ).fetchone()
                locator = json.loads(record["locator_json"])
            try:
                self.index.delete_exact(locator)
            except Exception as error:
                with self.database.write_transaction() as c:
                    c.execute(
                        "UPDATE media_rebuild_index_cleanup SET status='failed',error=? W"
                        "HERE scope_id=? AND generation=? AND resource_key=?",
                        (str(error), claim["scope"], claim["generation"], key),
                    )
                raise
            with self.database.write_transaction() as c:
                self._alive(c, claim)
                c.execute(
                    "UPDATE media_rebuild_index_cleanup SET status='done',error='' WH"
                    "ERE scope_id=? AND generation=? AND resource_key=?",
                    (claim["scope"], claim["generation"], key),
                )
