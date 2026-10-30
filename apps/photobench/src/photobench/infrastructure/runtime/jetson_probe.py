"""A single owned Jetson memory worker, independent of the HTTP/publisher GIL."""

from __future__ import annotations

import multiprocessing
from collections.abc import Mapping
from multiprocessing.connection import Connection
from typing import Any


def _serve(connection: Connection, port: str, services: Mapping[str, Any] | None) -> None:
    # Import in the spawned child; do not fork an HTTP process with live locks.
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe
    from photobench.infrastructure.runtime.jetson_telemetry import _augment_local_process_memory

    memory, topology = BoundedProbe(limit=4), BoundedProbe(limit=1)
    snapshot: dict[str, Any] = {}
    try:
        connection.send({"ready": True})
        while connection.recv() == "sample":
            result = _augment_local_process_memory(
                None,
                model_pid=None,
                endpoint_port=port,
                telemetry_services=services,
                probe=memory,
                topology_probe=topology,
                topology_snapshot=snapshot,
            )
            connection.send(result)
    except (EOFError, BrokenPipeError, OSError):
        pass  # Parent exit/close owns the worker's lifetime, including during IO.
    finally:
        connection.close()


class JetsonMemoryWorker:
    """One request in flight; late replies retain their original source times."""

    def __init__(self, port: str, services: Mapping[str, Any] | None) -> None:
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(
            target=_serve, args=(child, port, services), daemon=True, name="jetson-memory-probe"
        )
        self._pending = False
        self._closed = False
        self._child = child

    def start(self) -> None:
        try:
            self._process.start()
        finally:
            self._child.close()
        try:
            if not self._connection.poll(10) or self._connection.recv() != {"ready": True}:
                raise RuntimeError("Jetson memory worker did not become ready")
        except BaseException:
            self.close()
            raise

    def sample(self) -> dict[str, Any]:
        if self._closed or not self._process.is_alive():
            raise RuntimeError("Jetson memory worker is not running")
        if not self._pending:
            self._connection.send("sample")
            self._pending = True
        if not self._connection.poll(2.5):
            # Do not enqueue another request or use an earlier memory value.
            raise TimeoutError("Jetson memory worker exceeded the 2.5s observation window")
        result: dict[str, Any] = self._connection.recv()
        self._pending = False
        result["process_probe_worker_pid"] = self._process.pid
        return result

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._connection.close()  # Child exits on EOF after any current read.
            self._child.close()
        if self._process.pid is not None:
            self._process.join(timeout=2.5)

    def pending_work(self) -> list[str]:
        return [f"jetson-memory-worker:{self._process.pid}"] if self._process.is_alive() else []
