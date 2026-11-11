"""Keep disconnected callers from replaying a pipeline with an uncertain outcome."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from sentrix.domain.media.pipeline import PipelineReceiptStore


class TrackPipeline:
    def __init__(self, store: PipelineReceiptStore) -> None:
        self._store = store
        self._lock = Lock()
        self._active_scopes: set[str] = set()

    def get(self, operation_id: str) -> dict[str, Any] | None:
        return self._store.get(operation_id)

    def execute(
        self,
        operation_id: str,
        scope_id: str,
        asset_ids: list[str],
        kind: str,
        action: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            previous = self._store.get(operation_id)
            if previous is not None:
                if (previous["scope_id"], previous["asset_ids"], previous["kind"]) != (
                    scope_id,
                    asset_ids,
                    kind,
                ):
                    raise ValueError("pipeline operation inputs differ from its receipt")
                if previous["status"] == "completed":
                    return dict(previous["result"])
                raise ValueError("pipeline outcome is unfinished or failed; query its receipt")
            if scope_id in self._active_scopes:
                raise ValueError("a pipeline is already running for this scope")
            self._store.create(operation_id, scope_id, asset_ids, kind)
            self._active_scopes.add(scope_id)
        try:
            result = action()
            self._store.finish(operation_id, result, None)
            return result
        except Exception as error:
            self._store.finish(operation_id, None, f"{type(error).__name__}: {error}")
            raise
        finally:
            with self._lock:
                self._active_scopes.remove(scope_id)
