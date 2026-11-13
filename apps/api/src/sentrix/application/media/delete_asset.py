"""Delete authoritative media once, then retry external projections from an outbox."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sentrix.domain.media.ports import MediaDeletionRecord, MediaDeletionRepository, MediaStorage
from sentrix.domain.retrieval.ports import VectorIndex

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteAssetResult:
    asset_id: str
    deleted: bool
    warnings: tuple[str, ...]


class DeleteAsset:
    def __init__(
        self, repository: MediaDeletionRepository, storage: MediaStorage, vectors: VectorIndex
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._vectors = vectors

    def execute(self, asset_id: str) -> DeleteAssetResult | None:
        deleted = self._repository.delete_asset(asset_id)
        if not deleted and not self._repository.was_deleted(asset_id):
            return None
        warnings = self.retry(asset_id)
        return DeleteAssetResult(asset_id=asset_id, deleted=True, warnings=warnings)

    def retry(self, asset_id: str | None = None) -> tuple[str, ...]:
        warnings: list[str] = []
        for root_id, asset_ids, paths, sources, files_done, vectors_done in self._repository.pending_cleanup(
            asset_id
        ):
            errors: list[str] = []
            if not files_done:
                try:
                    skipped = self._storage.delete_assets(asset_ids, paths)
                    files_done = True
                    if skipped:
                        warnings.append(f"{root_id}: {len(skipped)} 个外部源文件保留在媒体目录外")
                except (OSError, ValueError) as error:
                    errors.append(f"file: {error}")
            if not vectors_done:
                try:
                    self._vectors.delete_sources(sources)
                    vectors_done = True
                except Exception as error:  # noqa: BLE001 - external index failure is recorded for retry
                    errors.append(f"vector: {type(error).__name__}: {error}")
            self._repository.mark_cleanup(
                root_id, files_done=files_done, vectors_done=vectors_done, error="; ".join(errors)
            )
            if errors:
                log.warning("media cleanup pending asset_id=%s errors=%s", root_id, errors)
                warnings.append(f"{root_id}: 外部清理待重试（{'、'.join(errors)}）")
        return tuple(warnings)

    def list_records(self, *, scope_id: str | None, limit: int) -> tuple[MediaDeletionRecord, ...]:
        return tuple(self._repository.list_deletions(scope_id=scope_id, limit=limit))
