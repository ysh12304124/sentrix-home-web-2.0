"""Advance library requests using the same durable checkpoints as the staged API."""

import fcntl
import logging

from sentrix.bootstrap.container import Container
from sentrix.domain.media.deletion import InvalidDeletion
from sentrix.infrastructure.persistence.repositories.cleanups import SqliteCleanupRepository
from sentrix.infrastructure.persistence.repositories.deletion_plans import SqliteDeletionPlanRepository
from sentrix.infrastructure.persistence.repositories.reconciliations import SqliteReconciliationRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

log = logging.getLogger(__name__)


def advance_library_deletion(container: Container) -> bool:
    # A process crash releases the lock; the pending row and stage checkpoints survive.
    with (container.settings.data_dir / ".library-deletion.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with container.database.connect() as connection:
            task = connection.execute(
                "SELECT d.deletion_id,d.scope_id,l.state FROM media_deletions d "
                "JOIN media_deletion_lifecycle l USING(deletion_id) "
                "WHERE library_state='pending' ORDER BY d.created_at,d.deletion_id LIMIT 1"
            ).fetchone()
        if task is None:
            return False
        did, scope, state = task
        status = "submitted"
        try:
            if state != "completed":
                index = container.vector_index
                plans = SqliteDeletionPlanRepository(
                    container.database,
                    data_dir=container.settings.data_dir,
                    qdrant_inventory=getattr(index, "deletion_inventory", None),
                    qdrant_prefix=getattr(index, "prefix", "sentrix_home"),
                )
                SqliteReconciliationRepository(container.database, plans=plans).execute(did, scope)
                cleanup = SqliteCleanupRepository(
                    container.database,
                    data_dir=container.settings.data_dir,
                    index=index if isinstance(index, QdrantVectorIndex) else None,
                ).execute(did, scope)
                if cleanup["status"] not in {"storage_cleaned", "storage_cleaned_with_retained"}:
                    status = "blocked" if cleanup["status"] == "blocked" else "failed"
                # The existing 3D SQLite trigger queues the rebuild after storage cleanup.
        except InvalidDeletion:
            log.warning("Library deletion %s blocked by safety validation", did, exc_info=True)
            status = "blocked"
        except Exception:
            log.exception("Library deletion %s could not advance", did)
            status = "failed"
        with container.database.write_transaction() as connection:
            connection.execute(
                "UPDATE media_deletions SET library_state=? WHERE deletion_id=?", (status, did)
            )
        return True
