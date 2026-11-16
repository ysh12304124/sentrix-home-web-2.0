"""Single-instance result-root lease and auditable orphan recovery (Unix deployments)."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from pathlib import Path
from typing import IO

from photobench.domain.runs.analysis import now_iso
from photobench.infrastructure.results.store import committed_path, read_entry, write_committed_snapshot

log = logging.getLogger(__name__)


def _cleanup_orphan_turns(state: dict) -> int:
    """清理 orphan run 创建的 turn，避免 TurnExecutor 反复认领。

    Run 崩溃后，QA 评测产生的 turn 可能还卡在 Sentrix 数据库中。
    TurnExecutor 会反复尝试认领这些 turn（因为租约过期），导致 VLM 队列满。
    在恢复 orphan run 时，同时清理这些 turn。
    """
    import httpx

    # 从 state 中获取 Sentrix URL
    sentrix_url = state.get("sentrix_url") or "http://127.0.0.1:8191"
    items = state.get("items") or []
    cleaned = 0

    for item in items:
        # 清理 runtime_turns 中的 turn
        runtime_turns = item.get("runtime_turns") or []
        for turn in runtime_turns:
            turn_id = turn.get("turn_id") if isinstance(turn, dict) else None
            if turn_id:
                try:
                    response = httpx.post(
                        f"{sentrix_url}/api/v1/assistant/turns/{turn_id}/cancel",
                        timeout=2,
                    )
                    if response.status_code == 202:
                        cleaned += 1
                except Exception:  # noqa: BLE001 - 清理失败不影响恢复
                    pass
    return cleaned


class FileRunStartupRecovery:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lease: IO[str] | None = None

    def acquire_and_recover(self) -> int:
        self._root.mkdir(parents=True, exist_ok=True)
        lease = (self._root / ".photobench-instance.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
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
                    # 清理 orphan run 创建的 turn，避免 TurnExecutor 反复认领
                    cleaned_turns = _cleanup_orphan_turns(state)
                    if cleaned_turns > 0:
                        state.setdefault("recovery", {})["cleaned_turns"] = cleaned_turns
                        log.info("清理了 %d 个 orphan turn: %s", cleaned_turns, run_dir.name)
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
            fcntl.flock(self._lease, fcntl.LOCK_UN)
            self._lease.close()
            self._lease = None
