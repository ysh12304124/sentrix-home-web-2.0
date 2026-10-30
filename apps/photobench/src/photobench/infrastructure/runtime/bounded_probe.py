"""Bounded daemon probes: no queued work, preserved observation time, no forced thread kill."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


class BoundedProbe:
    def __init__(self, *, limit: int = 4) -> None:
        self.limit = max(1, limit)
        self._cursor = 0
        self._lock = threading.Lock()
        self._jobs: dict[str, tuple[threading.Thread, threading.Event, dict[str, Any]]] = {}

    def collect(
        self, readers: Mapping[str, Callable[[], Any]], *, budget: float, launch: bool = True
    ) -> dict[str, dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, budget)
        ready: dict[str, dict[str, Any]] = {}
        launched: set[str] = set()
        while True:
            with self._lock:
                for key, (thread, event, result) in list(self._jobs.items()):
                    if event.is_set() and not thread.is_alive():
                        del self._jobs[key]
                        if key in readers:
                            ready[key] = dict(result)
                keys = [key for key in readers if launch and key not in ready and key not in launched]
                offset = self._cursor % len(keys) if keys else 0
                for index, key in enumerate(keys[offset:] + keys[:offset]):
                    if key in self._jobs or len(self._jobs) >= self.limit:
                        continue
                    done = threading.Event()
                    result = {}

                    def run(
                        read: Callable[[], Any] = readers[key],
                        target: dict[str, Any] = result,
                        event: threading.Event = done,
                    ) -> None:
                        target["started"] = time.monotonic()
                        try:
                            target["value"] = read()
                        except Exception as error:
                            target["error"] = f"{type(error).__name__}: {error}"
                        finally:
                            target["finished"] = time.monotonic()
                            event.set()

                    thread = threading.Thread(target=run, name="telemetry-probe", daemon=True)
                    self._jobs[key] = (thread, done, result)
                    launched.add(key)
                    try:
                        thread.start()
                    except Exception:
                        del self._jobs[key]
                        raise
                    self._cursor = offset + index + 1
                jobs = dict(self._jobs)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(ready) == len(readers):
                # A zero-budget optional probe still publishes already completed work.
                with self._lock:
                    for key, (thread, event, result) in list(self._jobs.items()):
                        if key in readers and event.is_set() and not thread.is_alive():
                            ready[key] = dict(result)
                            del self._jobs[key]
                return ready
            pending = [event for key, (_, event, _) in jobs.items() if key in readers]
            if not pending:
                return ready
            # Poll within the same deadline so completed slots can serve later PIDs.
            # A blocked reader cannot hold up completed readers in other slots.
            pending[0].wait(min(0.005, remaining))

    def pending_work(self) -> list[str]:
        with self._lock:
            return [key for key, (thread, _, _) in self._jobs.items() if thread.is_alive()]
