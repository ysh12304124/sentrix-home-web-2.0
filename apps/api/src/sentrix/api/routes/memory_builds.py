"""图片构建状态的只读端点。路由只做传输映射。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.schemas.memory_builds import (
    BackfillRequest,
    BackfillResponse,
    BuildTaskListResponse,
    CancelRebuildRequest,
    CancelRebuildResponse,
    MemoryBuildAssetPayload,
    MemoryBuildDetailResponse,
    MemoryBuildInventoryResponse,
    MemoryBuildPayload,
    MemoryBuildStageState,
    MemoryEvidencePayload,
    RebuildRequest,
    RebuildResponse,
    RebuildRunRequest,
    RebuildRunResponse,
    RebuildTaskPayload,
)
from sentrix.application.media.backfill_memories import (
    BackfillMemories,
    QueueMemoryRebuild,
    RunMemoryRebuildTasks,
    build_task_progress,
)
from sentrix.application.media.manage_memory_builds import (
    ManageMemoryBuilds,
    MemoryBuildAssetState,
    MemoryBuildDetail,
)
from sentrix.application.memories.publish_event_document import PublishEventDocument
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


def _publisher(container: Container) -> PublishEventDocument:
    return PublishEventDocument(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        places=container.asset_place_repository,
        faces=container.face_repository,
        participants=container.event_participant_repository,
        evidence=container.memory_evidence,
        documents=container.event_documents,
    )


@router.post("/memory-builds/backfill", response_model=BackfillResponse)
def backfill_memories(
    payload: BackfillRequest, container: Container = Depends(get_container)
) -> BackfillResponse:
    result = BackfillMemories(
        assets=container.asset_repository,
        observations=container.observation_repository,
        events=container.event_repository,
        lexical=container.lexical_index,
        occurrences=container.person_occurrence_index,
        publisher=_publisher(container),
    ).execute(payload.scope_id, dry_run=payload.dry_run)
    return BackfillResponse(
        scope_id=result.scope_id,
        dry_run=result.dry_run,
        counts=dict(result.counts),
        stages=list(result.stages),
    )


def _queue(container: Container) -> QueueMemoryRebuild:
    return QueueMemoryRebuild(
        assets=container.asset_repository,
        observations=container.observation_repository,
        events=container.event_repository,
        documents=container.event_documents,
        tasks=container.memory_build_tasks,
    )


@router.post("/memory-builds/rebuild", response_model=RebuildResponse)
def submit_memory_rebuild(
    payload: RebuildRequest, container: Container = Depends(get_container)
) -> RebuildResponse:
    result = _queue(container).submit(
        payload.scope_id,
        asset_ids=payload.asset_ids,
        stages=payload.stages,
        dry_run=payload.dry_run,
    )
    return RebuildResponse(
        scope_id=result.scope_id,
        dry_run=result.dry_run,
        tasks=[
            RebuildTaskPayload(
                id=task.id,
                asset_id=task.asset_id,
                stage=task.stage,
                status=task.status,
                attempts=task.attempts,
                last_error=task.last_error,
            )
            for task in result.tasks
        ],
        missing=dict(result.missing),
        estimated_model_calls=result.estimated_model_calls,
    )


@router.post("/memory-builds/run", response_model=RebuildRunResponse)
def run_memory_rebuild(
    payload: RebuildRunRequest, container: Container = Depends(get_container)
) -> RebuildRunResponse:
    result = RunMemoryRebuildTasks(
        tasks=container.memory_build_tasks,
        assets=container.asset_repository,
        observations=container.observation_repository,
        events=container.event_repository,
        observer=container.vlm_observer,
        storage=container.media_storage,
        builds=container.memory_builds,
        evidence=container.memory_evidence,
        lexical=container.lexical_index,
        embedder=container.text_embedder,
        vectors=container.vector_repository,
        publisher=_publisher(container),
        occurrences=container.face_repository,
    ).execute(limit=payload.limit)
    return RebuildRunResponse(claimed=result.claimed, done=result.done, failed=result.failed)


@router.post("/memory-builds/cancel", response_model=CancelRebuildResponse)
def cancel_memory_rebuild(
    payload: CancelRebuildRequest, container: Container = Depends(get_container)
) -> CancelRebuildResponse:
    canceled = container.memory_build_tasks.cancel_scope(payload.scope_id)
    return CancelRebuildResponse(scope_id=payload.scope_id, canceled=canceled)


@router.get("/memory-builds/tasks", response_model=BuildTaskListResponse)
def list_memory_build_tasks(
    scope_id: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    container: Container = Depends(get_container),
) -> BuildTaskListResponse:
    tasks = container.memory_build_tasks.list_for_scope(scope_id, limit=limit)
    counts, payloads = build_task_progress(tasks)
    return BuildTaskListResponse(
        scope_id=scope_id,
        counts=counts,
        tasks=[
            RebuildTaskPayload(
                id=str(item["id"]),
                asset_id=str(item["asset_id"]),
                stage=str(item["stage"]),
                status=str(item["status"]),
                attempts=int(str(item["attempts"])),
                last_error=str(item["last_error"]),
            )
            for item in payloads
        ],
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
