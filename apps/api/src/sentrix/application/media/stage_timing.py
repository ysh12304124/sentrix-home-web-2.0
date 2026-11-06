"""Request-scoped pipeline stage boundaries, including overlapping worker tasks."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Lock
from typing import TypedDict

from sentrix.domain.media.progress import OperationCancelled


class StageSpan(TypedDict):
    stage: str
    asset_id: str | None
    started_at: str
    finished_at: str
    started_offset_seconds: float
    finished_offset_seconds: float
    elapsed_seconds: float
    status: str


_active: ContextVar[StageTimings | None] = ContextVar("pipeline_stage_timings", default=None)


class StageTimings:
    def __init__(self) -> None:
        self._origin = time.perf_counter()
        self._lock = Lock()
        self._spans: list[StageSpan] = []

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = _active.set(self)
        try:
            yield
        finally:
            _active.reset(token)

    @contextmanager
    def measure(self, stage: str, asset_id: str | None = None) -> Iterator[None]:
        start = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        status = "completed"
        try:
            yield
        except OperationCancelled:
            status = "cancelled"
            raise
        except BaseException:
            status = "failed"
            raise
        finally:
            end = time.perf_counter()
            span: StageSpan = {
                "stage": stage,
                "asset_id": asset_id,
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "started_offset_seconds": start - self._origin,
                "finished_offset_seconds": end - self._origin,
                "elapsed_seconds": end - start,
                "status": status,
            }
            with self._lock:
                self._spans.append(span)

    def spans(self) -> list[StageSpan]:
        with self._lock:
            return sorted(
                (span.copy() for span in self._spans), key=lambda span: span["started_offset_seconds"]
            )

    def walls(self) -> dict[str, float]:
        bounds: dict[str, tuple[float, float]] = {}
        for span in self.spans():
            key = span["stage"]
            start, end = span["started_offset_seconds"], span["finished_offset_seconds"]
            if key in bounds:
                start, end = min(start, bounds[key][0]), max(end, bounds[key][1])
            bounds[key] = start, end
        return {key + "_wall": round(end - start, 3) for key, (start, end) in bounds.items()}


@contextmanager
def stage_span(stage: str, asset_id: str | None = None) -> Iterator[None]:
    recorder = _active.get()
    if recorder is None:
        yield
    else:
        with recorder.measure(stage, asset_id):
            yield
