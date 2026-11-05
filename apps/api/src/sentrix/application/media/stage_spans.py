"""同一单调时钟上的阶段首尾；并行及分段任务按最早开始至最晚结束汇总。"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Lock


class StageSpans:
    def __init__(self) -> None:
        self.intervals: dict[str, tuple[float, float]] = {}
        self._lock = Lock()

    def include(self, intervals: Mapping[str, tuple[float, float]]) -> None:
        with self._lock:
            for key, (start, end) in intervals.items():
                previous = self.intervals.get(key)
                self.intervals[key] = (
                    (min(start, previous[0]), max(end, previous[1])) if previous else (start, end)
                )

    @contextmanager
    def measure(self, key: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.include({key: (start, time.perf_counter())})

    def export(self, origin: float) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, (start, end) in self.intervals.items():
            result[f"{key}_wall"] = round(end - start, 3)
            result[f"{key}_start_seconds"] = round(start - origin, 3)
            result[f"{key}_end_seconds"] = round(end - origin, 3)
        return result
