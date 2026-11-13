"""Immutable, recoverable dependency manifests. No business-row updates or resource deletion."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.media.analyze_deletion import DependencyAnalysis, Row, digest, parse_json
from sentrix.domain.media.deletion import DeletionNotFound, InvalidDeletion
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

ANALYSIS_VERSION = "media-deletion-3a-v1"

TABLES = (
    "assets",
    "observations",
    "asset_places",
    "face_instances",
    "face_clusters",
    "face_prototypes",
    "entities",
    "entity_mentions",
    "entity_observations",
    "entity_properties",
    "entity_revisions",
    "events",
    "event_observations",
    "event_participants",
    "event_entities",
    "facts",
    "relationships",
    "semantic_profiles",
    "semantic_claims",
    "person_appearance_evidence",
    "person_moments",
    "person_portrait_revisions",
    "family_person_memberships",
    "family_relationships",
    "family_relationship_blocks",
    "family_person_portraits",
    "family_analysis_runs",
    "family_scope_groups",
    "memory_graph_nodes",
    "memory_graph_edges",
    "memory_graph_builds",
    "memory_rotation_sequences",
    "memory_rotation_viewed",
    "agent_result_sets",
    "agent_turns",
    "agent_turn_events",
    "agent_turn_events_v2",
    "agent_conversation_messages",
    "memory_vectors",
    "text_vectors",
    "observation_search_terms",
    "observation_search_fts",
    "video_jobs",
    "video_job_stages",
    "import_jobs",
    "import_job_stages",
    "scope_postprocess",
    "media_tombstones",
    "media_object_states",
    "media_evidence_revocations",
    "media_scope_invalidations",
)


class SqliteDeletionPlanRepository:
    def __init__(
        self,
        database: Database,
        *,
        data_dir: Path | None = None,
        qdrant_inventory: Callable[[], list[dict[str, object]]] | None = None,
        qdrant_prefix: str = "sentrix_home",
    ) -> None:
        self.database = database
        self.data_dir = (data_dir or database.path.parent).resolve()
        self.qdrant_inventory = qdrant_inventory
        self.qdrant_prefix = qdrant_prefix

    @staticmethod
    def _task(
        connection: sqlite3.Connection, deletion_id: str, scope_id: str, *, allow_missing: bool = False
    ) -> tuple[Row, list[Row]]:
        task = connection.execute(
            "SELECT * FROM media_deletions WHERE deletion_id=? AND scope_id=?", (deletion_id, scope_id)
        ).fetchone()
        if task is None:
            raise DeletionNotFound("deletion task not found in this scope")
        members = connection.execute(
            "SELECT m.*,a.deletion_state,a.deletion_generation FROM media_deletion_members m "
            "LEFT JOIN assets a ON a.id=m.asset_id WHERE m.deletion_id=? ORDER BY m.asset_id",
            (deletion_id,),
        ).fetchall()
        if task["status"] != "isolated" or not members:
            raise InvalidDeletion("only an isolated deletion with durable members can be analyzed")
        for member in members:
            if member["deletion_state"] is None and allow_missing:
                tombstone = connection.execute(
                    "SELECT 1 FROM media_tombstones WHERE kind='asset' AND object_id=? "
                    "AND deletion_id=? AND generation=?",
                    (member["asset_id"], deletion_id, member["generation"]),
                ).fetchone()
                if tombstone:
                    continue
            if (
                member["deletion_state"] != "isolated"
                or member["deletion_generation"] != member["generation"]
            ):
                raise InvalidDeletion(
                    "member isolation version mismatch or source missing; manual review required"
                )
        return dict(task), [dict(m) for m in members]

    @staticmethod
    def _snapshot(connection: sqlite3.Connection) -> dict[str, list[Row]]:
        snapshot = {}
        for table in TABLES:
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if not columns:
                continue
            keys = [r["name"] for r in sorted(columns, key=lambda r: r["pk"]) if r["pk"]]
            rows = []
            for raw in connection.execute(f'SELECT rowid AS _rowid,* FROM "{table}"'):
                row = dict(raw)
                row["_key"] = {k: row[k] for k in keys} if keys else {"rowid": row["_rowid"]}
                row.pop("_rowid", None)
                rows.append(row)
            snapshot[table] = sorted(rows, key=lambda row: json.dumps(row["_key"], sort_keys=True))
        return snapshot

    def _external(
        self, snapshot: dict[str, list[Row]], asset_ids: set[str]
    ) -> tuple[list[Row], list[Row], list[str]]:
        files: dict[str, Row] = {}
        warnings = ["filesystem_and_external_index_require_3B_revalidation"]
        assets = snapshot.get("assets", [])
        candidates: list[tuple[Path, str, str]] = []
        for asset in assets:
            if asset["id"] not in asset_ids:
                continue
            aid = str(asset["id"])
            if asset.get("path"):
                candidates.append((Path(str(asset["path"])), "asset.path", aid))
            metadata, _ = parse_json(asset.get("metadata_json", "{}"))
            if isinstance(metadata, dict) and metadata.get("browser_preview_path"):
                candidates.append(
                    (Path(str(metadata["browser_preview_path"])), "metadata.browser_preview_path", aid)
                )
            previews = self.data_dir / "previews"
            if previews.is_dir():
                for child in previews.iterdir():
                    # Parse the asset-id portion exactly; a substring match would
                    # confuse asset_1 with asset_10 and unrelated filenames.
                    base = child.name.removeprefix(".")
                    if base.startswith(aid + "-") and base[len(aid) + 1 :].split(".")[0].isdigit():
                        candidates.append((child, "preview.asset_id-size", aid))
            if Path(aid).name != aid or aid in {".", ".."} or "\\" in aid:
                warnings.append(f"unsafe_asset_directory_identity:{aid}")
                continue
            for directory in [
                self.data_dir / "media/derived/video" / aid,
                self.data_dir / "media/derived/svd" / aid,
                self.data_dir / "media/derived/svd_katna" / aid,
            ]:
                self._walk(directory, aid, candidates, warnings)
        jobs = {
            str(j["job_id"]): str(j["asset_id"])
            for j in snapshot.get("video_jobs", [])
            if j["asset_id"] in asset_ids
        }
        for job_id, aid in jobs.items():
            self._walk(self.data_dir / "video-jobs" / job_id, aid, candidates, warnings)
        for stage in snapshot.get("video_job_stages", []):
            if str(stage["job_id"]) in jobs and stage.get("output_artifact"):
                artifact_path = str(stage["output_artifact"])
                if not artifact_path.startswith("assets."):
                    candidates.append(
                        (Path(artifact_path), "video_job_stages.output_artifact", jobs[str(stage["job_id"])])
                    )
        for job in snapshot.get("import_jobs", []):
            if job.get("asset_id") in asset_ids and job.get("original_path"):
                candidates.append(
                    (Path(str(job["original_path"])), "import_jobs.original_path", str(job["asset_id"]))
                )
        # File aliases are checked against every asset, including other scopes.
        owners: dict[str, set[str]] = {}
        for asset in assets:
            if asset.get("path"):
                owners.setdefault(str(Path(str(asset["path"])).resolve()), set()).add(str(asset["id"]))
        for record in assets:
            metadata, _ = parse_json(record.get("metadata_json", "{}"))
            if isinstance(metadata, dict) and metadata.get("browser_preview_path"):
                owners.setdefault(str(Path(str(metadata["browser_preview_path"])).resolve()), set()).add(
                    str(record["id"])
                )
        for record in snapshot.get("import_jobs", []):
            if record.get("original_path"):
                owners.setdefault(str(Path(str(record["original_path"])).resolve()), set()).add(
                    str(record["asset_id"])
                )
        all_jobs = {str(j["job_id"]): str(j["asset_id"]) for j in snapshot.get("video_jobs", [])}
        for record in snapshot.get("video_job_stages", []):
            artifact = record.get("output_artifact")
            if artifact and str(record["job_id"]) in all_jobs and not str(artifact).startswith("assets."):
                owners.setdefault(str(Path(str(artifact)).resolve()), set()).add(
                    all_jobs[str(record["job_id"])]
                )
        for path, source, aid in candidates:
            try:
                resolved = path.resolve()
                roots = [self.data_dir / name for name in ("media", "previews", "video-jobs")]
                managed = any(resolved.is_relative_to(root) for root in roots) and not path.is_symlink()
                stat = path.lstat() if path.exists() or path.is_symlink() else None
                previous = files.get(str(path.absolute()))
                item = previous or {
                    "path": str(path.absolute()),
                    "resolved_path": str(resolved),
                    "sources": [],
                    "owner_asset_ids": sorted(owners.get(str(resolved), set()) | {aid}),
                    "managed": managed,
                    "symlink": path.is_symlink(),
                    "category": "exclusive" if managed else "unknown",
                    "automatic_deletion_allowed": False,
                }
                item["sources"] = sorted(set(item["sources"]) | {source})
                item["expected_content_sha256"] = {
                    str(a["id"]): str(a.get("content_sha256", ""))
                    for a in assets
                    if a.get("path") and str(Path(str(a["path"])).resolve()) == str(resolved)
                }
                item["exists"] = stat is not None
                item["stat"] = (
                    {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "device": stat.st_dev,
                        "inode": stat.st_ino,
                    }
                    if stat
                    else None
                )
                item["owner_scopes"] = {
                    owner: next((a["scope_id"] for a in assets if a["id"] == owner), None)
                    for owner in item["owner_asset_ids"]
                }
                item["ownership_complete"] = False  # no universal artifact registry yet
                if stat and stat.st_nlink > 1:
                    item["category"] = "unknown"
                    warnings.append(f"hardlink_requires_reference_audit:{path}")
                if set(item["owner_asset_ids"]) - asset_ids:
                    item["category"] = "shared"
                files[str(path.absolute())] = item
            except (OSError, ValueError, RuntimeError) as error:
                warnings.append(f"file_inventory_error:{path}:{type(error).__name__}")
        try:
            external = self.qdrant_inventory() if self.qdrant_inventory is not None else []
            if self.qdrant_inventory is None:
                warnings.append("qdrant_inventory_unavailable_expected_locators_only")
        except Exception as error:  # noqa: BLE001 - unavailable external inventory must remain visible
            external = []
            warnings.append(f"qdrant_inventory_unavailable:{type(error).__name__}")
        return (
            sorted(files.values(), key=lambda r: str(r["path"])),
            sorted(external, key=digest),
            sorted(set(warnings)),
        )

    def _walk(
        self, directory: Path, aid: str, candidates: list[tuple[Path, str, str]], warnings: list[str]
    ) -> None:
        try:
            safe = directory.resolve().is_relative_to(self.data_dir) and not directory.is_symlink()
        except (OSError, ValueError, RuntimeError):
            safe = False
        if not safe:
            warnings.append(f"unmanaged_or_symlink_directory:{directory}")
            return
        if not directory.is_dir():
            return
        try:
            for child in sorted(directory.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    self._walk(child, aid, candidates, warnings)
                else:
                    candidates.append((child, "asset_or_job_directory", aid))
        except OSError as error:
            warnings.append(f"directory_inventory_error:{directory}:{type(error).__name__}")

    def _capture(self, connection: sqlite3.Connection, task: Row, members: list[Row]) -> tuple[Row, Row]:
        snapshot = self._snapshot(connection)
        asset_ids = {str(m["asset_id"]) for m in members}
        files, external, warnings = self._external(snapshot, asset_ids)
        versions = {
            table: {"row_count": len(rows), "fingerprint": digest(rows)} for table, rows in snapshot.items()
        }
        source = {
            "analysis_version": ANALYSIS_VERSION,
            "location_config": {"data_dir": str(self.data_dir), "qdrant_prefix": self.qdrant_prefix},
            "tables": versions,
            "members": members,
            "deletion_generation": task["generation"],
            "schema_versions": [
                r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
            ],
            "files_fingerprint": digest(files),
            "qdrant_fingerprint": digest(external),
            "inventory_warnings": warnings,
        }
        return {"rows": snapshot, "files": files, "qdrant": external, "warnings": warnings}, source

    def generate(self, deletion_id: str, scope_id: str) -> Row:
        with self.database.connect() as connection, connection:
            # No expensive model work occurs here. The instance-wide snapshot is
            # deliberately conservative and serialized with all SQLite writers.
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            task, members = self._task(connection, deletion_id, scope_id)
            captured, source = self._capture(connection, task, members)
            fingerprint = digest(source)
            existing = connection.execute(
                "SELECT * FROM media_deletion_plans WHERE deletion_id=? AND fingerprint=?",
                (deletion_id, fingerprint),
            ).fetchone()
            if existing is not None:
                return self._payload(existing, stale=False)
            analyzer = DependencyAnalysis(captured["rows"], {str(m["asset_id"]) for m in members}, scope_id)
            items = analyzer.analyze()
            vectors = self._vectors(captured, analyzer, items)
            index_items = [
                i for i in items if i["table"] in {"observation_search_terms", "observation_search_fts"}
            ]
            indexes = [
                {
                    "database": str(self.database.path),
                    "table": i["table"],
                    "key": i["key"],
                    "evidence": i["evidence"],
                    "row_fingerprint": i["row_fingerprint"],
                    "automatic_deletion_allowed": False,
                }
                for i in index_items
            ]
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM media_deletion_plans WHERE deletion_id=?",
                (deletion_id,),
            ).fetchone()[0]
            payload = {
                "root_asset_id": task["root_asset_id"],
                "asset_ids": sorted(analyzer.targets),
                "scope_id": scope_id,
                "items": items,
                "dependency_edges": [
                    {
                        "from": item["locator"],
                        "to": {"kind": ref["kind"], "id": ref["id"]},
                        "field": ref["field"],
                        "role": ref["role"],
                        "unresolved": ref["unresolved"],
                    }
                    for item in items
                    for ref in item["evidence"]
                ],
                "coverage": {
                    "database": "registered_tables_raw_snapshot",
                    "qdrant": "unavailable"
                    if any("qdrant_inventory_unavailable" in w for w in captured["warnings"])
                    else "observed_all_collections_payloads",
                    "files": "best_effort_known_paths_no_global_artifact_registry",
                },
                "vectors": vectors,
                "indexes": indexes,
                "files": captured["files"],
                "warnings": captured["warnings"],
                "category_counts": dict(Counter(str(i["category"]) for i in items)),
                "analysis_only": True,
                "automatic_deletion_allowed": False,
                "concurrency_policy": (
                    "BEGIN IMMEDIATE; immutable instance-snapshot fingerprint; "
                    "external locations must be revalidated in 3B"
                ),
            }
            connection.execute(
                "INSERT INTO media_deletion_plans VALUES (?,?,?,?,?,?,?)",
                (
                    f"plan_{uuid.uuid4().hex}",
                    deletion_id,
                    version,
                    fingerprint,
                    datetime.now(UTC).isoformat(),
                    json.dumps(source, sort_keys=True),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            row = connection.execute(
                "SELECT * FROM media_deletion_plans WHERE deletion_id=? AND version=?", (deletion_id, version)
            ).fetchone()
            return self._payload(row, stale=False)

    def get(self, deletion_id: str, scope_id: str, version: int | None = None) -> Row:
        with self.database.connect() as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            task, members = self._task(connection, deletion_id, scope_id, allow_missing=True)
            query = "SELECT * FROM media_deletion_plans WHERE deletion_id=?"
            params: tuple[object, ...] = (deletion_id,)
            if version is not None:
                query += " AND version=?"
                params += (version,)
            row = connection.execute(query + " ORDER BY version DESC LIMIT 1", params).fetchone()
            if row is None:
                raise DeletionNotFound("deletion plan has not been generated")
            _, source = self._capture(connection, task, members)
            return self._payload(row, stale=digest(source) != row["fingerprint"])

    @staticmethod
    def _payload(row: sqlite3.Row, *, stale: bool) -> Row:
        return {
            "plan_id": row["plan_id"],
            "deletion_id": row["deletion_id"],
            "version": row["version"],
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
            "stale": stale,
            "source_versions": json.loads(row["source_versions_json"]),
            "impact": json.loads(row["payload_json"]),
        }

    def _vectors(self, captured: Row, analyzer: DependencyAnalysis, items: list[Row]) -> list[Row]:
        result = []
        selected = {(str(i["table"]), json.dumps(i["key"], sort_keys=True)) for i in items}
        for table in ["memory_vectors", "text_vectors"]:
            for row in captured["rows"].get(table, []):
                if (table, json.dumps(row["_key"], sort_keys=True)) not in selected:
                    continue
                locator = QdrantVectorIndex.locator(
                    str(row["space"]),
                    str(row["source_type"]),
                    str(row["source_id"]),
                    str(row["model_name"]),
                    int(row["dimension"]),
                    prefix=self.qdrant_prefix,
                )
                result.append(
                    {
                        "store": "sqlite",
                        "table": table,
                        "row_id": row["id"],
                        "scope_id": row["scope_id"],
                        **locator,
                        "qdrant_presence": "unverified_expected_location",
                        "automatic_deletion_allowed": False,
                    }
                )
        for point in captured["qdrant"]:
            payload = point.get("payload", {})
            if not isinstance(payload, dict):
                continue
            refs = [{"kind": str(payload.get("source_type")), "id": str(payload.get("source_id"))}]
            metadata = payload.get("metadata", {})
            if isinstance(metadata, dict):
                refs.extend(
                    {"kind": kind, "id": str(metadata[key])}
                    for key, kind in [
                        ("asset_id", "asset"),
                        ("observation_id", "observation"),
                        ("event_id", "event"),
                    ]
                    if metadata.get(key)
                )
            resolved = [analyzer.resolve(r["kind"], r["id"]) for r in refs]
            matches = any(assets & analyzer.targets for assets, _ in resolved)
            unknown = (not refs or any(unresolved for _, unresolved in resolved)) and payload.get(
                "scope_id"
            ) in {None, analyzer.scope}
            if matches or unknown:
                result.append(
                    {
                        "store": "qdrant",
                        **point,
                        "evidence": refs,
                        "category": "unknown"
                        if unknown or not point.get("namespace_owned", False)
                        else "derived",
                        "qdrant_presence": "observed",
                        "cross_scope": payload.get("scope_id") != analyzer.scope,
                        "automatic_deletion_allowed": False,
                    }
                )
        return sorted(result, key=digest)
