"""Checkpointed 3C source cleanup. Shared memory and revocation ledgers survive."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.media.analyze_deletion import DependencyAnalysis, Row, digest, parse_json
from sentrix.application.media.withdraw_evidence import encode_key, withdraw
from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion
from sentrix.infrastructure.media.cleanup_files import (
    UnsafeMediaFile,
    file_identity,
    managed_relative,
    unlink_managed,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.deletion_plans import SqliteDeletionPlanRepository
from sentrix.infrastructure.persistence.repositories.reconciliations import (
    JSON_FIELDS,
    SqliteReconciliationRepository,
)
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

TERMINAL = {"done", "retained_shared", "retained_external"}
SOURCE_TABLES = {
    "asset": "assets",
    "observation": "observations",
    "face_instance": "face_instances",
    "event": "events",
}
ORDER = (
    "observation_search_fts",
    "observation_search_terms",
    "memory_vectors",
    "text_vectors",
    "entity_mentions",
    "entity_observations",
    "event_observations",
    "person_appearance_evidence",
    "person_moments",
    "face_prototypes",
    "event_participants",
    "event_entities",
    "face_instances",
    "observations",
    "asset_places",
    "video_job_stages",
    "video_jobs",
    "events",
    "assets",
)


class SqliteCleanupRepository:
    def __init__(
        self,
        database: Database,
        *,
        data_dir: Path | None = None,
        index: QdrantVectorIndex | None = None,
        fault: Callable[[str, str], None] | None = None,
        remove_file: Callable[[Path, Row], None] = unlink_managed,
    ) -> None:
        self.database = database
        self.data_dir = Path(data_dir or database.path.parent).absolute()
        self.index = index
        self.fault = fault
        self.remove_file = remove_file
        self.plans = SqliteDeletionPlanRepository(
            database,
            data_dir=self.data_dir,
            qdrant_inventory=index.deletion_inventory if index else None,
            qdrant_prefix=index.prefix if index else "sentrix_home",
        )

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def get(self, deletion_id: str, scope_id: str) -> Row:
        with self.database.connect() as c:
            self._authorize(c, deletion_id, scope_id)
            row = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (deletion_id,)).fetchone()
            if row is None:
                raise DeletionNotFound("storage cleanup has not started")
            result = dict(row)
            result["manifest"] = json.loads(result.pop("manifest_json"))
            result["validation"] = json.loads(result.pop("validation_json"))
            result["resources"] = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM media_cleanup_resources WHERE deletion_id=? ORDER BY kind,resource_id",
                    (deletion_id,),
                )
            ]
            for resource in result["resources"]:
                resource["locator"] = json.loads(resource.pop("locator_json"))
            result["steps"] = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM media_cleanup_steps WHERE deletion_id=? ORDER BY completed_at,step",
                    (deletion_id,),
                )
            ]
            result["total_deletion_complete"] = False
            return result

    @staticmethod
    def _authorize(c: sqlite3.Connection, deletion_id: str, scope: str) -> Row:
        row = c.execute(
            "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?", (deletion_id, scope)
        ).fetchone()
        if row is None:
            raise DeletionNotFound("deletion task not found in this scope")
        if row["status"] != "isolated":
            raise InvalidDeletion("cleanup requires an isolated deletion")
        run = c.execute("SELECT * FROM media_reconciliations WHERE deletion_id=?", (deletion_id,)).fetchone()
        if run is None or run["status"] != "reconciled" or run["step_index"] != 4:
            raise InvalidDeletion("3B reconciliation must finish before storage cleanup")
        return dict(row)

    def execute(self, deletion_id: str, scope_id: str) -> Row:
        try:
            self._prepare(deletion_id, scope_id)
            with self.database.connect() as check:
                done = check.execute(
                    "SELECT status FROM media_cleanups WHERE deletion_id=?", (deletion_id,)
                ).fetchone()
                if done[0] in {"storage_cleaned", "storage_cleaned_with_retained"}:
                    return self.get(deletion_id, scope_id)
            self._sqlite(deletion_id, scope_id)
            with self.database.connect() as c:
                run = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (deletion_id,)).fetchone()
                if not run["sqlite_done"]:
                    return self.get(deletion_id, scope_id)
            self._inventory(deletion_id, scope_id)
            self._resources(deletion_id, scope_id)
            self._verify(deletion_id, scope_id)
        except Exception as error:
            with self.database.write_transaction() as c:
                c.execute(
                    "UPDATE media_cleanups SET status='failed',error=?,updated_at=? WHERE deletion_id=? "
                    "AND EXISTS (SELECT 1 FROM media_deletions d WHERE d.deletion_id="
                    "media_cleanups.deletion_id AND d.scope_id=?)",
                    (f"{type(error).__name__}: {error}"[:1600], self.now(), deletion_id, scope_id),
                )
            raise
        return self.get(deletion_id, scope_id)

    def _prepare(self, did: str, scope: str) -> None:
        with self.database.write_transaction() as c:
            self._authorize(c, did, scope)
            old = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (did,)).fetchone()
            if old:
                self._tombstones(c, did, scope, json.loads(old["manifest_json"]))
                if old["status"] in {"failed", "blocked"}:
                    c.execute(
                        "UPDATE media_cleanups SET retry_count=retry_count+1,status='runn"
                        "ing',error='' WHERE deletion_id=?",
                        (did,),
                    )
                return
            SqliteReconciliationRepository(self.database)._validate(c, did, scope)
            plan = self.plans.generate(did, scope)
            snapshot = self.plans._snapshot(c)
            targets = set(plan["impact"]["asset_ids"])
            analysis = DependencyAnalysis(snapshot, targets, scope)
            manifest = self._manifest(c, snapshot, analysis, did, plan)
            now = self.now()
            c.execute(
                "INSERT INTO media_cleanups(deletion_id,plan_id,status,manifest_j"
                "son,created_at,updated_at) VALUES (?,?,'running',?,?,?)",
                (did, plan["plan_id"], json.dumps(manifest, ensure_ascii=False), now, now),
            )
            for item in plan["impact"]["files"]:
                self._file_resource(c, did, item, manifest)
            self._resource(c, did, "inventory", {"store": "qdrant"}, "pending")
            unsafe_inventory = [
                w
                for w in plan["impact"]["warnings"]
                if any(
                    text in w
                    for text in (
                        "directory_inventory_error",
                        "unmanaged_or_symlink_directory",
                        "unsafe_asset_directory_identity",
                    )
                )
            ]
            if unsafe_inventory:
                self._resource(
                    c,
                    did,
                    "inventory",
                    {"store": "filesystem", "warnings": unsafe_inventory},
                    "blocked",
                    "filesystem_inventory_requires_review",
                )
            self._step(c, did, "manifest", {"plan_id": plan["plan_id"], "blockers": manifest["blockers"]})

    def _manifest(
        self, c: sqlite3.Connection, snapshot: Row, analysis: DependencyAnalysis, did: str, plan: Row
    ) -> Row:
        targets = analysis.targets
        selected: dict[str, list[Row]] = {t: [] for t in ORDER}
        allrows = {t: [dict(r) for r in c.execute(f'SELECT rowid AS __rowid,* FROM "{t}"')] for t in ORDER}
        oids = {r["id"] for r in allrows["observations"] if r["asset_id"] in targets}
        fids = {r["id"] for r in allrows["face_instances"] if r["asset_id"] in targets}
        blockers = []
        # Event removal requires exact ownership and the invalidity computed by 3B.
        event_ids = set()
        for event in allrows["events"]:
            links = [r for r in allrows["event_observations"] if r["event_id"] == event["id"]]
            owned = bool(links or event["source_asset_id"]) and all(
                r["observation_id"] in oids for r in links
            )
            owned &= event["source_asset_id"] is None or event["source_asset_id"] in targets
            affected = event["source_asset_id"] in targets or any(r["observation_id"] in oids for r in links)
            state = c.execute(
                "SELECT validity FROM media_object_states WHERE object_table='events' AND object_key=?",
                (encode_key({"id": event["id"]}),),
            ).fetchone()
            if affected and owned and event["scope_id"] == analysis.scope and state and state[0] == "invalid":
                event_ids.add(event["id"])
            elif event["source_asset_id"] in targets:
                blockers.append(
                    {"table": "events", "id": event["id"], "reason": "shared_or_unproven_source_event"}
                )
        kinds = {
            "asset": sorted(targets),
            "observation": sorted(oids),
            "face_instance": sorted(fids),
            "event": sorted(event_ids),
        }
        jobs = {r["job_id"] for r in allrows["video_jobs"] if r["asset_id"] in targets}
        for table, rows in allrows.items():
            columns = [r["name"] for r in c.execute(f'PRAGMA table_info("{table}")') if r["pk"]]
            for row in rows:
                include = False
                if table in {
                    "assets",
                    "observations",
                    "face_instances",
                    "asset_places",
                    "person_appearance_evidence",
                    "person_moments",
                    "video_jobs",
                }:
                    include = (row["id"] if table == "assets" else row["asset_id"]) in targets
                elif table in {
                    "entity_mentions",
                    "entity_observations",
                    "event_observations",
                    "observation_search_terms",
                    "observation_search_fts",
                }:
                    include = row["observation_id"] in oids
                elif table == "face_prototypes":
                    include = row["face_instance_id"] in fids
                    if include:
                        state = c.execute(
                            "SELECT validity FROM media_object_states WHERE object_table='fac"
                            "e_prototypes' AND object_key=?",
                            (encode_key({"id": row["id"]}),),
                        ).fetchone()
                        if not state or state[0] == "valid":
                            blockers.append(
                                {"table": table, "id": row["id"], "reason": "prototype_not_reconciled"}
                            )
                elif table in {"event_participants", "event_entities"}:
                    include = row["event_id"] in event_ids
                elif table == "events":
                    include = row["id"] in event_ids
                elif table == "video_job_stages":
                    include = row["job_id"] in jobs
                elif table in {"memory_vectors", "text_vectors"}:
                    include, reason = self._vector_owner(row, kinds, analysis.scope, sqlite=True)
                    if reason:
                        blockers.append({"table": table, "id": row["id"], "reason": reason})
                if not include:
                    continue
                if table in {"face_instances", "person_appearance_evidence", "person_moments"} and (
                    (row.get("observation_id") and row["observation_id"] not in oids)
                    or (row.get("face_instance_id") and row["face_instance_id"] not in fids)
                ):
                    blockers.append({"table": table, "id": row["id"], "reason": "mixed_source_foreign_keys"})
                scope = analysis.scope_for(table, row)
                if scope not in {None, analysis.scope}:
                    blockers.append({"table": table, "reason": "cross_scope_source", "key": row.get("id")})
                key = {k: row[k] for k in columns} if columns else {"rowid": row["__rowid"]}
                selected[table].append({"key": key, "row": {k: v for k, v in row.items() if k != "__rowid"}})
        # Shared business JSON must already have had this task's exact evidence removed.
        for table, field in JSON_FIELDS.items():
            for row in snapshot.get(table, []):
                if table in {"event_participants", "event_entities"} and row["event_id"] in event_ids:
                    continue
                out = withdraw(row[field], "observation", analysis)
                extra = (
                    withdraw(row.get("supporting_event_ids_json", "[]"), "event", analysis)
                    if table == "semantic_claims"
                    else None
                )
                if out.changed or (extra and extra.changed):
                    blockers.append(
                        {"table": table, "key": row["_key"], "reason": "unwithdrawn_or_cross_scope_evidence"}
                    )
        # Unknown new foreign-key references are never handled by cascading deletion.
        deleted = {t: {encode_key(i["key"]) for i in items} for t, items in selected.items()}
        identities = {
            t: {str(i["row"]["id"]) for i in selected[t]}
            for t in ["assets", "observations", "face_instances", "events"]
        }
        identities["video_jobs"] = jobs
        for table_row in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            table = table_row[0]
            for fk in c.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                if fk["table"] not in identities:
                    continue
                for identity in identities[fk["table"]]:
                    refs = c.execute(
                        f'SELECT rowid AS __rowid,* FROM "{table}" WHERE "{fk["from"]}"=?', (identity,)
                    ).fetchall()
                    pks = [r["name"] for r in c.execute(f'PRAGMA table_info("{table}")') if r["pk"]]
                    for ref in refs:
                        key = {k: ref[k] for k in pks} if pks else {"rowid": ref["__rowid"]}
                        if encode_key(key) not in deleted.get(table, set()):
                            blockers.append(
                                {
                                    "table": table,
                                    "key": key,
                                    "reason": "retained_foreign_key_reference",
                                    "parent": fk["table"],
                                }
                            )
        return {
            "scope_id": analysis.scope,
            "deletion_id": did,
            "plan_id": plan["plan_id"],
            "plan_version": plan["version"],
            "targets": kinds,
            "rows": selected,
            "blockers": blockers,
            "files": plan["impact"]["files"],
            "source_versions": plan["source_versions"],
            "asset_ids": sorted(targets),
        }

    @staticmethod
    def _vector_owner(row: object, targets: Row, scope: str, *, sqlite: bool = False) -> tuple[bool, str]:
        if not isinstance(row, dict):
            return False, "unknown_vector_payload"
        metadata, valid = (
            parse_json(row.get("metadata_json", "{}")) if sqlite else (row.get("metadata", {}), True)
        )
        if not isinstance(metadata, dict):
            metadata, valid = {}, False
        kind, identity = str(row.get("source_type", "")), row.get("source_id")
        direct = identity in targets.get(kind, [])
        evidence = any(
            metadata.get(field) in targets[k]
            for field, k in [
                ("asset_id", "asset"),
                ("observation_id", "observation"),
                ("face_instance_id", "face_instance"),
            ]
        )
        candidate = direct or evidence
        if not candidate:
            return False, ""
        if row.get("scope_id") != scope:
            return False, "cross_scope_vector"
        if not valid or kind not in SOURCE_TABLES or not row.get("model_name") or not row.get("space"):
            return False, "unknown_vector_identity"
        conflicts = any(
            metadata.get(field) and metadata[field] not in targets[k]
            for field, k in [
                ("asset_id", "asset"),
                ("observation_id", "observation"),
                ("face_instance_id", "face_instance"),
            ]
        )
        if conflicts:
            return False, "mixed_vector_provenance"
        if direct or (kind == "event" and evidence):
            return True, ""
        return False, "unproven_vector_owner"

    def _file_resource(self, c: sqlite3.Connection, did: str, item: Row, manifest: Row) -> None:
        locator = dict(item)
        path = Path(item["path"])
        try:
            locator["stat"] = file_identity(path)
        except OSError as failure:
            locator["stat"] = None
            self._resource(c, did, "file", locator, "failed", f"inventory_stat_failed:{failure}")
            return
        status, reason = "pending", ""
        owners = set(item.get("owner_asset_ids", []))
        if "import_jobs.original_path" in item["sources"] and "asset.path" not in item["sources"]:
            status, reason = "retained_external", "external_import_source"
        else:
            try:
                relative = managed_relative(self.data_dir, path)
                # Managed location alone is not proof of ownership.
                name_owned = any(path.name.startswith(aid + "_") for aid in owners)
                if not name_owned:
                    for prior in c.execute(
                        "SELECT locator_json FROM media_cleanup_resources WHERE kind='file' "
                        "AND status='retained_shared' AND resolved_path=?",
                        (str(path.resolve()),),
                    ):
                        previous = json.loads(prior[0])
                        if previous.get("stat") == locator["stat"] and any(
                            path.name.startswith(aid + "_") for aid in previous.get("owner_asset_ids", [])
                        ):
                            name_owned = True
                structured = any(
                    source in item["sources"]
                    for source in ["asset_or_job_directory", "preview.asset_id-size"]
                )
                if not (name_owned or structured):
                    raise UnsafeMediaFile("unproven_managed_file_owner")
                if path.resolve() != Path(item["resolved_path"]) or path.is_symlink():
                    raise UnsafeMediaFile("symlink_or_changed_resolution")
                for parent in [path, *path.parents]:
                    if parent == self.data_dir:
                        break
                    if parent.is_symlink():
                        raise UnsafeMediaFile("symlink_component")
                locator["relative_path"] = str(relative)
            except (ValueError, OSError, RuntimeError) as error:
                # External originals are explicitly retained; unsafe managed paths block.
                if not path.is_relative_to(self.data_dir) or str(error) == "outside_managed_root":
                    status, reason = "retained_external", "outside_managed_roots"
                else:
                    status, reason = "blocked", str(error)
        self._resource(c, did, "file", locator, status, reason)

    def _resource(
        self, c: sqlite3.Connection, did: str, kind: str, locator: Row, status: str, error: str = ""
    ) -> None:
        identity = digest(
            {
                "kind": kind,
                "locator": {
                    k: locator[k]
                    for k in (
                        ["path"]
                        if kind == "file"
                        else ["collection", "point_id"]
                        if kind == "qdrant"
                        else ["store"]
                    )
                },
            }
        )
        c.execute(
            "INSERT OR IGNORE INTO media_cleanup_resources "
            "(deletion_id,resource_id,kind,status,locator_json,path,resolved_"
            "path,error,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                did,
                identity,
                kind,
                status,
                json.dumps(locator, ensure_ascii=False),
                locator.get("path"),
                locator.get("resolved_path"),
                error,
                self.now(),
            ),
        )

    def _tombstones(self, c: sqlite3.Connection, did: str, scope: str, manifest: Row) -> None:
        for kind, identities in manifest["targets"].items():
            if kind == "event":
                continue  # Registered in the same commit as physical event removal.
            for identity in identities:
                if (
                    c.execute(
                        "SELECT 1 FROM media_tombstones WHERE kind=? AND object_id=? AND "
                        "deletion_id=? AND scope_id=?",
                        (kind, identity, did, scope),
                    ).fetchone()
                    is None
                ):
                    raise InvalidDeletion("missing permanent source tombstone")

    def _sqlite(self, did: str, scope: str) -> None:
        with self.database.write_transaction() as c:
            task = self._authorize(c, did, scope)
            run = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (did,)).fetchone()
            if run["sqlite_done"]:
                return
            previous = json.loads(run["manifest_json"])
            self._tombstones(c, did, scope, previous)
            SqliteReconciliationRepository(self.database)._validate(c, did, scope)
            # Re-inventory current dependencies immediately before physical commit.
            plan = self.plans.generate(did, scope)
            manifest = self._manifest(
                c,
                self.plans._snapshot(c),
                DependencyAnalysis(self.plans._snapshot(c), set(previous["asset_ids"]), scope),
                did,
                plan,
            )

            def file_versions(files: list[Row]) -> list[Row]:
                return [
                    {key: f.get(key) for key in ("path", "stat", "resolved_path", "symlink")} for f in files
                ]

            if digest(file_versions(manifest["files"])) != digest(file_versions(previous["files"])):
                manifest["blockers"].append({"reason": "file_inventory_changed_since_manifest"})
            if manifest["targets"] != previous["targets"] or digest(manifest["rows"]) != digest(
                previous["rows"]
            ):
                manifest["blockers"].append({"reason": "source_rows_or_ownership_changed_since_manifest"})
            if manifest["blockers"]:
                c.execute(
                    "UPDATE media_cleanups SET status='blocked',error=?,validation_json=?,"
                    "updated_at=? WHERE deletion_id=?",
                    (
                        json.dumps(manifest["blockers"], ensure_ascii=False),
                        json.dumps(
                            {
                                "storage_cleaned": False,
                                "blockers": manifest["blockers"],
                                "total_deletion_complete": False,
                            }
                        ),
                        self.now(),
                        did,
                    ),
                )
                return
            manifest["initial_plan_id"] = previous.get("initial_plan_id", previous["plan_id"])
            manifest["plan_history"] = list(
                dict.fromkeys([*previous.get("plan_history", [previous["plan_id"]]), plan["plan_id"]])
            )
            c.execute(
                "UPDATE media_cleanups SET plan_id=?,manifest_json=? WHERE deletion_id=?",
                (plan["plan_id"], json.dumps(manifest, ensure_ascii=False), did),
            )
            with self.database.reconciliation(c):
                for eid in manifest["targets"]["event"]:
                    c.execute(
                        "INSERT OR IGNORE INTO media_tombstones VALUES ('event',?,?,?,?,?)",
                        (eid, scope, did, task["generation"], self.now()),
                    )
                for table in ORDER:
                    items = manifest["rows"][table]
                    if table == "assets":
                        byid = {i["row"]["id"]: i for i in items}

                        def depth(item: Row, byid: Row = byid) -> int:
                            seen = set()
                            parent = item["row"]["parent_asset_id"]
                            while parent in byid and parent not in seen:
                                seen.add(parent)
                                parent = byid[parent]["row"]["parent_asset_id"]
                            return len(seen)

                        items = sorted(items, key=depth, reverse=True)
                    for item in items:
                        key = item["key"]
                        where = " AND ".join(f'"{k}"=?' for k in key)
                        c.execute(f'DELETE FROM "{table}" WHERE {where}', tuple(key.values()))
                    self._step(c, did, "sqlite:" + table, {"deleted": len(items)})
                if c.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise InvalidDeletion("foreign key integrity check failed")
                if self.fault:
                    self.fault("sqlite", "before_commit")
                c.execute(
                    "UPDATE media_cleanups SET sqlite_done=1,status='running',updated"
                    "_at=? WHERE deletion_id=?",
                    (self.now(), did),
                )
        if self.fault:
            self.fault("sqlite", "after_commit")

    def _inventory(self, did: str, scope: str) -> None:
        with self.database.write_transaction() as c:
            self._authorize(c, did, scope)
            run = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (did,)).fetchone()
            manifest = json.loads(run["manifest_json"])
            try:
                if self.index is None:
                    raise RuntimeError("qdrant adapter unavailable; inventory not verified")
                points = self.index.deletion_inventory()
                for point in points:
                    owned, reason = self._vector_owner(point.get("payload"), manifest["targets"], scope)
                    if owned or reason:
                        if not point.get("namespace_owned"):
                            owned, reason = False, "unmanaged_qdrant_namespace"
                        self._resource(c, did, "qdrant", point, "pending" if owned else "blocked", reason)
                c.execute(
                    "UPDATE media_cleanup_resources SET status='done',error='',attemp"
                    "ts=attempts+1,updated_at=? WHERE deletion_id=? AND kind='invento"
                    "ry' AND json_extract(locator_json,'$.store')='qdrant'",
                    (self.now(), did),
                )
            except Exception as error:
                c.execute(
                    "UPDATE media_cleanup_resources SET status='failed',error=?,attem"
                    "pts=attempts+1,updated_at=? WHERE deletion_id=? AND kind='invent"
                    "ory' AND json_extract(locator_json,'$.store')='qdrant'",
                    (str(error)[:1500], self.now(), did),
                )

    def _shared_file(self, c: sqlite3.Connection, locator: Row) -> bool:
        path = Path(locator["path"])
        wanted = Path(locator["resolved_path"])
        identity = locator.get("stat")
        # All scopes and all surviving owners, including other isolated tasks.
        owners = []
        for row in c.execute("SELECT path,metadata_json FROM assets"):
            owners.append(row["path"])
            metadata, _ = parse_json(row["metadata_json"])
            if isinstance(metadata, dict):
                owners.append(metadata.get("browser_preview_path"))
        owners.extend(
            r[0]
            for r in c.execute(
                "SELECT original_path FROM import_jobs WHERE original_path!='' AN"
                "D asset_id NOT IN (SELECT asset_id FROM media_deletion_members W"
                "HERE deletion_id=?)",
                (locator["deletion_id"],),
            )
        )
        owners.extend(
            r[0]
            for r in c.execute(
                "SELECT s.output_artifact FROM video_job_stages s JOIN video_jobs j ON j.job_id=s.job_id"
            )
        )
        for raw in owners:
            if not raw:
                continue
            candidate = Path(raw)
            if candidate == path or candidate.resolve() == wanted:
                return True
            try:
                info = candidate.stat()
                if identity and (info.st_dev, info.st_ino) == (identity["device"], identity["inode"]):
                    return True
            except FileNotFoundError:
                pass
        return False

    def _resources(self, did: str, scope: str) -> None:
        with self.database.connect() as c:
            pending = [
                r["resource_id"]
                for r in c.execute(
                    "SELECT * FROM media_cleanup_resources WHERE deletion_id=? AND ki"
                    "nd!='inventory' AND status NOT IN ('done','retained_shared','ret"
                    "ained_external')",
                    (did,),
                )
            ]
        for rid in pending:
            # Serializes ownership checks, publishers and other cleanups across processes.
            with self.database.write_transaction() as c:
                self._authorize(c, did, scope)
                resource = c.execute(
                    "SELECT * FROM media_cleanup_resources WHERE deletion_id=? AND resource_id=?", (did, rid)
                ).fetchone()
                if resource["status"] in TERMINAL:
                    continue
                locator = json.loads(resource["locator_json"])
                status = "done"
                error = ""
                try:
                    if resource["status"] == "blocked":
                        # Revalidate file blockers only when originally proven owned and unchanged.
                        raise UnsafeMediaFile(resource["error"] or "manual_ownership_review_required")
                    if resource["kind"] == "qdrant":
                        if self.index is None:
                            raise RuntimeError("qdrant adapter unavailable")
                        self.index.delete_exact(locator)
                    else:
                        locator["deletion_id"] = did
                        if self._shared_file(c, locator):
                            status = "retained_shared"
                            error = "current_shared_file_owner"
                        else:
                            self.remove_file(self.data_dir, locator)
                    if self.fault:
                        self.fault(resource["kind"], "after_external_action")
                except (UnsafeMediaFile, ValueError) as failure:
                    status, error = "blocked", str(failure)
                except Exception as failure:
                    status, error = "failed", str(failure)
                c.execute(
                    "UPDATE media_cleanup_resources SET status=?,error=?,attempts=att"
                    "empts+1,updated_at=? WHERE deletion_id=? AND resource_id=?",
                    (status, error[:1500], self.now(), did, rid),
                )

    @staticmethod
    def _step(c: sqlite3.Connection, did: str, step: str, result: Row) -> None:
        c.execute(
            "INSERT OR IGNORE INTO media_cleanup_steps VALUES (?,?,?,?)",
            (did, step, json.dumps(result), datetime.now(UTC).isoformat()),
        )

    def _verify(self, did: str, scope: str) -> None:
        with self.database.write_transaction() as c:
            self._authorize(c, did, scope)
            run = c.execute("SELECT * FROM media_cleanups WHERE deletion_id=?", (did,)).fetchone()
            manifest = json.loads(run["manifest_json"])
            errors = []
            self._tombstones(c, did, scope, manifest)
            for table in ORDER:
                for item in manifest["rows"][table]:
                    key = item["key"]
                    if table == "observation_search_fts":
                        key = {"observation_id": item["row"]["observation_id"]}
                    where = " AND ".join(f'"{k}"=?' for k in key)
                    if c.execute(f'SELECT 1 FROM "{table}" WHERE {where}', tuple(key.values())).fetchone():
                        errors.append({"table": table, "key": key, "reason": "sqlite_source_remains"})
            for resource in c.execute(
                "SELECT * FROM media_cleanup_resources WHERE deletion_id=? AND kind='file' AND status='done'",
                (did,),
            ):
                if Path(resource["path"]).exists() or Path(resource["path"]).is_symlink():
                    errors.append({"path": resource["path"], "reason": "file_remains_or_reappeared"})
            try:
                if self.index is None:
                    raise RuntimeError("qdrant inventory unavailable")
                for point in self.index.deletion_inventory():
                    owned, reason = self._vector_owner(point.get("payload"), manifest["targets"], scope)
                    if owned or reason:
                        errors.append(
                            {"point_id": point["point_id"], "reason": reason or "qdrant_source_remains"}
                        )
            except Exception as error:
                errors.append({"reason": "qdrant_verification_failed", "error": str(error)})
            resources = list(
                c.execute("SELECT status,error FROM media_cleanup_resources WHERE deletion_id=?", (did,))
            )
            failed = any(r["status"] == "failed" for r in resources)
            blocked = any(r["status"] == "blocked" for r in resources)
            if c.execute("PRAGMA foreign_key_check").fetchone():
                errors.append({"reason": "foreign_key_violation"})
            pending = any(r["status"] not in TERMINAL for r in resources)
            retained = any(r["status"].startswith("retained_") for r in resources)
            status = (
                "failed"
                if failed
                else "blocked"
                if blocked or errors or pending
                else "storage_cleaned_with_retained"
                if retained
                else "storage_cleaned"
            )
            validation = {
                "errors": errors,
                "foreign_keys_enabled": c.execute("PRAGMA foreign_keys").fetchone()[0],
                "source_rows_absent": not any(e.get("table") for e in errors),
                "storage_cleaned": not (errors or pending),
                "total_deletion_complete": False,
            }
            c.execute(
                "UPDATE media_cleanups SET status=?,validation_json=?,error=?,upd"
                "ated_at=? WHERE deletion_id=?",
                (status, json.dumps(validation), json.dumps(errors) if errors else "", self.now(), did),
            )
            if validation["storage_cleaned"]:
                self._step(c, did, "storage_validation", validation)
