"""图片记忆导出：一张原生图片一条发布记忆 + 字段来源 + manifest。

只读、0 模型调用。ID 用原始文件名主干；同一导出集合内主干冲突阻止发布，除非用户
提供显式 ID 映射。``location_name`` 只取真实地点，绝不用视觉场景兜底。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentrix.domain.identity.ports import FaceRepository
from sentrix.domain.media.models import (
    Asset,
    AssetStatus,
    MediaType,
    MemoryExportSnapshot,
    Observation,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    MemoryBuildRepository,
    MemoryEvidenceRepository,
    MemoryExportRepository,
    MemoryExportWriter,
    ObservationRepository,
)
from sentrix.domain.places.ports import AssetPlaceRepository

SCHEMA_VERSION = "image-memory-v1"
_PLACEHOLDERS = {"", "其他或不确定", "未知", "未知地点"}
_VERIFIED_STATES = {"user_verified", "auto_trusted"}


@dataclass(frozen=True, slots=True)
class ExportRequest:
    scope_id: str
    asset_ids: Sequence[str] = ()
    compat_mode: str = "strict_v3"
    id_map: Mapping[str, str] | None = None


def _hash(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _line(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _stem(file_name: str) -> str:
    return file_name.rsplit(".", 1)[0] if "." in file_name else file_name


def _timestamp(asset: Asset) -> tuple[str | None, str, str]:
    value = asset.captured_at
    if value is None:
        return None, "unknown", "not_provided"
    precision = asset.capture_time.precision or "datetime"
    if precision == "year":
        return f"{value.year:04d}", precision, "effective"
    if precision == "year_month":
        return f"{value.year:04d}-{value.month:02d}", precision, "effective"
    if precision == "date":
        return value.date().isoformat(), precision, "effective"
    return value.isoformat(), precision or "datetime", "effective"


def _field_status(state: str, value: object, *, legacy: bool = False) -> str:
    if legacy:
        return "legacy"
    if value not in (None, "", [], ()):
        return "effective"
    return state or "not_provided"


class ExportImageMemories:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        places: AssetPlaceRepository,
        faces: FaceRepository,
        evidence: MemoryEvidenceRepository,
        builds: MemoryBuildRepository,
        snapshots: MemoryExportRepository,
        writer: MemoryExportWriter,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._places = places
        self._faces = faces
        self._evidence = evidence
        self._builds = builds
        self._snapshots = snapshots
        self._writer = writer

    def execute(self, request: ExportRequest) -> MemoryExportSnapshot:
        if not request.scope_id.strip():
            raise ValueError("scope_id is required")
        selected = (
            [item for item in (self._assets.get(asset_id) for asset_id in request.asset_ids) if item]
            if request.asset_ids
            else list(self._assets.list_by_scope(request.scope_id, limit=100_000))
        )
        by_id = {asset.id: asset for asset in selected if asset.scope_id == request.scope_id}
        id_map = dict(request.id_map or {})
        records: list[dict[str, object]] = []
        provenance: list[dict[str, object]] = []
        conflicts: list[str] = []
        used_ids: dict[str, str] = {}
        counts: dict[str, int] = {
            "assets_in_scope": len(by_id),
            "native_images": 0,
            "excluded_derived": 0,
            "excluded_non_image": 0,
            "legacy": 0,
            "strict_v3": 0,
            "missing_detail_caption": 0,
            "missing_ocr_text": 0,
            "missing_location": 0,
            "missing_time": 0,
            "missing_tags": 0,
            "missing_entities": 0,
            "path_missing": 0,
            "symlink_assets": 0,
        }
        for asset in sorted(by_id.values(), key=lambda item: item.id):
            if asset.status is not AssetStatus.PROCESSED:
                counts["excluded_non_image"] = counts["excluded_non_image"] + 1
                continue
            if asset.media_type is not MediaType.IMAGE:
                counts["excluded_non_image"] = counts["excluded_non_image"] + 1
                continue
            if asset.derived_kind or asset.parent_asset_id:
                counts["excluded_derived"] = counts["excluded_derived"] + 1
                continue
            counts["native_images"] = counts["native_images"] + 1
            record_id = id_map.get(asset.id) or _stem(asset.file_name)
            if record_id in used_ids and used_ids[record_id] != asset.id:
                conflicts.append(f"duplicate_id:{record_id}:{used_ids[record_id]}:{asset.id}")
            used_ids[record_id] = asset.id
            observation = self._latest_observation(asset.id)
            record, field_provenance = self._record(asset, observation, record_id)
            records.append(record)
            provenance.extend(field_provenance)
            if observation is None or not observation.active_build_id:
                counts["legacy"] = counts["legacy"] + 1
            else:
                counts["strict_v3"] = counts["strict_v3"] + 1
            if not record["caption"]:
                counts["missing_detail_caption"] = counts["missing_detail_caption"] + 1
            if not record["ocr_text"]:
                counts["missing_ocr_text"] = counts["missing_ocr_text"] + 1
            if not record["location_name"]:
                counts["missing_location"] = counts["missing_location"] + 1
            if record["timestamp"] is None:
                counts["missing_time"] = counts["missing_time"] + 1
            if not record["tags"]:
                counts["missing_tags"] = counts["missing_tags"] + 1
            if not record["entities"]:
                counts["missing_entities"] = counts["missing_entities"] + 1
            path = Path(asset.path)
            if not path.exists():
                counts["path_missing"] = counts["path_missing"] + 1
            if path.is_symlink():
                counts["symlink_assets"] = counts["symlink_assets"] + 1

        selection = {
            "scope_id": request.scope_id,
            "asset_ids": sorted(by_id),
            "id_map": id_map,
            "compat_mode": request.compat_mode,
        }
        if conflicts:
            return self._snapshots.create(
                MemoryExportSnapshot(
                    id="",
                    scope_id=request.scope_id,
                    status="blocked",
                    schema_version=SCHEMA_VERSION,
                    compat_mode=request.compat_mode,
                    selection=selection,
                    conflicts=tuple(conflicts),
                    counts=counts,
                )
            )
        snapshot_id = f"mexp_{uuid.uuid4().hex[:16]}"
        records_payload = b"".join(_line(item) for item in records)
        provenance_payload = b"".join(_line(item) for item in provenance)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "compat_mode": request.compat_mode,
            "scope_id": request.scope_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "selection": selection,
            "counts": counts,
            "files": [
                {"name": "image_memories.jsonl", "sha256": hashlib.sha256(records_payload).hexdigest()},
                {
                    "name": "image_memory_provenance.jsonl",
                    "sha256": hashlib.sha256(provenance_payload).hexdigest(),
                },
            ],
            "time_policy": "capture_precision_preserved",
            "place_policy": "physical_location_only",
            "ocr_regions": {"status": "not_provided", "regions": None},
            "missing_rates": {
                key: (float(value) / max(1, counts["native_images"]))
                for key, value in counts.items()
                if key.startswith("missing_")
            },
            "id_mapping": used_ids,
            "model_config_hash": _hash(sorted({str(item["model_used"]) for item in records})),
        }
        manifest_payload = (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        files = self._writer.write(
            snapshot_id,
            {
                "image_memories.jsonl": records_payload,
                "image_memory_provenance.jsonl": provenance_payload,
                "export_manifest.json": manifest_payload,
            },
        )
        return self._snapshots.create(
            MemoryExportSnapshot(
                id=snapshot_id,
                scope_id=request.scope_id,
                status="ready",
                schema_version=SCHEMA_VERSION,
                compat_mode=request.compat_mode,
                selection=selection,
                manifest=manifest,
                directory=snapshot_id,
                files=tuple(files),
                conflicts=(),
                counts=counts,
            )
        )

    def _latest_observation(self, asset_id: str) -> Observation | None:
        observations = self._observations.list_by_asset(asset_id)
        return max(observations, key=lambda item: item.updated_at, default=None)

    def _record(
        self, asset: Asset, observation: Observation | None, record_id: str
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        state = observation.field_states if observation is not None else {}
        short = observation.short_caption if observation is not None else ""
        detail = observation.detail_caption if observation is not None else ""
        legacy_caption = observation.caption if observation is not None else ""
        caption = detail or legacy_caption
        ocr_text = observation.ocr_text if observation is not None else ""
        tags = list(dict.fromkeys(observation.tags)) if observation is not None else []
        entities = self._entities(asset, observation)
        build = (
            self._builds.get(observation.active_build_id)
            if observation is not None and observation.active_build_id
            else None
        )
        timestamp, precision, time_status = _timestamp(asset)
        place = self._places.get(asset.id)
        address_evidence = self._evidence.list_for_owner(
            scope_id=asset.scope_id,
            owner_kind="asset",
            owner_id=asset.id,
            field_path="asset.place.address",
        )
        conflict = any(item.status == "conflict" for item in address_evidence)
        location_name = ""
        place_status = "not_provided"
        if place is not None and not conflict:
            candidate = place.precise_address.strip() or place.display().strip()
            if candidate and candidate not in _PLACEHOLDERS:
                location_name = candidate
                place_status = "effective"
        elif conflict:
            place_status = "conflict"
        record: dict[str, object] = {
            "id": record_id,
            "type": "image",
            "image_path": asset.file_name,
            "timestamp": timestamp,
            "location": (
                {"latitude": asset.captured_location.latitude, "longitude": asset.captured_location.longitude}
                if asset.captured_location is not None
                else None
            ),
            "location_name": location_name,
            "short_caption": short or legacy_caption,
            "caption": caption,
            "ocr_text": ocr_text,
            "tags": tags,
            "entities": entities,
            "model_used": build.model_name if build is not None and build.model_name else None,
            "processing_version": (
                build.protocol_version if build is not None and build.protocol_version else None
            ),
            "processed_at": (
                build.finished_at.isoformat() if build is not None and build.finished_at is not None else None
            ),
        }
        refs = [item.id for item in address_evidence]
        provenance: list[dict[str, object]] = []

        def add(field: str, status: str, source: str, evidence_refs: Sequence[str] = ()) -> None:
            provenance.append(
                {
                    "record_id": record_id,
                    "field": field,
                    "status": status,
                    "source": source,
                    "evidence_refs": list(evidence_refs),
                    "observation_revision": observation.revision if observation is not None else 0,
                    "build_id": build.id if build is not None else "",
                }
            )

        add("id", "effective", "file_name_stem")
        add("image_path", "effective", "asset")
        add("timestamp", time_status, f"capture_time:{precision}", [f"asset:{asset.id}"])
        add("location", "effective" if asset.captured_location else "not_provided", "asset_gps")
        add("location_name", place_status, "asset_place", refs)
        add(
            "short_caption",
            _field_status(
                state.get("short_caption", ""),
                short,
                legacy=bool(not short and legacy_caption),
            ),
            "observation",
        )
        add(
            "caption",
            _field_status(
                state.get("detail_caption", ""),
                detail,
                legacy=bool(not detail and legacy_caption),
            ),
            "observation",
        )
        add("ocr_text", _field_status(state.get("ocr_text", ""), ocr_text), "observation")
        add("tags", _field_status(state.get("tags", ""), tags), "observation")
        add("entities", _field_status(state.get("entities", ""), entities), "observation")
        add("model_used", "effective" if build is not None else "legacy", "observation_build")
        add("processing_version", "effective" if build is not None else "legacy", "observation_build")
        add(
            "processed_at",
            "effective" if build is not None and build.finished_at else "not_provided",
            "observation_build",
        )
        provenance.append(
            {
                "record_id": record_id,
                "field": "ocr_regions",
                "status": "not_provided",
                "source": "not_implemented",
                "regions": None,
                "evidence_refs": [],
            }
        )
        return record, provenance

    def _entities(self, asset: Asset, observation: Observation | None) -> list[dict[str, object]]:
        entities: list[dict[str, object]] = []
        if observation is not None:
            for item in observation.entities:
                entities.append({"name": item.name, "type": item.entity_type})
            for name in observation.people:
                if name and {"name": name, "type": "person"} not in entities:
                    entities.append({"name": name, "type": "person"})
        for record in self._faces.list_occurrences(scope_id=asset.scope_id, asset_id=asset.id, limit=100):
            if record.identity_state in _VERIFIED_STATES and record.person_id:
                name = "" if record.display_label.startswith(("人物…", "本图…")) else record.display_label
            else:
                name = record.display_label
            if name and {"name": name, "type": "person"} not in entities:
                entities.append({"name": name, "type": "person"})
        return entities
