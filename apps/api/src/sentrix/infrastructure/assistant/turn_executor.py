"""问答回合的有界执行循环：认领、租约、围栏与依赖等待。

浏览器断线只影响 SSE 订阅；执行尝试在进程内由本模块负责，重启后按原
``turn_id`` 重新认领并从本轮问题开头重跑。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Callable

from sentrix.domain.assistant.models import TurnStatus

log = logging.getLogger("sentrix.assistant.executor")

LEASE_S = 120
HEARTBEAT_S = 20
POLL_S = 1.0
MAX_ATTEMPTS = 3


class _Heartbeat:
    def __init__(self, turns, turn_id: str, fence: int, clock) -> None:
        self._turns = turns
        self._turn_id = turn_id
        self._fence = fence
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"turn-heartbeat-{turn_id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self._turns.heartbeat(
                    self._turn_id, self._fence, now=self._clock(), lease_s=LEASE_S
                )
            except Exception:  # noqa: BLE001
                log.warning("回合心跳失败 turn=%s", self._turn_id, exc_info=True)


def _backoff(attempt: int) -> float:
    return min(5.0 * 2 ** max(0, attempt - 1), 120.0) + random.uniform(0, 5.0)


class TurnExecutor:
    def __init__(
        self,
        *,
        turns,
        run_turn_factory: Callable[[], object],
        broker,
        dependencies_ready: Callable[[], tuple[bool, str]],
        operation_control=None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._turns = turns
        self._run_turn_factory = run_turn_factory
        self._broker = broker
        self._dependencies_ready = dependencies_ready
        self._operation_control = operation_control
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready_streak = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="turn-executor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self._run_once()
            except Exception:  # noqa: BLE001
                log.exception("回合执行器迭代失败")
                worked = False
            if not worked:
                self._stop.wait(POLL_S)

    def _run_once(self) -> bool:
        now = self._clock()
        record = self._turns.claim_due(now=now, lease_s=LEASE_S)
        if record is not None:
            self._execute(record)
            return True
        ready, _ = self._dependencies_ready()
        self._ready_streak = self._ready_streak + 1 if ready else 0
        return False

    def _execute(self, record) -> None:
        turn_id = record.id
        heartbeat = _Heartbeat(self._turns, turn_id, record.fence, self._clock)
        heartbeat.start()
        self._broker.open(turn_id)
        try:
            if record.cancel_requested:
                self._finish_simple(turn_id, record, TurnStatus.CANCELLED, "cancelled")
                return
            ready, reason = self._dependencies_ready()
            if not ready:
                self._turns.set_execution_state(
                    turn_id,
                    record.fence,
                    now=self._clock(),
                    execution_state="waiting_dependency",
                    waiting_reason=reason,
                    pending=True,
                )
                return
            runner = self._run_turn_factory()
            runner.run(
                turn_id,
                emit=lambda event: self._broker.publish(turn_id, event),
                fence=record.fence,
                attempt_no=record.attempt_no,
            )
        except Exception as error:  # noqa: BLE001 - 崩溃即重试/转失败
            log.exception("回合执行失败 turn=%s", turn_id)
            ready, reason = self._dependencies_ready()
            if not ready:
                self._turns.set_execution_state(
                    turn_id,
                    record.fence,
                    now=self._clock(),
                    execution_state="waiting_dependency",
                    waiting_reason=reason,
                    pending=True,
                )
            elif record.attempt_no < MAX_ATTEMPTS:
                self._turns.set_execution_state(
                    turn_id,
                    record.fence,
                    now=self._clock(),
                    execution_state="retry_scheduled",
                    waiting_reason=str(error),
                    next_retry_at=self._clock() + timedelta(seconds=_backoff(record.attempt_no)),
                    pending=True,
                )
            else:
                self._finish_simple(turn_id, record, TurnStatus.FAILED, "worker_failed")
        finally:
            heartbeat.stop()
            self._broker.finish(turn_id)
            if self._operation_control is not None:
                self._operation_control.finish("turn:" + turn_id)

    def _finish_simple(self, turn_id: str, record, status: TurnStatus, reason: str) -> None:
        self._turns.finish(
            turn_id,
            status=status.value,
            termination_reason=reason,
            answer="",
            attachments=(),
            requirements=(),
            retrieved_candidates=(),
            trace={"worker_error": reason},
            updated_at=self._clock(),
        )
