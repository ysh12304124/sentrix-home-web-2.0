"""Typed, conservative dependency analysis. No IO and no deletion decisions."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

# SQLite rows and legacy JSON have versioned, heterogeneous shapes.
Row = dict[str, Any]
Reference = dict[str, str]

TABLE_KINDS = {
    "assets": "asset",
    "observations": "observation",
    "face_instances": "face_instance",
    "face_clusters": "face_cluster",
    "entities": "entity",
    "events": "event",
    "facts": "fact",
    "relationships": "relationship",
    "person_moments": "person_moment",
    "person_appearance_evidence": "appearance",
    "person_portrait_revisions": "portrait",
    "semantic_claims": "claim",
    "semantic_profiles": "profile",
    "memory_graph_nodes": "graph_node",
    "agent_result_sets": "result_set",
    "agent_turns": "turn",
    "memory_rotation_sequences": "rotation",
    "video_jobs": "video_job",
    "family_analysis_runs": "family_run",
    "family_relationships": "family_relationship",
    "family_person_portraits": "family_portrait",
}
FIELD_KINDS = {
    "asset_id": "asset",
    "parent_asset_id": "asset",
    "source_asset_id": "asset",
    "observation_id": "observation",
    "face_instance_id": "face_instance",
    "event_id": "event",
    "person_id": "entity",
    "entity_id": "entity",
    "subject_entity_id": "entity",
    "object_entity_id": "entity",
    "value_entity_id": "entity",
    "left_entity_id": "entity",
    "right_entity_id": "entity",
    "inference_run_id": "family_run",
    "cluster_id": "face_cluster",
    "moment_id": "person_moment",
    "relationship_id": "relationship",
    "fact_id": "fact",
    "turn_id": "turn",
    "result_set_id": "result_set",
    "sequence_id": "rotation",
    "job_id": "video_job",
}
LIST_KINDS = {
    "asset_ids": "asset",
    "observation_ids": "observation",
    "event_ids": "event",
    "supporting_event_ids": "event",
    "participant_entity_ids": "entity",
    "event_entity_ids": "entity",
}
DERIVED = {
    "events",
    "event_participants",
    "event_entities",
    "face_prototypes",
    "person_moments",
    "person_appearance_evidence",
    "person_portrait_revisions",
    "semantic_profiles",
    "semantic_claims",
    "family_person_portraits",
    "family_analysis_runs",
    "memory_graph_nodes",
    "memory_graph_edges",
    "memory_graph_builds",
    "memory_rotation_sequences",
    "memory_rotation_viewed",
    "memory_vectors",
    "text_vectors",
    "observation_search_terms",
    "observation_search_fts",
}
SOURCE_UNKNOWN = {
    "facts",
    "relationships",
    "entity_properties",
    "entity_revisions",
    "semantic_profiles",
    "semantic_claims",
    "person_portrait_revisions",
    "family_person_memberships",
    "family_relationships",
    "family_person_portraits",
    "family_analysis_runs",
    "agent_turns",
    "agent_turn_events",
    "agent_turn_events_v2",
    "agent_conversation_messages",
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def parse_json(value: object) -> tuple[object, bool]:
    if not isinstance(value, str):
        return value, True
    try:
        return json.loads(value), True
    except (TypeError, ValueError):
        return None, False


class DependencyAnalysis:
    def __init__(self, snapshot: Mapping[str, list[Row]], asset_ids: set[str], scope_id: str) -> None:
        self.rows = snapshot
        self.targets = asset_ids
        self.scope = scope_id
        self.by_kind = {
            kind: {
                str(row.get("id", row.get("job_id", row.get("sequence_id")))): row
                for row in snapshot.get(table, [])
            }
            for table, kind in TABLE_KINDS.items()
        }
        self.tombstones = {
            (str(r["kind"]), str(r["object_id"])) for r in snapshot.get("media_tombstones", [])
        }
        self.event_observations: dict[str, list[str]] = defaultdict(list)
        self.entity_observations: dict[str, set[str]] = defaultdict(set)
        self.cluster_faces: dict[str, list[str]] = defaultdict(list)
        for row in snapshot.get("event_observations", []):
            self.event_observations[str(row["event_id"])].append(str(row["observation_id"]))
        for table in ["entity_mentions", "entity_observations"]:
            for row in snapshot.get(table, []):
                self.entity_observations[str(row["entity_id"])].add(str(row["observation_id"]))
        for row in snapshot.get("face_instances", []):
            self.cluster_faces[str(row.get("cluster_id"))].append(str(row["id"]))
        self.affected: dict[str, set[str]] = defaultdict(set)
        self.affected["asset"] = asset_ids
        self.affected["video_job"] = {
            str(row["job_id"])
            for row in snapshot.get("video_jobs", [])
            if str(row.get("asset_id")) in asset_ids
        }
        for kind in [
            "observation",
            "face_instance",
            "person_moment",
            "event",
            "face_cluster",
            "entity",
            "turn",
            "result_set",
            "rotation",
        ]:
            for identity in self.by_kind.get(kind, {}):
                assets, _ = self.resolve(kind, identity)
                if assets & asset_ids:
                    self.affected[kind].add(identity)

        self.graph_scopes = {scope_id}
        for node in snapshot.get("memory_graph_nodes", []):
            if self.resolve("graph_node", str(node["id"]))[0] & asset_ids:
                self.graph_scopes.add(str(node["scope_id"]))
        for edge in snapshot.get("memory_graph_edges", []):
            if any(
                self.resolve("graph_node", str(edge[key]))[0] & asset_ids
                for key in ("source_id", "target_id")
            ):
                self.graph_scopes.add(str(edge["scope_id"]))

        self.family_graph_ids = {
            str(row["graph_id"])
            for row in snapshot.get("family_scope_groups", [])
            if row["scope_id"] == scope_id
        }
        for table in ["family_relationships", "family_person_portraits", "family_person_memberships"]:
            for row in snapshot.get(table, []):
                if not row.get("inference_run_id"):
                    continue
                assets = set()
                for ref in self.refs(table, row):
                    if ref["kind"] not in {"entity", "face_cluster", "family_run"}:
                        assets |= self.resolve(ref["kind"], ref["id"])[0]
                if assets & asset_ids:
                    self.affected["family_run"].add(str(row["inference_run_id"]))

    @staticmethod
    def ref(kind: str, identity: object, field: str) -> Reference:
        return {"kind": kind, "id": str(identity), "field": field}

    def json_refs(
        self, value: object, field: str, default: str | None = None, *, decoded: bool = False
    ) -> list[Reference]:
        parsed, valid = (value, True) if decoded else parse_json(value)
        if not valid:
            return [self.ref("unknown", "malformed_json", field)]
        if isinstance(parsed, list):
            refs = []
            for index, item in enumerate(parsed):
                refs.extend(self.json_refs(item, f"{field}[{index}]", default, decoded=True))
            return refs
        if isinstance(parsed, dict):
            if "kind" in parsed and "id" in parsed:
                kind = str(parsed["kind"])
                if kind == "person":
                    kind = "entity"
                return [self.ref(kind, parsed["id"], field)]
            refs = []
            for key, item in parsed.items():
                if key in FIELD_KINDS and item:
                    refs.append(self.ref(FIELD_KINDS[key], item, f"{field}.{key}"))
                elif key in LIST_KINDS:
                    refs.extend(self.json_refs(item, f"{field}.{key}", LIST_KINDS[key], decoded=True))
                elif key in {"evidence_refs", "themes", "evidence_ids"}:
                    refs.extend(
                        self.json_refs(
                            item,
                            f"{field}.{key}",
                            "observation" if key == "evidence_ids" else None,
                            decoded=True,
                        )
                    )
                elif isinstance(item, (dict, list)):
                    refs.extend(self.json_refs(item, f"{field}.{key}", decoded=True))
            if not refs and parsed and ("evidence" in field or "attachments" in field):
                refs.append(self.ref("unknown", "untyped_object", field))
            return refs
        if isinstance(parsed, str) and parsed:
            return [self.ref(default or "unknown", parsed, field)]
        if parsed is not None and not isinstance(parsed, (dict, list, str)):
            return [self.ref("unknown", "untyped_scalar", field)]
        return []

    def refs(self, table: str, row: Row) -> list[Reference]:
        refs = [self.ref("asset", row["id"], "id")] if table == "assets" else []
        for field, kind in FIELD_KINDS.items():
            if row.get(field):
                refs.append(self.ref(kind, row[field], field))
        # Each field has a code-established contract; never infer kind from an ID prefix.
        for field in [
            "evidence_ids_json",
            "evidence_refs_json",
            "supporting_event_ids_json",
            "asset_ids_json",
            "event_ids_json",
        ]:
            if field in row:
                default_kind = {
                    "supporting_event_ids_json": "event",
                    "asset_ids_json": "asset",
                    "event_ids_json": "event",
                }.get(field)
                if field == "evidence_ids_json" or (
                    field == "evidence_refs_json" and table.startswith("family_")
                ):
                    default_kind = "observation"
                refs.extend(self.json_refs(row[field], field, default_kind))
        for field in [
            "themes_json",
            "attributes_json",
            "attachments_json",
            "candidates_json",
            "trace_json",
            "payload_json",
            "event_memory_json",
            "output_json",
            "evidence_classes_json",
        ]:
            if field in row:
                refs.extend(self.json_refs(row[field], field))
        if table == "events":
            refs.extend(
                self.ref("observation", oid, "event_observations")
                for oid in self.event_observations[str(row["id"])]
            )
        elif table == "entities":
            refs.extend(
                self.ref("observation", oid, "entity_mentions/entity_observations")
                for oid in sorted(self.entity_observations[str(row["id"])])
            )
            for cluster in self.rows.get("face_clusters", []):
                if cluster.get("entity_id") == row["id"]:
                    refs.extend(
                        self.ref("face_instance", fid, "face_clusters/face_instances")
                        for fid in self.cluster_faces[str(cluster["id"])]
                    )
        elif table == "face_clusters":
            refs.extend(
                self.ref("face_instance", fid, "face_instances") for fid in self.cluster_faces[str(row["id"])]
            )
        elif table in {"memory_vectors", "text_vectors"}:
            refs.append(self.ref(str(row["source_type"]), row["source_id"], "source_type/source_id"))
            refs.extend(self.json_refs(row.get("metadata_json", "{}"), "metadata_json"))
        elif table == "memory_graph_edges":
            refs.extend(self.ref("graph_node", row[key], key) for key in ["source_id", "target_id"])
        return refs

    def resolve(
        self, kind: str, identity: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> tuple[set[str], bool]:
        key = (kind, identity)
        if key in seen:
            return set(), True
        if kind == "asset":
            return (
                ({identity}, False)
                if identity in self.targets or identity in self.by_kind.get("asset", {})
                else (set(), True)
            )
        row = self.by_kind.get(kind, {}).get(identity)
        if row is None:
            return set(), True
        if kind in {"observation", "face_instance", "person_moment", "appearance"}:
            return self.resolve("asset", str(row.get("asset_id", "")), seen | {key})
        if kind == "event":
            refs = [
                self.ref("observation", oid, "event_observations")
                for oid in self.event_observations[identity]
            ]
            if row.get("source_asset_id"):
                refs.append(self.ref("asset", row["source_asset_id"], "source_asset_id"))
        elif kind == "face_cluster":
            refs = [self.ref("face_instance", fid, "members") for fid in self.cluster_faces[identity]]
        elif kind == "entity":
            refs = [self.ref("observation", oid, "mentions") for oid in self.entity_observations[identity]]
            for cluster in self.rows.get("face_clusters", []):
                if cluster.get("entity_id") == identity:
                    refs.append(self.ref("face_cluster", cluster["id"], "cluster"))
        else:
            table = next((t for t, k in TABLE_KINDS.items() if k == kind), "")
            refs = [r for r in self.refs(table, row) if r["kind"] not in {"entity", "face_cluster"}]
        assets: set[str] = set()
        unknown = not bool(refs)
        for ref in refs:
            values, unresolved = self.resolve(ref["kind"], ref["id"], seen | {key})
            assets |= values
            unknown |= unresolved
        return assets, unknown

    def analyze(self) -> list[Row]:
        items: list[Row] = []
        for table, rows in sorted(self.rows.items()):
            if table in {"schema_migrations", "media_tombstones"}:
                continue
            for row in rows:
                kind = TABLE_KINDS.get(table, table)
                refs = self.refs(table, row)
                dedicated_tables = {
                    "asset_places",
                    "entity_mentions",
                    "entity_observations",
                    "event_observations",
                    "person_appearance_evidence",
                    "person_moments",
                    "observation_search_terms",
                    "observation_search_fts",
                }
                direct = (
                    kind in {"asset", "observation", "face_instance", "person_moment"}
                    and str(row.get("id")) in self.affected[kind]
                ) or (
                    table in dedicated_tables
                    and (
                        str(row.get("asset_id")) in self.targets
                        or str(row.get("observation_id")) in self.affected["observation"]
                        or str(row.get("face_instance_id")) in self.affected["face_instance"]
                    )
                )
                evidence = []
                target_assets: set[str] = set()
                remaining_assets: set[str] = set()
                related_identity = False
                unknown = False
                for ref in refs:
                    values, unresolved = self.resolve(ref["kind"], ref["id"])
                    # Identity endpoints are context, not proof that a relationship/property
                    # was inferred from every photo showing that person.
                    context = ref["kind"] in {
                        "entity",
                        "face_cluster",
                        "rotation",
                        "turn",
                        "result_set",
                        "video_job",
                        "family_run",
                    }
                    if ref["field"] == "event_id" and table in {
                        "person_moments",
                        "event_observations",
                        "event_participants",
                        "event_entities",
                    }:
                        context = True
                    valid = {aid for aid in values - self.targets if self.valid_asset(aid)}
                    target = values & self.targets
                    if not context:
                        target_assets |= target
                        remaining_assets |= valid
                        unknown |= unresolved
                    related_identity |= ref["id"] in self.affected.get(ref["kind"], set())
                    evidence.append(
                        {
                            **ref,
                            "role": "context" if context else "evidence",
                            "target_asset_ids": sorted(target),
                            "remaining_valid_asset_ids": sorted(valid),
                            "resolved_asset_ids": sorted(values),
                            "other_isolated_asset_ids": sorted(values - target - valid),
                            "unresolved": unresolved,
                            "reference_valid": bool(valid) and not target and not unresolved,
                            "requires_dependency_rebuild": bool(target and valid),
                        }
                    )
                related_event = any(
                    ref["kind"] == "event" and ref["id"] in self.affected["event"] for ref in refs
                )
                scope = self.scope_for(table, row)
                projection = (table.startswith("memory_graph_") and scope in self.graph_scopes) or (
                    table == "family_scope_groups" and str(row.get("graph_id")) in self.family_graph_ids
                )
                selected_identity = str(row.get("id")) in self.affected.get(kind, set())
                unknown_candidate = (
                    table in SOURCE_UNKNOWN
                    and scope == self.scope
                    and (unknown or not any(e["role"] == "evidence" for e in evidence))
                )
                if not (
                    direct
                    or target_assets
                    or related_identity
                    or related_event
                    or projection
                    or unknown_candidate
                    or selected_identity
                ):
                    continue
                if table in {"entities", "face_clusters"}:
                    related_identity = True
                key = row["_key"]
                versions = {
                    k: row[k]
                    for k in ["revision", "updated_at", "build_revision", "generation", "status", "state"]
                    if k in row
                }
                no_provenance = not any(e["role"] == "evidence" for e in evidence)
                if direct:
                    category = "exclusive"
                elif unknown or no_provenance or (not target_assets and not remaining_assets):
                    category = "unknown"
                elif remaining_assets or table in {"entities", "face_clusters"}:
                    category = "shared"
                elif table in DERIVED:
                    category = "derived"
                else:
                    category = "exclusive"
                if (
                    table
                    in {
                        "facts",
                        "relationships",
                        "entity_properties",
                        "entity_revisions",
                        "semantic_claims",
                        "family_relationships",
                        "family_person_memberships",
                    }
                    and category == "exclusive"
                ):
                    category = (
                        "derived"
                        if row.get("source") in {"derived", "model", "model_inferred"}
                        else "unknown"
                    )
                # Identity is never an exclusive resource even if all known photos are targets.
                if table in {"entities", "face_clusters"}:
                    category = "shared"
                unique_valid = {
                    (e["kind"], e["id"]) for e in evidence if e["role"] == "evidence" and e["reference_valid"]
                }
                unique_target = {
                    (e["kind"], e["id"])
                    for e in evidence
                    if e["role"] == "evidence" and e["target_asset_ids"]
                }
                ref_scopes = {
                    str(self.by_kind["asset"][aid]["scope_id"])
                    for aid in target_assets | remaining_assets
                    if aid in self.by_kind["asset"]
                }
                items.append(
                    {
                        "table": table,
                        "key": key,
                        "locator": {"table": table, "primary_key": key},
                        "source_evidence_fields": self.source_fields(row),
                        "kind": kind,
                        "scope_id": scope,
                        "category": category,
                        "derived": table in DERIVED,
                        "cross_scope": scope not in {None, self.scope}
                        or any(s != self.scope for s in ref_scopes),
                        "evidence": evidence,
                        "target_evidence_count": len(unique_target),
                        "remaining_valid_evidence_count": len(unique_valid),
                        "remaining_valid_asset_count": len(remaining_assets),
                        "evidence_count_complete": not (unknown or no_provenance),
                        "versions": versions,
                        "row_fingerprint": digest({k: v for k, v in row.items() if k != "_key"}),
                        "indirect": any(
                            e["kind"] in {"person_moment", "event", "graph_node"} for e in evidence
                        ),
                        "uncertainties": (
                            ["unresolved_or_missing_source"] if unknown or no_provenance else []
                        )
                        + (
                            ["identity_context_is_not_provenance"]
                            if related_identity and not target_assets
                            else []
                        )
                        + (["scope_projection_requires_rebuild"] if projection else []),
                        "automatic_deletion_allowed": False,
                    }
                )
        return items

    @staticmethod
    def source_fields(row: Row) -> dict[str, object]:
        fields: dict[str, object] = {}
        for name, value in row.items():
            if name.endswith("_json") and name not in {
                "vector_json",
                "embedding_json",
                "representative_embedding_json",
            }:
                parsed, valid = parse_json(value)
                field_value: dict[str, object] = {"value": parsed, "json_valid": valid}
                if not valid:
                    field_value["raw"] = value
                fields[name] = field_value
        return fields

    def valid_asset(self, identity: str) -> bool:
        row = self.by_kind.get("asset", {}).get(identity)
        return bool(
            row
            and row.get("deletion_state", "active") == "active"
            and ("asset", identity) not in self.tombstones
        )

    def scope_for(self, table: str, row: Row) -> str | None:
        if row.get("scope_id") is not None:
            return str(row["scope_id"])
        entity = self.by_kind.get("entity", {}).get(str(row.get("entity_id", row.get("person_id", ""))))
        if entity:
            return str(entity["scope_id"])
        for kind, field in [
            ("asset", "asset_id"),
            ("observation", "observation_id"),
            ("face_instance", "face_instance_id"),
            ("event", "event_id"),
            ("video_job", "job_id"),
            ("rotation", "sequence_id"),
            ("turn", "turn_id"),
        ]:
            parent = self.by_kind.get(kind, {}).get(str(row.get(field, "")))
            if parent and parent.get("scope_id") is not None:
                return str(parent["scope_id"])
            if parent and parent.get("asset_id"):
                asset = self.by_kind.get("asset", {}).get(str(parent["asset_id"]))
                if asset:
                    return str(asset["scope_id"])
        return None
