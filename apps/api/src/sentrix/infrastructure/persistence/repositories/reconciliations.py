"""Checkpointed evidence withdrawal. Retains every source row, vector and file."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sentrix.application.media.analyze_deletion import DependencyAnalysis, Row, parse_json
from sentrix.application.media.withdraw_evidence import encode_key, withdraw
from sentrix.domain.identity.extraction import EVENT_RELATIONS
from sentrix.domain.media.capture_time import time_sort_key
from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.reconciliation_support import mark_state, rebuild_cluster
from sentrix.infrastructure.persistence.repositories.deletion_plans import SqliteDeletionPlanRepository

STEPS = ("evidence", "identities", "events", "derived")
JSON_FIELDS = {
    "facts": "evidence_ids_json",
    "relationships": "evidence_ids_json",
    "entity_properties": "evidence_ids_json",
    "semantic_claims": "evidence_ids_json",
    "event_participants": "evidence_ids_json",
    "event_entities": "evidence_ids_json",
    "family_relationships": "evidence_refs_json",
    "family_person_memberships": "evidence_refs_json",
}
BRIDGES = {"entity_mentions", "entity_observations", "event_observations"}
DERIVED = {
    "person_portrait_revisions",
    "semantic_profiles",
    "family_person_portraits",
    "memory_graph_nodes",
    "memory_graph_edges",
    "memory_graph_builds",
    "memory_rotation_sequences",
    "person_moments",
    "person_appearance_evidence",
}
HUMAN = {"user", "manual", "user_override", "user-confirmed", "user_confirmed"}
HANDLED = set(JSON_FIELDS) | BRIDGES | DERIVED | {"entities", "events", "face_clusters", "face_prototypes"}


def human_independent(row: Row, field: str | None) -> bool:
    if row.get("source") not in HUMAN and row.get("confidence_source") not in HUMAN:
        return False
    if field is None:
        return False
    parsed, valid = parse_json(row.get(field, "[]"))
    return valid and parsed == []


class SqliteReconciliationRepository:
    def __init__(
        self,
        database: Database,
        *,
        plans: SqliteDeletionPlanRepository | None = None,
        fault: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.plans = plans or SqliteDeletionPlanRepository(database)
        self.fault = fault

    def get(self, deletion_id: str, scope_id: str) -> Row:
        with self.database.connect() as c:
            if (
                c.execute(
                    "SELECT 1 FROM media_deletions WHERE deletion_id=? AND scope_id=?",
                    (deletion_id, scope_id),
                ).fetchone()
                is None
            ):
                raise DeletionNotFound("deletion task not found in this scope")
            row = c.execute(
                "SELECT * FROM media_reconciliations WHERE deletion_id=?", (deletion_id,)
            ).fetchone()
            if row is None:
                raise DeletionNotFound("reconciliation has not started")
            steps = [
                {"step": r["step"], "result": json.loads(r["result_json"]), "completed_at": r["completed_at"]}
                for r in c.execute(
                    "SELECT * FROM media_reconciliation_steps WHERE deletion_id=? ORDER BY completed_at,step",
                    (deletion_id,),
                )
            ]
            result = dict(row)
            result["steps"] = steps
            result["plan_history"] = json.loads(result.pop("plan_history_json"))
            workset = json.loads(result.pop("workset_json"))
            result["workset_size"] = len(workset)
            result["objects"] = []
            for item in workset:
                state = c.execute(
                    "SELECT * FROM media_object_states WHERE object_table=? AND object_key=?",
                    (item["table"], encode_key(item["key"])),
                ).fetchone()
                if state:
                    obj = dict(state)
                    obj["metrics"] = json.loads(obj.pop("metrics_json"))
                    result["objects"].append(obj)
            result["physical_deletion_complete"] = False
            return result

    def execute(self, deletion_id: str, scope_id: str) -> Row:
        # Physical 3C cleanup may have removed sources after this run completed.
        with self.database.connect() as check:
            finished = check.execute(
                "SELECT 1 FROM media_reconciliations r JOIN media_deletions d ON d.deletion_id=r.deletion_id "
                "WHERE r.deletion_id=? AND d.scope_id=? AND r.status='reconciled'",
                (deletion_id, scope_id),
            ).fetchone()
            if finished:
                return self.get(deletion_id, scope_id)
        try:
            self._prepare(deletion_id, scope_id)
            while self._advance(deletion_id, scope_id):
                pass
        except Exception as error:
            with self.database.write_transaction() as c:
                c.execute(
                    (
                        "UPDATE media_reconciliations SET status='failed',error=?,updated_at=? WHERE "
                        "deletion_id=? AND status!='reconciled' "
                        "AND EXISTS (SELECT 1 FROM media_deletions d WHERE "
                        "d.deletion_id=media_reconciliations.deletion_id AND d.scope_id=?)"
                    ),
                    (f"{type(error).__name__}: {error}"[:1500], self._now(), deletion_id, scope_id),
                )
            raise
        return self.get(deletion_id, scope_id)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _validate(self, c: sqlite3.Connection, deletion_id: str, scope_id: str) -> tuple[Row, list[Row]]:
        task, members = self.plans._task(c, deletion_id, scope_id)
        for member in members:
            if (
                c.execute(
                    (
                        "SELECT 1 FROM media_tombstones WHERE kind='asset' AND object_id=? AND "
                        "scope_id=? AND deletion_id=? AND generation=?"
                    ),
                    (member["asset_id"], scope_id, deletion_id, member["generation"]),
                ).fetchone()
                is None
            ):
                raise InvalidDeletion("missing or mismatched asset tombstone")
            for table, kind in [("observations", "observation"), ("face_instances", "face_instance")]:
                for source in c.execute(f"SELECT id FROM {table} WHERE asset_id=?", (member["asset_id"],)):
                    if (
                        c.execute(
                            (
                                "SELECT 1 FROM media_tombstones WHERE kind=? AND object_id=? AND "
                                "deletion_id=? AND generation=?"
                            ),
                            (kind, source["id"], deletion_id, member["generation"]),
                        ).fetchone()
                        is None
                    ):
                        raise InvalidDeletion("source identity is not protected by the deletion generation")
        # A recursive cross-check must agree with the durable closure, not just trust the client/plan.
        if (
            c.execute("SELECT media_type FROM assets WHERE id=?", (task["root_asset_id"],)).fetchone()[0]
            == "video"
        ):
            current = {
                r[0]
                for r in c.execute(
                    (
                        "WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT a.id FROM assets a JOIN "
                        "tree t ON a.parent_asset_id=t.id) SELECT id FROM tree"
                    ),
                    (task["root_asset_id"],),
                )
            }
            if current != {m["asset_id"] for m in members}:
                raise InvalidDeletion("asset closure changed; isolation repair required")
        return task, members

    def _prepare(self, deletion_id: str, scope_id: str) -> None:
        with self.database.write_transaction() as c:
            task, members = self._validate(c, deletion_id, scope_id)
            old = c.execute(
                "SELECT * FROM media_reconciliations WHERE deletion_id=?", (deletion_id,)
            ).fetchone()
            if old and old["status"] == "reconciled":
                return
            # generate joins this transaction: no TOCTOU gap between planning and binding.
            plan = self.plans.generate(deletion_id, scope_id)
            fresh = [i for i in plan["impact"]["items"] if i["table"] in HANDLED]
            existing = json.loads(old["workset_json"]) if old else []
            work = {i["table"] + ":" + encode_key(i["key"]): i for i in existing}
            new = []
            for item in fresh:
                identity = item["table"] + ":" + encode_key(item["key"])
                if identity not in work:
                    new.append(item)
                work[identity] = item
            history = json.loads(old["plan_history_json"]) if old else []
            if plan["plan_id"] not in history:
                history.append(plan["plan_id"])
            now = self._now()
            if old:
                c.execute(
                    (
                        "UPDATE media_reconciliations SET "
                        "plan_id=?,workset_json=?,plan_history_json=?,status='running',error='',retry"
                        "_count=retry_count+?,updated_at=? WHERE deletion_id=?"
                    ),
                    (
                        plan["plan_id"],
                        json.dumps(list(work.values()), ensure_ascii=False),
                        json.dumps(history),
                        int(old["status"] == "failed"),
                        now,
                        deletion_id,
                    ),
                )
            else:
                c.execute(
                    (
                        "INSERT INTO "
                        "media_reconciliations(deletion_id,initial_plan_id,plan_id,status,workset_jso"
                        "n,plan_history_json,created_at,updated_at) VALUES (?,?,?,'running',?,?,?,?)"
                    ),
                    (
                        deletion_id,
                        plan["plan_id"],
                        plan["plan_id"],
                        json.dumps(list(work.values()), ensure_ascii=False),
                        json.dumps(history),
                        now,
                        now,
                    ),
                )
            with self.database.reconciliation(c):
                for item in new:
                    self._invalidate(c, item, deletion_id)
                c.execute(
                    (
                        "INSERT INTO media_scope_invalidations VALUES (?,?,?,?) ON "
                        "CONFLICT(scope_id) DO UPDATE SET "
                        "generation=media_scope_invalidations.generation+1,cutoff_at=excluded.cutoff_"
                        "at,last_deletion_id=excluded.last_deletion_id WHERE media_scope_invalidation"
                        "s.last_deletion_id!=excluded.last_deletion_id"
                    ),
                    (scope_id, 1, now, deletion_id),
                )
                mark_state(
                    c,
                    "memory_graph_builds",
                    {"scope_id": scope_id},
                    scope_id,
                    deletion_id,
                    "invalid",
                    "scope_graph_requires_rebuild",
                )

    @staticmethod
    def _row(c: sqlite3.Connection, item: Row) -> Row | None:
        table = item["table"]
        if table not in HANDLED:
            raise InvalidDeletion("unsupported reconciliation table")
        key = item["key"]
        columns = {r["name"] for r in c.execute(f'PRAGMA table_info("{table}")')}
        if not key or not set(key) <= columns:
            raise InvalidDeletion("invalid object primary key")
        where = " AND ".join(f'"{field}"=?' for field in key)
        row = c.execute(f'SELECT * FROM "{table}" WHERE {where}', tuple(key.values())).fetchone()
        return dict(row) if row else None

    def _invalidate(self, c: sqlite3.Connection, item: Row, deletion_id: str) -> None:
        row = self._row(c, item)
        if row is None:
            return
        table = item["table"]
        evidence_field = JSON_FIELDS.get(
            table, "evidence_refs_json" if table == "family_person_portraits" else None
        )
        if human_independent(row, evidence_field):
            return
        if table in JSON_FIELDS:
            if (
                item["target_evidence_count"] == 0
                and item["evidence_count_complete"]
                and item["remaining_valid_evidence_count"] > 0
            ):
                return
            validity = "needs_review"
        elif table in BRIDGES or table == "face_prototypes":
            return
        elif table == "face_clusters":
            validity = "needs_review"
        elif table == "entities":
            validity = "valid"
        elif table == "events":
            validity = "needs_review"
        elif table in DERIVED:
            validity = "invalid"
        else:
            return
        reason = "cross_scope_review" if item["cross_scope"] else "dependency_withdrawal_pending"
        mark_state(c, table, item["key"], item["scope_id"], deletion_id, validity, reason)

    def _advance(self, deletion_id: str, scope_id: str) -> bool:
        committed_step = None
        with self.database.write_transaction() as c:
            task, members = self._validate(c, deletion_id, scope_id)
            run = c.execute(
                "SELECT * FROM media_reconciliations WHERE deletion_id=?", (deletion_id,)
            ).fetchone()
            if run["status"] == "reconciled":
                return False
            step_index = run["step_index"]
            if step_index >= len(STEPS):
                c.execute(
                    (
                        "UPDATE media_reconciliations SET status='reconciled',error='',updated_at=? "
                        "WHERE deletion_id=?"
                    ),
                    (self._now(), deletion_id),
                )
                return False
            step = STEPS[step_index]
            current_plan = self.plans.generate(deletion_id, scope_id)
            work = {i["table"] + ":" + encode_key(i["key"]): i for i in json.loads(run["workset_json"])}
            added = []
            for item in current_plan["impact"]["items"]:
                identity = item["table"] + ":" + encode_key(item["key"])
                if item["table"] in HANDLED:
                    if identity not in work:
                        added.append(item)
                    work[identity] = item
            history = json.loads(run["plan_history_json"])
            if current_plan["plan_id"] not in history:
                history.append(current_plan["plan_id"])
            c.execute(
                (
                    "UPDATE media_reconciliations SET "
                    "plan_id=?,workset_json=?,plan_history_json=? WHERE deletion_id=?"
                ),
                (
                    current_plan["plan_id"],
                    json.dumps(list(work.values()), ensure_ascii=False),
                    json.dumps(history),
                    deletion_id,
                ),
            )
            with self.database.reconciliation(c):
                for item in added:
                    self._invalidate(c, item, deletion_id)
            run = c.execute(
                "SELECT * FROM media_reconciliations WHERE deletion_id=?", (deletion_id,)
            ).fetchone()
            items = list(work.values())
            snapshot = self.plans._snapshot(c)
            analysis = DependencyAnalysis(snapshot, {m["asset_id"] for m in members}, scope_id)
            # Recheck current ownership even for a retained work item whose exact
            # evidence was withdrawn in an earlier checkpoint.
            for item in items:
                current_row = self._row(c, item)
                if current_row is not None:
                    current_scope = analysis.scope_for(item["table"], current_row)
                    item["cross_scope"] = item["cross_scope"] or current_scope not in {None, scope_id}
                    item["scope_id"] = current_scope
            with self.database.reconciliation(c):
                result = getattr(self, "_" + step)(c, items, analysis, run)
                if self.fault:
                    self.fault(step, "before_commit")
                c.execute(
                    "INSERT INTO media_reconciliation_steps VALUES (?,?,?,?)",
                    (deletion_id, step, json.dumps(result, ensure_ascii=False), self._now()),
                )
                c.execute(
                    (
                        "UPDATE media_reconciliations SET "
                        "step_index=step_index+1,status='running',updated_at=? WHERE deletion_id=?"
                    ),
                    (self._now(), deletion_id),
                )
            committed_step = step
        if self.fault:
            self.fault(committed_step, "after_commit")
        return True

    def _audit(self, c: sqlite3.Connection, item: Row, field: str, removed: Row, run: sqlite3.Row) -> None:
        c.execute(
            "INSERT OR IGNORE INTO media_evidence_revocations VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run["deletion_id"],
                item["table"],
                encode_key(item["key"]),
                field,
                removed["kind"],
                removed["id"],
                json.dumps(removed["original"], ensure_ascii=False),
                run["plan_id"],
                self._now(),
            ),
        )

    def _evidence(
        self, c: sqlite3.Connection, items: list[Row], analysis: DependencyAnalysis, run: sqlite3.Row
    ) -> Row:
        changed = 0
        deferred = 0
        for item in items:
            table = item["table"]
            row = self._row(c, item)
            if row is None:
                continue
            if item["cross_scope"]:
                deferred += 1
                continue
            if table in BRIDGES:
                oid = str(row.get("observation_id", ""))
                if oid in analysis.affected["observation"]:
                    mark_state(
                        c,
                        table,
                        item["key"],
                        item["scope_id"],
                        run["deletion_id"],
                        "revoked",
                        "isolated_observation_link",
                    )
                    self._audit(
                        c, item, "observation_id", {"kind": "observation", "id": oid, "original": oid}, run
                    )
                    changed += 1
                continue
            if table not in JSON_FIELDS:
                continue
            field = JSON_FIELDS[table]
            if human_independent(row, field):
                continue
            outcome = withdraw(row[field], "observation", analysis)
            # Also withdraw explicit event evidence using its declared kind.
            updates: Row = {}
            if outcome.changed and outcome.kept is not None:
                updates[field] = json.dumps(outcome.kept, ensure_ascii=False)
                for removed in outcome.removed:
                    self._audit(c, item, field, removed, run)
            extra = None
            if table == "semantic_claims":
                extra = withdraw(row["supporting_event_ids_json"], "event", analysis)
                if extra.changed and extra.kept is not None:
                    updates["supporting_event_ids_json"] = json.dumps(extra.kept, ensure_ascii=False)
                    for removed in extra.removed:
                        self._audit(c, item, "supporting_event_ids_json", removed, run)
            if not updates:
                # Known healthy sources unrelated to this deletion remain untouched.
                continue
            changed += 1
            valid_count = outcome.valid_count + (extra.valid_count if extra else 0)
            unresolved = outcome.unresolved or bool(extra and extra.unresolved)
            validity = "needs_review" if valid_count or unresolved else "invalid"
            if table == "facts" or table == "relationships":
                updates["status"] = "pending" if validity == "needs_review" else "retracted"
            elif table in {"entity_properties", "semantic_claims"}:
                updates["status"] = "pending"
            if "revision" in row:
                updates["revision"] = int(row["revision"]) + 1
            if "updated_at" in row:
                updates["updated_at"] = self._now()
            where = " AND ".join(f'"{k}"=?' for k in item["key"])
            fields = ",".join(f'"{k}"=?' for k in updates)
            c.execute(
                f'UPDATE "{table}" SET {fields} WHERE {where}', (*updates.values(), *item["key"].values())
            )
            mark_state(
                c,
                table,
                item["key"],
                item["scope_id"],
                run["deletion_id"],
                validity,
                "evidence_changed_requires_reassessment",
                metrics={
                    "remaining_valid_evidence_count": valid_count,
                    "counts_complete": not unresolved,
                    "aggregate_confidence_reliable": False,
                    "aggregate_support_count_reliable": False,
                },
            )
        return {"changed_objects": changed, "cross_scope_deferred": deferred}

    def _identities(
        self, c: sqlite3.Connection, items: list[Row], analysis: DependencyAnalysis, run: sqlite3.Row
    ) -> Row:
        people = 0
        clusters = 0
        for item in items:
            if item["cross_scope"]:
                continue
            if item["table"] == "entities":
                eid = item["key"]["id"]
                observations = c.execute(
                    (
                        "SELECT DISTINCT observation_id FROM readable_entity_mentions WHERE "
                        "entity_id=? UNION SELECT DISTINCT observation_id FROM "
                        "readable_entity_observations WHERE entity_id=?"
                    ),
                    (eid, eid),
                ).fetchall()
                face_sources = c.execute(
                    "SELECT f.observation_id,f.asset_id FROM readable_face_instances f "
                    "JOIN face_clusters fc ON fc.id=f.cluster_id WHERE fc.entity_id=?",
                    (eid,),
                ).fetchall()
                oids = {r[0] for r in observations} | {
                    r["observation_id"] for r in face_sources if r["observation_id"]
                }
                assets = {
                    analysis.by_kind["observation"][oid]["asset_id"]
                    for oid in oids
                    if oid in analysis.by_kind["observation"]
                } | {r["asset_id"] for r in face_sources}
                mark_state(
                    c,
                    "entities",
                    item["key"],
                    item["scope_id"],
                    run["deletion_id"],
                    "valid",
                    "identity_preserved_model_summary_invalid",
                    metrics={"valid_observation_count": len(oids), "valid_asset_count": len(assets)},
                )
                people += 1
            elif item["table"] == "face_clusters":
                rebuild_cluster(c, item["key"]["id"], run["deletion_id"])
                clusters += 1
        return {"identities_updated": people, "clusters_updated": clusters}

    def _events(
        self, c: sqlite3.Connection, items: list[Row], analysis: DependencyAnalysis, run: sqlite3.Row
    ) -> Row:
        updated = 0
        empty = 0
        for item in items:
            if item["table"] != "events" or item["cross_scope"]:
                continue
            event = self._row(c, item)
            if event is None:
                continue
            eid = str(event["id"])
            rows = c.execute(
                (
                    "SELECT o.*,a.captured_at AS asset_captured_at FROM "
                    "readable_event_observations eo JOIN readable_observations o ON "
                    "o.id=eo.observation_id JOIN readable_assets a ON a.id=o.asset_id WHERE "
                    "eo.event_id=? ORDER BY o.id"
                ),
                (eid,),
            ).fetchall()
            remaining = [dict(r) for r in rows]
            times = [
                datetime.fromisoformat(r["asset_captured_at"] or r["captured_at"])
                for r in remaining
                if r["asset_captured_at"] or r["captured_at"]
            ]
            mixed = len({t.utcoffset() is None for t in times}) > 1
            place_values = {r["place"] for r in remaining if r["place"]}
            place = next(iter(place_values)) if len(place_values) == 1 else ""
            cover = next(
                (
                    r["asset_id"]
                    for r in sorted(
                        remaining,
                        key=lambda r: (
                            time_sort_key(datetime.fromisoformat(r["asset_captured_at"] or r["captured_at"]))
                            if r["asset_captured_at"] or r["captured_at"]
                            else "",
                            r["asset_id"],
                        ),
                    )
                ),
                None,
            )
            validity = "invalid" if not remaining else ("needs_review" if mixed else "valid")
            start = min(times, key=time_sort_key).isoformat() if times and not mixed else None
            end = max(times, key=time_sort_key).isoformat() if times and not mixed else None
            c.execute(
                (
                    "UPDATE events SET "
                    "time_start=?,time_end=?,place=?,admin_district=?,source_metadata_json=?,stat"
                    "us=?,revision=revision+1,updated_at=? WHERE id=?"
                ),
                (
                    start,
                    end,
                    place,
                    "",
                    json.dumps(
                        {
                            "cover_asset_id": cover,
                            "remaining_observation_ids": [r["id"] for r in remaining],
                            "summary_state": "invalid",
                        },
                        ensure_ascii=False,
                    ),
                    "active" if remaining else "invalid",
                    self._now(),
                    eid,
                ),
            )
            mark_state(
                c,
                "events",
                {"id": eid},
                item["scope_id"],
                run["deletion_id"],
                validity,
                "remaining_observations_recomputed_summary_invalid",
                metrics={
                    "valid_observation_count": len(remaining),
                    "valid_asset_count": len({r["asset_id"] for r in remaining}),
                    "cover_asset_id": cover,
                    "time_semantics_mixed": mixed,
                },
                structural_restore=True,
            )
            self._event_links(c, event, analysis, run)
            updated += 1
            empty += int(not remaining)
        return {"events_updated": updated, "empty_events": empty}

    def _event_links(
        self, c: sqlite3.Connection, event: Row, analysis: DependencyAnalysis, run: sqlite3.Row
    ) -> None:
        eid = event["id"]
        scope = event["scope_id"]
        # Structural presence is derived only from live confirmed mentions, never an old summary.
        proofs: dict[str, list[tuple[str, float]]] = {}
        for row in c.execute(
            (
                "SELECT m.entity_id,m.observation_id,m.confidence FROM "
                "readable_entity_mentions m JOIN readable_event_observations eo ON "
                "eo.observation_id=m.observation_id JOIN entities e ON e.id=m.entity_id "
                "WHERE eo.event_id=? AND e.status='confirmed' AND e.scope_id=?"
            ),
            (eid, scope),
        ):
            proofs.setdefault(row["entity_id"], []).append((row["observation_id"], float(row["confidence"])))
        for person, refs in proofs.items():
            existing = c.execute(
                (
                    "SELECT * FROM event_participants WHERE event_id=? AND person_id=? AND "
                    "role='visible_subject'"
                ),
                (eid, person),
            ).fetchone()
            identity = (
                existing["id"]
                if existing
                else f"participant_{uuid.uuid5(uuid.NAMESPACE_URL, str(eid) + ':' + person).hex}"
            )
            self._projection(
                c,
                "event_participants",
                {"id": identity},
                dict(existing) if existing else None,
                {"event_id": eid, "person_id": person, "role": "visible_subject"},
                refs,
                scope,
                analysis,
                run,
            )
        for row in c.execute("SELECT * FROM event_participants WHERE event_id=?", (eid,)).fetchall():
            if row["role"] != "visible_subject" or row["person_id"] not in proofs:
                mark_state(
                    c,
                    "event_participants",
                    {"id": row["id"]},
                    scope,
                    run["deletion_id"],
                    "needs_review" if row["role"] != "visible_subject" else "invalid",
                    "participant_no_longer_supported",
                )
        entity_proofs: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for row in c.execute(
            (
                "SELECT x.entity_id,x.observation_id,x.confidence,e.entity_type FROM "
                "readable_entity_observations x JOIN readable_event_observations eo ON "
                "eo.observation_id=x.observation_id JOIN entities e ON e.id=x.entity_id "
                "WHERE eo.event_id=? AND e.scope_id=?"
            ),
            (eid, scope),
        ):
            entity_proofs.setdefault(
                (row["entity_id"], EVENT_RELATIONS.get(row["entity_type"], "")), []
            ).append((row["observation_id"], float(row["confidence"])))
        for row in c.execute("SELECT * FROM event_entities WHERE event_id=?", (eid,)).fetchall():
            key = {k: row[k] for k in ["entity_id", "event_id", "relation"]}
            refs = entity_proofs.get((row["entity_id"], row["relation"]), [])
            if refs:
                self._projection(c, "event_entities", key, dict(row), {}, refs, scope, analysis, run)
            else:
                mark_state(
                    c,
                    "event_entities",
                    key,
                    scope,
                    run["deletion_id"],
                    "invalid",
                    "event_entity_no_remaining_observation",
                )

    def _projection(
        self,
        c: sqlite3.Connection,
        table: str,
        key: Row,
        old: Row | None,
        base: Row,
        refs: list[tuple[str, float]],
        scope: str,
        analysis: DependencyAnalysis,
        run: sqlite3.Row,
    ) -> None:
        oids = sorted({oid for oid, _ in refs})
        # Keep other deletion tasks' pending evidence in the source row. The read
        # projection exposes only live IDs, and each task audits its own subtraction.
        if old:
            outcome = withdraw(old["evidence_ids_json"], "observation", analysis)
            kept = outcome.kept if outcome.kept is not None else []
            if outcome.kept is None:
                mark_state(
                    c,
                    table,
                    key,
                    scope,
                    run["deletion_id"],
                    "needs_review",
                    "unparseable_structural_evidence",
                )
                return
            for removed in outcome.removed:
                self._audit(c, {"table": table, "key": key}, "evidence_ids_json", removed, run)
            for oid in oids:
                if oid not in kept:
                    kept.append(oid)
        else:
            kept = oids
        now = self._now()
        confidence = max(v for _, v in refs)
        if old:
            where = " AND ".join(f'"{k}"=?' for k in key)
            revision = ",revision=revision+1" if "revision" in old else ""
            c.execute(
                f"UPDATE {table} SET evidence_ids_json=?,confidence=?,updated_at=?{revision} WHERE {where}",
                (json.dumps(kept), confidence, now, *key.values()),
            )
        else:
            values = {
                **key,
                **base,
                "evidence_ids_json": json.dumps(kept),
                "confidence": confidence,
                "created_at": now,
                "updated_at": now,
            }
            c.execute(
                f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
        mark_state(
            c,
            table,
            key,
            scope,
            run["deletion_id"],
            "valid",
            "remaining_structural_evidence",
            summary_valid=True,
            metrics={
                "valid_observation_count": len(oids),
                "confidence_source": "remaining_link_confidence_max",
            },
            structural_restore=True,
        )

    def _derived(
        self, c: sqlite3.Connection, items: list[Row], analysis: DependencyAnalysis, run: sqlite3.Row
    ) -> Row:
        # Contents stay intact for audit. Persistent validity and read projections
        # already took effect atomically in prepare, before any partial withdrawal.
        invalidated = 0
        for item in items:
            if item["table"] not in DERIVED:
                continue
            row = self._row(c, item)
            if row is None or human_independent(
                row, "evidence_refs_json" if item["table"] == "family_person_portraits" else None
            ):
                continue
            mark_state(
                c,
                item["table"],
                item["key"],
                item["scope_id"],
                run["deletion_id"],
                "invalid",
                "dependent_content_requires_versioned_rebuild",
            )
            invalidated += 1
        return {
            "derived_objects_invalidated": invalidated,
            "models_called": 0,
            "source_rows_deleted": 0,
            "files_deleted": 0,
            "source_vectors_deleted": 0,
        }
