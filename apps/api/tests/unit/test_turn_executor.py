import threading
import time
from types import SimpleNamespace

from sentrix.infrastructure.assistant.turn_executor import TurnExecutor


def test_loop_runs_no_more_than_configured_worker_count():
    class Turns:
        def __init__(self):
            self.records = [SimpleNamespace(id=str(i)) for i in range(3)]
            self.lock = threading.Lock()

        def claim_due(self, **_kwargs):
            with self.lock:
                return self.records.pop(0) if self.records else None

    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    executor = TurnExecutor(
        turns=Turns(),
        run_turn_factory=lambda: None,
        broker=None,
        dependencies_ready=lambda: (True, ""),
        max_workers=3,
    )

    def execute(_record):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 3:
                entered.set()
        release.wait(timeout=2)
        with lock:
            active -= 1

    executor._execute = execute
    executor.start()
    assert entered.wait(timeout=2), "configured workers were not all started"
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with lock:
            if active == 0:
                break
        time.sleep(0.01)
    executor.stop()
    assert peak == 3
