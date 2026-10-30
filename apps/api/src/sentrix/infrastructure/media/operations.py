"""Bounded process-local cancellation ownership; no durable job queue."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from sentrix.domain.media.progress import OperationCancelled


class InProcessOperationControl:
    def __init__(self, active_limit: int = 128, history_limit: int = 128) -> None:
        self._active: dict[str, bool] = {}
        self._history: OrderedDict[str, bool] = OrderedDict()
        self._lock = Lock()
        self._active_limit = active_limit
        self._history_limit = history_limit

    def start(self, operation_id: str) -> None:
        with self._lock:
            if operation_id in self._active or operation_id in self._history:
                raise ValueError("operation already registered")
            if len(self._active) >= self._active_limit:
                raise OverflowError("operation capacity reached")
            self._active[operation_id] = False

    def request_cancel(self, operation_id: str) -> dict[str, str | bool]:
        with self._lock:
            if operation_id in self._active:
                self._active[operation_id] = True
                return {"operation_id": operation_id, "cancel_requested": True, "cleanup_status": "pending"}
            if operation_id in self._history:
                return {
                    "operation_id": operation_id,
                    "cancel_requested": self._history[operation_id],
                    "cleanup_status": "stopped",
                }
            raise KeyError(operation_id)

    def check(self, operation_id: str) -> None:
        with self._lock:
            cancelled = self._active[operation_id]
        if cancelled:
            raise OperationCancelled(operation_id)

    def finish(self, operation_id: str) -> None:
        with self._lock:
            if operation_id not in self._active:
                return
            self._history[operation_id] = self._active.pop(operation_id)
            while len(self._history) > self._history_limit:
                self._history.popitem(last=False)
