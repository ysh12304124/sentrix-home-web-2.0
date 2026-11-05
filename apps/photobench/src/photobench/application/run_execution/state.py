"""一次 run 的持久状态、相位记账、**后台落盘**与**取消信号**。

1.0 的 ``self.state`` 是一个挂在 ``BenchmarkRun`` 上的裸 dict，配一把 ``threading.RLock``
（``_record_phase`` ``:2278``）。2.0 收成这个类，理由不是整洁：**那份 dict 的形状是
落盘契约**（``run.json`` 会被 ``domain.runs.analysis.run_list_entry`` 读，也会被指到
1.0 ``results/`` 的读者读），散在相位实现里就没人能一眼看出「哪些键必须存在」。

**并发**：QA 池的多个线程会同时 ``append`` item 与写相位进度，GPU 采样线程也会写。
1.0 用一把 RLock 兜住，这里照搬——**换更细的锁换不来任何东西**，因为 ``persist``
（写 run.json）本身就要在一致快照上做。

字典形状与 1.0 ``BenchmarkRun.__init__``（``:1979-2010``）逐键对应，只去掉 2.0
本切片不产出的那几项（云端模型），并加上 2.0 特有的 ``sentrix_url`` 与
``import_directories``。

**S9b-3 补上的三件（都是从 1.0 ``BenchmarkRun`` 上搬下来的）：**

1. **后台 persist 写线程**（1.0 ``_persist_loop`` ``:2056`` / ``persist`` ``:2080`` /
   ``_stop_persist_writer`` ``:2095``）。S9b-2 是同步写快照并如实记了代价。那个线程
   存在的理由有两条，现在两条都成立：不让磁盘 IO 污染 Agent/Judge 的墙钟测量，
   以及让写侧路由查到的实时视图保持新鲜（1.0 的 ``_persist_condition`` 注释）。
   保留 1.0 的**debounce 合并**（同一个短窗口里的多次请求只写一次）与
   ``persist(wait=True)`` 的「等这次落到盘上」语义。
2. **取消信号** ``cancel_event``（1.0 ``self._cancel``）。它现在是真的被
   ``_wait_model_ready`` / ``_await_turn`` / judge 的退避与限流等着的——S9b-2 记的
   「等待不是取消感知的」这条账在这里还清。
3. **实时遥测视图** ``telemetry_live`` 的维护挪到了 ``service._sample_writer`` 里
   （算法在 ``domain.runs.analysis.apply_live_sample``）；``RunState`` 只提供
   ``mutate`` 这个「在锁下改一段状态」的口子。
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from typing import Any

from photobench.domain.results.ports import RunResultWriter
from photobench.domain.runs.analysis import now_iso

#: 1.0 ``:211`` 的 ``PERSIST_DEBOUNCE_SECONDS``。合并同一短窗口里的多次快照请求。
log = logging.getLogger(__name__)

PERSIST_DEBOUNCE_SECONDS = max(0.05, float(os.environ.get("PHOTOBENCH_PERSIST_DEBOUNCE_SECONDS", "0.25")))


class RunState:
    """带锁的 run 状态字典 + 相位记账 + 后台写线程 + 取消信号。"""

    def __init__(
        self,
        initial: dict[str, Any],
        *,
        run_id: str = "",
        writer: RunResultWriter | None = None,
        debounce: float = PERSIST_DEBOUNCE_SECONDS,
        persist_timeout: float = 10.0,
    ) -> None:
        self._lock = threading.RLock()
        self.data = initial
        self._phase_started: dict[str, float] = {}
        self.run_id = run_id
        self._writer = writer
        self.debounce = debounce
        self.persist_timeout = persist_timeout
        #: 取消信号。**它在构造时就存在**（不是被取消时才建）——等待方拿到的必须是
        #: 同一个 Event，否则「取消」对已经在等的那一段永远不可见。
        self.cancel_event = threading.Event()
        self._persist_condition = threading.Condition()
        self._persist_requested = 0
        self._persist_written = 0
        self._persist_stopping = False
        self._persist_error: Exception | None = None
        self._persist_thread: threading.Thread | None = None

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.data.get(key, default)

    def mutate(self, key: str) -> dict[str, Any]:
        """取一个可变的子字典（不存在就建），**调用方需自己持锁**。

        给 ``telemetry_live`` 这种「整块被反复追加」的字段用：把它的维护写成
        「读—改—写」三步在锁外做，两个采样点会互相覆盖。1.0 是在 ``self.lock``
        里直接 ``self.state.setdefault(...)``，这里给它一个等价的名。
        """
        with self._lock:
            value = self.data.get(key)
            if not isinstance(value, dict):
                value = {}
                self.data[key] = value
            return value

    def record_phase(self, phase: str, key: str, value: Any) -> None:
        """1.0 ``:2278``。相位不存在就建一个 ``{"status": "pending"}`` 的空壳，
        这样读侧永远能假定「出现过的相位都有一份形状相同的记录」。"""
        with self._lock:
            bucket = self.data.setdefault("phases", {}).setdefault(phase, {"status": "pending"})
            bucket[key] = value

    def phase_start(self, phase: str) -> None:
        with self._lock:
            self._phase_started[phase] = time.perf_counter()
        self.record_phase(phase, "status", "running")
        self.record_phase(phase, "started_at", now_iso())

    def phase_done(self, phase: str, extra: dict[str, Any] | None = None) -> None:
        """1.0 ``:2289``：**``extra`` 里有 ``total_seconds`` 时不覆盖它**。

        那是给「自己算过更准的耗时」的相位用的（``pipeline_processing`` 从循环开始
        计时、``model_deploy`` 把装载/健康检查分开记），不这么写就会把那份更细的
        计时覆盖成整段墙钟。
        """
        self.record_phase(phase, "status", "done")
        self.record_phase(phase, "finished_at", now_iso())
        with self._lock:
            started = self._phase_started.pop(phase, None)
        if started is not None and not (extra and "total_seconds" in extra):
            self.record_phase(phase, "total_seconds", round(time.perf_counter() - started, 3))
        for key, value in (extra or {}).items():
            self.record_phase(phase, key, value)

    def phase_partial(self, phase: str, extra: dict[str, Any] | None = None) -> None:
        """1.0 ``:2299``：``done`` 之后再压回 ``partial``——这是 ``execute`` 判定
        ``completed_with_errors`` 的唯一依据（``:2389``）。"""
        self.phase_done(phase, extra)
        self.record_phase(phase, "status", "partial")

    def phase_failed(self, phase: str, error: str) -> None:
        self.record_phase(phase, "status", "failed")
        self.record_phase(phase, "error", error)
        self.record_phase(phase, "finished_at", now_iso())

    # ---- 取消 ---------------------------------------------------------------

    def request_cancel(self, source: str = "api") -> None:
        """置取消信号并记账。**不落盘、不回收模型**——那两件在 ``RunExecution.cancel``。

        1.0 ``BenchmarkRun.cancel``（``:2232``）把三件揉在一起；这里拆开的理由是
        回收模型要 ``LifecycleProvider``，而 ``RunState`` 没有（也不该有）它。
        """
        self.cancel_event.set()
        with self._lock:
            self.data["cancel_requested_at"] = now_iso()
            self.data["cancel_source"] = source
            # 1.0：还没开跑的 run 直接落 ``cancelled``；跑着的落 ``cancelling``，
            # 由 ``execute`` 在相位循环发现信号后收成 ``cancelled``。这个区别让前端
            # 能区分「已经停了」与「正在停」。
            if self.data.get("status") == "pending":
                self.data["status"] = "cancelled"
                self.data["finished_at"] = now_iso()
            else:
                self.data["status"] = "cancelling"

    # ---- 后台落盘 -----------------------------------------------------------

    def start_persist_writer(self) -> None:
        """起后台写线程（1.0 在 ``__init__`` 里起，这里在 ``execute`` 开头起）。

        没有 writer 时是空操作——用例里的 ``RunState({"current_phase": ...})`` 直接
        用来调 ``_sample_writer``，那条路径不该要求一个写线程。
        """
        if self._writer is None or self._persist_thread is not None:
            return
        with self._persist_condition:
            self._persist_stopping = False
        self._persist_thread = threading.Thread(
            target=self._persist_loop, daemon=True, name=f"persist-{self.run_id}"
        )
        self._persist_thread.start()

    def snapshot(self) -> dict[str, Any]:
        """Detach a consistent state; never hold the condition while taking state.lock."""
        with self._lock:
            return copy.deepcopy(self.data)

    def _persist_loop(self) -> None:
        try:
            while True:
                with self._persist_condition:
                    self._persist_condition.wait_for(
                        lambda: self._persist_stopping or self._persist_requested > self._persist_written
                    )
                    if self._persist_stopping:
                        return
                    self._persist_condition.wait(timeout=self.debounce)
                    target = self._persist_requested
                try:
                    stored = self.snapshot()
                    writer = self._writer
                    assert writer is not None
                    writer.write_snapshot(self.run_id, stored)
                except Exception as exc:  # noqa: BLE001 — publish failure and stop, never mark as written
                    log.exception("Run snapshot failed: %s", self.run_id)
                    with self._lock:
                        self.data["persistence"] = {"status": "failed", "error": str(exc)}
                    with self._persist_condition:
                        self._persist_error = exc
                        self._persist_condition.notify_all()
                    return
                with self._persist_condition:
                    self._persist_written = target
                    self._persist_condition.notify_all()
        finally:
            with self._persist_condition:
                if not self._persist_stopping and self._persist_error is None:
                    self._persist_error = RuntimeError("persist writer exited unexpectedly")
                self._persist_condition.notify_all()

    def persist(self, wait: bool = False) -> None:
        """Request a snapshot; bounded waiting acknowledges only successfully committed revisions."""
        writer = self._writer
        if writer is None:
            return
        if self._persist_thread is None:
            writer.write_snapshot(self.run_id, self.snapshot())
            return
        deadline = time.monotonic() + self.persist_timeout
        with self._persist_condition:
            if self._persist_error is not None:
                raise RuntimeError(f"benchmark state persistence failed: {self._persist_error}")
            if self._persist_stopping:
                raise RuntimeError("benchmark state writer has stopped")
            self._persist_requested += 1
            target = self._persist_requested
            self._persist_condition.notify_all()
            if not wait:
                return
            while self._persist_written < target:
                if self._persist_error is not None:
                    raise RuntimeError(f"benchmark state persistence failed: {self._persist_error}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("benchmark state persistence deadline exceeded")
                self._persist_condition.wait(timeout=remaining)

    def writer_pending(self) -> bool:
        return bool(self._persist_thread and self._persist_thread.is_alive())

    def stop_persist_writer(self) -> None:
        """Signal stop even on persistence failure; report any writer still holding resources."""
        deadline = time.monotonic() + self.persist_timeout
        try:
            self.persist(wait=True)
        finally:
            with self._persist_condition:
                self._persist_stopping = True
                self._persist_condition.notify_all()
            thread = self._persist_thread
            if thread is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    with self._lock:
                        self.data["persistence"] = {"status": "stop_pending", "error": "writer_stop_timeout"}
                    raise TimeoutError("benchmark state writer did not stop before deadline")


__all__ = ["PERSIST_DEBOUNCE_SECONDS", "RunState"]
