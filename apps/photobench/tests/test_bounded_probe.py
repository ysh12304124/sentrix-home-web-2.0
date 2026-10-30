"""Real bounded threads, no backlog; old observations retain original times."""

import threading
import time

from photobench.infrastructure.runtime.bounded_probe import BoundedProbe


def test_blocked_reader_does_not_block_other_source_or_create_backlog():
    probe = BoundedProbe(limit=2)
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    def blocked():
        calls.append(1)
        entered.set()
        gate.wait(2)
        return 12

    try:
        start = time.monotonic()
        result = probe.collect({"slow": blocked, "fast": lambda: 4}, budget=0.03)
        assert time.monotonic() - start < 0.3
        assert entered.is_set()
        assert result["fast"]["value"] == 4
        for _ in range(3):
            probe.collect({"slow": blocked}, budget=0)
        assert calls == [1]
        assert probe.pending_work() == ["slow"]
        gate.set()
        for _ in range(100):
            result = probe.collect({"slow": blocked}, budget=0.01)
            if "slow" in result:
                break
        assert result["slow"]["value"] == 12
        assert result["slow"]["started"] <= start + 0.3
        assert probe.pending_work() == []
    finally:
        gate.set()


def test_capacity_uses_fair_rotation_instead_of_starving_last_pid():
    probe = BoundedProbe(limit=1)
    seen = set()
    readers = {str(n): lambda n=n: n for n in range(5)}
    for _ in range(20):
        seen.update(probe.collect(readers, budget=0.01))
    assert seen == set(readers)


def test_reader_failure_is_a_fact_and_capacity_recovers():
    probe = BoundedProbe(limit=1)

    def failure():
        raise PermissionError("denied")

    result = probe.collect({"one": failure}, budget=0.1)
    assert "PermissionError" in result["one"]["error"]
    assert probe.collect({"two": lambda: 2}, budget=0.1)["two"]["value"] == 2


def test_one_sample_collects_more_pids_than_the_parallel_limit():
    probe = BoundedProbe(limit=2)
    readings = probe.collect({str(n): lambda n=n: n for n in range(7)}, budget=0.2)
    assert {key: value["value"] for key, value in readings.items()} == {str(n): n for n in range(7)}
    assert not probe.pending_work()
