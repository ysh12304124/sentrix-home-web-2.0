"""Media deletion lifecycle: isolation, planning, reconciliation and storage cleanup."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from sentrix.api.deps import get_container
from sentrix.bootstrap.container import Container
from sentrix.infrastructure.persistence.repositories.deletion_plans import SqliteDeletionPlanRepository
from sentrix.infrastructure.persistence.repositories.deletions import SqliteDeletionRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

router = APIRouter(tags=["media-deletions"])


class DeletionRequest(BaseModel):
    scope_id: str = Field(min_length=1, max_length=200)
    library: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/assets/{asset_id}/deletion", status_code=202)
def isolate_asset(
    asset_id: str, payload: DeletionRequest, container: Container = Depends(get_container)
) -> dict[str, object]:
    # User deployments have separate databases, protected by internal_call_guard.
    # The explicit scope must also match the authoritative asset row.
    return SqliteDeletionRepository(container.database).request(
        asset_id, payload.scope_id, payload.idempotency_key, library=payload.library
    )


@router.get("/media-deletions")
def list_library_deletions(
    scope_id: str = Query(default="", max_length=200), container: Container = Depends(get_container)
) -> list[dict[str, object]]:
    return SqliteDeletionRepository(container.database).list_library(scope_id)


@router.get("/media-deletions/{deletion_id}")
def get_deletion(
    deletion_id: str,
    scope_id: str = Query(min_length=1),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    return SqliteDeletionRepository(container.database).get(deletion_id, scope_id)


class PlanRequest(BaseModel):
    scope_id: str = Field(min_length=1, max_length=200)


@router.post("/media-deletions/{deletion_id}/retry", status_code=202)
def retry_library_deletion(
    deletion_id: str, payload: PlanRequest, container: Container = Depends(get_container)
) -> dict[str, object]:
    repo = SqliteDeletionRepository(container.database)
    repo.retry_library(deletion_id, payload.scope_id)
    return repo.get(deletion_id, payload.scope_id)


def _plans(container: Container) -> SqliteDeletionPlanRepository:
    return SqliteDeletionPlanRepository(
        container.database,
        data_dir=container.settings.data_dir,
        qdrant_inventory=getattr(container.vector_index, "deletion_inventory", None),
        qdrant_prefix=getattr(container.vector_index, "prefix", "sentrix_home"),
    )


@router.post("/media-deletions/{deletion_id}/plan")
def generate_plan(
    deletion_id: str,
    payload: PlanRequest,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    return _plans(container).generate(deletion_id, payload.scope_id)


@router.get("/media-deletions/{deletion_id}/plan")
def read_plan(
    deletion_id: str,
    scope_id: str = Query(min_length=1),
    version: int | None = Query(default=None, ge=1),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    return _plans(container).get(deletion_id, scope_id, version)


@router.post("/media-deletions/{deletion_id}/reconcile")
def reconcile_deletion(
    deletion_id: str,
    payload: PlanRequest,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    from sentrix.infrastructure.persistence.repositories.reconciliations import SqliteReconciliationRepository

    return SqliteReconciliationRepository(container.database, plans=_plans(container)).execute(
        deletion_id, payload.scope_id
    )


@router.get("/media-deletions/{deletion_id}/reconciliation")
def read_reconciliation(
    deletion_id: str,
    scope_id: str = Query(min_length=1),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    from sentrix.infrastructure.persistence.repositories.reconciliations import SqliteReconciliationRepository

    return SqliteReconciliationRepository(container.database).get(deletion_id, scope_id)


@router.post("/media-deletions/{deletion_id}/cleanup")
def cleanup_deletion(
    deletion_id: str, payload: PlanRequest, container: Container = Depends(get_container)
) -> dict[str, object]:
    from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository

    return SqliteCleanupRepository(
        container.database,
        data_dir=container.settings.data_dir,
        index=container.vector_index if isinstance(container.vector_index, QdrantVectorIndex) else None,
    ).execute(deletion_id, payload.scope_id)


@router.get("/media-deletions/{deletion_id}/cleanup")
def read_cleanup(
    deletion_id: str, scope_id: str = Query(min_length=1), container: Container = Depends(get_container)
) -> dict[str, object]:
    from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository

    return SqliteCleanupRepository(
        container.database,
        data_dir=container.settings.data_dir,
        index=container.vector_index if isinstance(container.vector_index, QdrantVectorIndex) else None,
    ).get(deletion_id, scope_id)


@router.post("/media-deletions/{deletion_id}/rebuild", status_code=202)
def request_rebuild(
    deletion_id: str, payload: PlanRequest, container: Container = Depends(get_container)
) -> dict[str, object]:
    from sentrix.bootstrap.rebuild_wiring import build_rebuilder

    return build_rebuilder(container).request(deletion_id, payload.scope_id)


@router.get("/media-deletions/{deletion_id}/rebuild")
def get_rebuild(
    deletion_id: str, scope_id: str = Query(min_length=1), container: Container = Depends(get_container)
) -> dict[str, object]:
    from sentrix.bootstrap.rebuild_wiring import build_rebuilder

    return build_rebuilder(container).get(deletion_id, scope_id)
