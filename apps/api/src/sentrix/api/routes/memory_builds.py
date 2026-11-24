"""图片构建状态的只读端点。路由只做传输映射。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.schemas.memory_builds import (
    MemoryBuildAssetPayload,
    MemoryBuildDetailResponse,
    MemoryBuildInventoryResponse,
    MemoryBuildPayload,
    MemoryBuildStageState,
    MemoryEvidencePayload,
)
from sentrix.application.media.manage_memory_builds import (
    ManageMemoryBuilds,
    MemoryBuildAssetState,
    MemoryBuildDetail,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import MemoryEvidence, ObservationBuild

router = APIRouter(tags=["memory-builds"])


def _use_case(container: Container) -> ManageMemoryBuilds:
    return ManageMemoryBuilds(
        assets=container.asset_repository,
        observations=container.observation_repository,
        builds=container.memory_builds,
        evidence=container.memory_evidence,
        tasks=container.memory_build_tasks,
    )


@router.get("/memory-builds", response_model=MemoryBuildInventoryResponse)
def list_memory_builds(
    scope_id: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    container: Container = Depends(get_container),
) -> MemoryBuildInventoryResponse:
    inventory = _use_case(container).inventory(scope_id, limit)
    return MemoryBuildInventoryResponse(
        scope_id=inventory.scope_id,
        counts=dict(inventory.counts),
        assets=[_asset_payload(state) for state in inventory.assets],
    )


@router.get("/memory-builds/{build_id}", response_model=MemoryBuildDetailResponse)
def get_memory_build(
    build_id: str,
    container: Container = Depends(get_container),
) -> MemoryBuildDetailResponse:
    detail = _use_case(container).get(build_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="memory build not found")
    return _detail_payload(detail)


def _asset_payload(state: MemoryBuildAssetState) -> MemoryBuildAssetPayload:
    return MemoryBuildAssetPayload(
        asset_id=state.asset_id,
        file_name=state.file_name,
        media_kind=state.media_kind,
        derived_kind=state.derived_kind,
        parent_asset_id=state.parent_asset_id,
        observation_id=state.observation_id,
        observation_revision=state.observation_revision,
        available_fields=list(state.available_fields),
        build_states=[
            MemoryBuildStageState(stage=stage, status=status) for stage, status in state.build_states
        ],
        status=state.status,
        pending_tasks=state.pending_tasks,
    )


def _detail_payload(detail: MemoryBuildDetail) -> MemoryBuildDetailResponse:
    return MemoryBuildDetailResponse(
        build=_build_payload(detail.build),
        evidence=[_evidence_payload(item) for item in detail.evidence],
    )


def _build_payload(build: ObservationBuild) -> MemoryBuildPayload:
    return MemoryBuildPayload(
        id=build.id,
        scope_id=build.scope_id,
        asset_id=build.asset_id,
        observation_id=build.observation_id,
        observation_revision=build.observation_revision,
        stage=build.stage,
        status=build.status,
        build_key=build.build_key,
        input_hash=build.input_hash,
        model_name=build.model_name,
        protocol_version=build.protocol_version,
        prompt_hash=build.prompt_hash,
        params=dict(build.params),
        input_transform=dict(build.input_transform),
        raw_response_ref=build.raw_response_ref,
        error_category=build.error_category,
        error=build.error,
        attempt=build.attempt,
        started_at=build.started_at,
        finished_at=build.finished_at,
        created_at=build.created_at,
        updated_at=build.updated_at,
    )


def _evidence_payload(evidence: MemoryEvidence) -> MemoryEvidencePayload:
    return MemoryEvidencePayload(
        id=evidence.id,
        scope_id=evidence.scope_id,
        owner_kind=evidence.owner_kind,
        owner_id=evidence.owner_id,
        field_path=evidence.field_path,
        role=evidence.role,
        value=evidence.value,
        value_hash=evidence.value_hash,
        source_kind=evidence.source_kind,
        source_modality=evidence.source_modality,
        source_channel=evidence.source_channel,
        locator=dict(evidence.locator),
        observation_revision=evidence.observation_revision,
        build_id=evidence.build_id,
        correction_id=evidence.correction_id,
        status=evidence.status,
        created_at=evidence.created_at,
        updated_at=evidence.updated_at,
    )
