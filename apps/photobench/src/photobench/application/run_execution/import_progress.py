"""Observe a synchronous import without replaying its side effects or abandoning its request."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from photobench.domain.runs.analysis import now_iso
from photobench.domain.sentrix.ports import CancellableOperations, ProgressTrackedImports, SentrixClient


def import_with_progress(
    client: SentrixClient,
    *,
    scope_id: str,
    directory: str,
    limit: int,
    timeout: int,
    observe: Callable[[dict[str, Any]], None],
    interval: float = 1.0,
    stop_timeout: float = 5.0,
    cancel_event: threading.Event | None = None,
    register_worker: Callable[[threading.Thread], None] | None = None,
) -> dict[str, Any]:
    if not isinstance(client, ProgressTrackedImports):
        observe({"availability": "unsupported", "query_error": "client has no progress capability"})
        legacy_result = client.import_album(
            scope_id=scope_id, directory=directory, limit=limit, timeout=timeout
        )
        observe(
            {
                "availability": "unsupported",
                "query_error": "client has no progress capability",
                "request_status": "returned",
            }
        )
        return legacy_result
    operation_id = uuid.uuid4().hex
    stop = threading.Event()
    observer_errors: list[Exception] = []
    observation: dict[str, Any] = {"operation_id": operation_id, "availability": "waiting"}

    def poll() -> None:
        nonlocal observation
        try:
            payload = client.import_progress(operation_id=operation_id, timeout=3)
            if payload.get("operation_id") != operation_id or payload.get("scope_id") != scope_id:
                raise ValueError("import progress does not match the frozen request")
            observation = {
                **observation,
                "observation": payload,
                "availability": "available",
                "query_error": None,
                "last_received_at": now_iso(),
                "last_checked_at": now_iso(),
            }
        except Exception as error:
            observation = {
                **observation,
                "availability": "unavailable",
                "last_checked_at": now_iso(),
                "query_error": f"{type(error).__name__}: {error}",
            }
        if not stop.is_set():
            observe(dict(observation))

    def watch() -> None:
        try:
            cancellation_sent = False
            cancellation_attempts = 0
            while not stop.is_set():
                if cancel_event is not None and cancel_event.is_set() and not cancellation_sent:
                    if isinstance(client, CancellableOperations):
                        cancellation_attempts += 1
                        try:
                            reply = client.cancel_operation(
                                kind="import", operation_id=operation_id, timeout=3
                            )
                            cancellation_sent = True
                            observe({**observation, "cancellation": reply})
                        except Exception as error:
                            # Retry registration/transport races only a bounded number of times.
                            cancellation_sent = cancellation_attempts >= 6
                            observe(
                                {
                                    **observation,
                                    "cancel_error": str(error),
                                    "cancel_attempts": cancellation_attempts,
                                }
                            )
                    else:
                        cancellation_sent = True
                        observe({**observation, "cancel_error": "product cancellation unsupported"})
                poll()
                if stop.wait(interval):
                    return
        except Exception as error:
            observer_errors.append(error)
            stop.set()

    observe(dict(observation))
    worker = threading.Thread(target=watch, name="photobench-import-progress", daemon=True)
    if register_worker is not None:
        register_worker(worker)
    worker.start()
    request_error: Exception | None = None
    result: dict[str, Any] = {}
    try:
        # Exactly one import POST. The observer requests cooperative product cancellation;
        # the caller still owns this synchronous request until it returns.
        result = client.import_album_tracked(
            scope_id=scope_id,
            directory=directory,
            limit=limit,
            timeout=timeout,
            operation_id=operation_id,
        )
    except Exception as error:
        request_error = error
    finally:
        stop.set()
        worker.join(timeout=stop_timeout)
        if worker.is_alive():
            observe(
                {
                    **observation,
                    "observer_stop_pending": True,
                    "query_error": "import progress observer stop deadline exceeded",
                }
            )
            raise TimeoutError("import progress observer did not stop before deadline")
    if observer_errors:
        raise RuntimeError("import progress could not be saved") from observer_errors[0]
    observation["request_status"] = "outcome_unknown" if request_error else "returned"
    observation["request_error"] = str(request_error) if request_error else None
    # One final read, after joining, includes batch finalization even for fast imports.
    stop.clear()
    try:
        poll()
    finally:
        stop.set()
    if request_error is not None:
        raise request_error
    return result
