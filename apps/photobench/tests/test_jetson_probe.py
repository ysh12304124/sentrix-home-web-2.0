"""Worker isolation, original observations and owned-process cleanup."""

from __future__ import annotations

import os

import pytest

from photobench.infrastructure.runtime.jetson_probe import JetsonMemoryWorker, _serve


def test_spawned_worker_has_a_distinct_pid_and_exits_on_close():
    worker = JetsonMemoryWorker("1", {})
    try:
        worker.start()
        result = worker.sample()
        assert result["process_probe_worker_pid"] != os.getpid()
        assert result["memory_observations"]["topology"]["observed_at_monotonic"] > 0
        assert worker.pending_work()
    finally:
        worker.close()
    assert worker.pending_work() == []
    worker.close()
    with pytest.raises(RuntimeError, match="not running"):
        worker.sample()


def test_timeout_keeps_one_request_and_returns_late_reply_without_restamping():
    class Process:
        pid = 123

        def is_alive(self):
            return True

    class Connection:
        sends = []
        available = False

        def send(self, value):
            self.sends.append(value)

        def poll(self, timeout):
            assert timeout == 2.5
            return self.available

        def recv(self):
            return {"memory_observations": {"topology": {"observed_at_monotonic": 5}}}

    worker = JetsonMemoryWorker.__new__(JetsonMemoryWorker)
    worker._process = Process()
    worker._connection = Connection()
    worker._pending = worker._closed = False
    for _ in range(3):
        with pytest.raises(TimeoutError):
            worker.sample()
    assert worker._connection.sends == ["sample"]
    worker._connection.available = True
    result = worker.sample()
    assert result["memory_observations"]["topology"]["observed_at_monotonic"] == 5
    assert worker._connection.sends == ["sample"]
    worker.sample()
    assert worker._connection.sends == ["sample", "sample"]


def test_child_disconnect_exits_and_preserves_read_facts(monkeypatch):
    from photobench.infrastructure.runtime import jetson_telemetry

    fact = {"memory_observations": {"topology": {"observed_at_monotonic": 10}}}
    monkeypatch.setattr(jetson_telemetry, "_augment_local_process_memory", lambda *args, **kwargs: fact)

    class Connection:
        sent = []
        closed = False

        def recv(self):
            if len(self.sent) == 1:
                return "sample"
            raise EOFError

        def send(self, value):
            self.sent.append(value)

        def close(self):
            self.closed = True

    connection = Connection()
    _serve(connection, "8103", {})
    assert connection.sent == [{"ready": True}, fact]
    assert connection.closed


def test_provider_reports_worker_start_failure_without_an_inline_fallback(monkeypatch):
    from photobench.infrastructure.runtime import jetson_telemetry

    class Worker:
        def __init__(self, *args):
            self.closed = False

        def start(self):
            raise RuntimeError("startup failed")

        def close(self):
            self.closed = True

        def pending_work(self):
            return [] if self.closed else ["owned-startup-worker"]

    monkeypatch.setattr(jetson_telemetry, "JetsonMemoryWorker", Worker)
    provider = jetson_telemetry.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8103")
    provider.start_sampling()
    assert provider.process_memory()["status"] == "unavailable"
    assert "startup failed" in provider.process_memory()["reason"]
    assert "owned-startup-worker" in provider.pending_work()
    provider.stop_sampling()
    assert provider.pending_work() == []
