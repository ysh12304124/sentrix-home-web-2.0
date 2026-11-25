"""Validate the deployed variant without touching live execution objects."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from photobench.application.run_execution.state import RunState
from photobench.domain.runs import analysis

BASE = Path(__file__).resolve().parent
candidate = BASE / sys.argv[1]
spec = importlib.util.spec_from_file_location("photobench.application.runs.suites", candidate)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
RunSuites = module.RunSuites

state = RunState(
    {
        "run_id": "isolated-lock-probe",
        "status": "running",
        "qa_count": 1013,
        "items": [
            {"qa_id": str(i), "execution_status": "completed", "judge": {"score": i % 3}} for i in range(1013)
        ],
        "phases": {"qa_eval": {"status": "running", "progress": {"agent_completed": 1013}}},
    }
)
suites = RunSuites.__new__(RunSuites)
suites._lock = threading.RLock()
suites._active = {}
suites._live_entries = {}
suites._threads = {}
suites._auxiliary = {}
suites._reservations = {}
suites._auxiliary_cleanup = {}
suites._auxiliary_samplers = {}
with suites._lock:
    suites._register_active("isolated-lock-probe", SimpleNamespace(state=state))
start = time.perf_counter()
entry = suites.active_states()["isolated-lock-probe"]
refresh_seconds = time.perf_counter() - start
assert entry == analysis.run_list_entry(state.data)
latencies = []
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    state.lock.acquire()
    try:
        for _ in range(20):
            start = time.perf_counter()
            work = pool.submit(suites.work_status)
            value = pool.submit(suites.active_states).result(timeout=1)
            assert work.result(timeout=1)["work"][0]["status"] == "running"
            latencies.append(time.perf_counter() - start)
            assert value["isolated-lock-probe"] == entry
    finally:
        state.lock.release()
print(
    json.dumps(
        {
            "candidate": candidate.name,
            "items": 1013,
            "refresh_seconds": refresh_seconds,
            "locked_reads": 20,
            "locked_max_seconds": max(latencies),
        }
    ),
    flush=True,
)
raise SystemExit(pytest.main([str(BASE / "test_suites.py"), "-q", "-o", "addopts="]))
