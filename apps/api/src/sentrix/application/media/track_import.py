"""Record actual import boundaries; counters describe settled media, not in-flight inference."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sentrix.domain.media.progress import ImportProgress, ImportProgressStore


class TrackImport:
    def __init__(self, store: ImportProgressStore, operation_id: str, scope_id: str) -> None:
        self.operation_id = operation_id
        self._store = store
        self._progress = ImportProgress(
            operation_id=operation_id, scope_id=scope_id, updated_at=datetime.now(UTC).isoformat()
        )
        store.create(self._progress)

    def update(self, **changes: Any) -> None:
        self._progress = replace(
            self._progress,
            **changes,
            sequence=self._progress.sequence + 1,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._store.save(self._progress)
