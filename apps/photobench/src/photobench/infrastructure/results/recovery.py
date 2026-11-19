"""Cross-platform single-instance result-root lease and auditable orphan recovery."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import IO

import portalocker

from photobench.domain.runs.analysis import now_iso
from photobench.infrastructure.results.store import committed_path, read_entry, write_committed_snapshot

log = logging.getLogger(__name__)


class FileRunStartupRecovery:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lease: IO[str] | None = None

    def acquire_and_recover(self) -> int:
        self._root.mkdir(parents=True, exist_ok=True)
        lease = (self._root / ".photobench-instance.lock").open("a+", encoding="utf-8")
        try:
            portalocker.lock(lease, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except (OSError, portalocker.LockException) as error:
            lease.close()
            raise RuntimeError("PhotoBench results root already has a live owner") from error
        self._lease = lease
        lease.seek(0)
        lease.truncate()
        lease.write(str(os.getpid()))
        lease.flush()
        recovered = 0
        for run_dir in sorted(self._root.iterdir()):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            path = run_dir / "run.json"
            try:
                path = committed_path(run_dir, "run.json")
                try:
                    entry = read_entry(run_dir)
                except ValueError:
                    # Old recovery also accepts records without run_id.
                    entry = json.loads(path.read_bytes())
                if (
                    entry.get("status") not in {"running", "pending", "cancelling"}
                    and (entry.get("rejudge") or {}).get("status") != "running"
                ):
                    continue
                original = path.read_bytes()
                state = json.loads(original)
                if not isinstance(state, dict):
                    continue
                active = state.get("status") in {
                    "running",
                    "pending",
                    "cancelling",
                }
                rejudge = state.get("rejudge")
                orphan_judge = isinstance(rejudge, dict) and rejudge.get("status") == "running"
                if not active and not orphan_judge:
                    continue
                backup = run_dir / ("run.before-orphan-recovery.json")
                # A crash after backup but before replacement must remain recoverable.
                if backup.exists():
                    backup = run_dir / (f"run.before-orphan-recovery.{uuid.uuid4().hex}.json")
                with backup.open("xb") as handle:
                    handle.write(original)
                timestamp = now_iso()
                state["recovery"] = {
                    "reason": "previous_instance_lost",
                    "recovered_at": timestamp,
                    "original_status": state["status"],
                    "backup": backup.name,
                }
                if active:
                    state.update(status="interrupted", finished_at=timestamp)
                if orphan_judge and isinstance(rejudge, dict):
                    state["rejudge"] = {
                        **rejudge,
                        "status": "interrupted",
                        "finished_at": timestamp,
                        "error": "previous_instance_lost",
                    }
                write_committed_snapshot(run_dir, state)
                recovered += 1
            except (OSError, ValueError, KeyError, TypeError):
                log.exception("Could not recover run state: %s", path)
        log.info(
            "PhotoBench instance %s owns %s; recovered %s orphan runs", os.getpid(), self._root, recovered
        )
        return recovered

    def close(self) -> None:
        if self._lease is not None:
            try:
                portalocker.unlock(self._lease)
            finally:
                self._lease.close()
                self._lease = None
