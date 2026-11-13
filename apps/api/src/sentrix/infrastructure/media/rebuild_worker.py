"""Small process-owned poller; SQLite leases own all durable work and recovery."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from sentrix.infrastructure.persistence.repositories.rebuilds import SqliteRebuildRepository

log = logging.getLogger(__name__)


class MediaRebuildWorker:
    def __init__(
        self, factory: Callable[[], SqliteRebuildRepository], advance: Callable[[], bool] | None = None
    ) -> None:
        self.factory = factory
        self.advance = advance
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="media-memory-rebuild", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.factory().run_once() or (self.advance and self.advance()):
                    continue
            except Exception:
                log.exception("Media rebuild polling failed; durable work retained")
            self._wake.wait(1)
            self._wake.clear()
