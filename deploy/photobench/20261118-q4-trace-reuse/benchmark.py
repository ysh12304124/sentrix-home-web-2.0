"""Verify real completed Q4 answers across two isolated cached checkpoints."""

import gc
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from photobench.application.run_execution.state import RunState
from photobench.infrastructure.results.encoding import open_snapshot
from photobench.infrastructure.results.store import FileResultWriter, committed_path

SOURCE = Path(
    "/home/realmagic/xy/atm-bench-20261110/results/20261118-175624-2026.11.17-atm-sources-addresses-current-resume-7955c3"
)
HERE = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with open_snapshot(path) as handle:
        for text in iter(lambda: handle.read(1024 * 1024), ""):
            h.update(text.encode())
    return h.hexdigest()


pointer = (SOURCE / "snapshot-commit.json").read_bytes()
began = time.monotonic()
with open_snapshot(committed_path(SOURCE, "run.json")) as handle:
    original = json.load(handle)
loaded = time.monotonic()
assert len(original["items"]) == 64 and original["status"] == "failed"
os.environ["PHOTOBENCH_REUSE_TRACE_CHECKPOINTS"] = "1"
os.environ["PHOTOBENCH_COMPRESS_SNAPSHOTS"] = "1"
state = RunState(original)
first = state.snapshot()
frozen = time.monotonic()
del original
gc.collect()
with tempfile.TemporaryDirectory(prefix="trace-reuse-check-", dir=SOURCE.parent) as scratch:
    root = Path(scratch)
    writer = FileResultWriter(root)
    # Keep the cache live for this benchmark; finalization cache release has unit coverage.
    first_status = first["status"]
    state.data["status"] = first["status"] = "running"
    t = time.monotonic()
    writer.write_snapshot(SOURCE.name, first)
    initial_write = time.monotonic() - t
    del first
    gc.collect()
    state.data["status"] = first_status
    # Same traces, changed run metadata: exercise reuse across a real revision.
    t = time.monotonic()
    second = state.snapshot()
    repeat_copy = time.monotonic() - t
    t = time.monotonic()
    writer.write_snapshot(SOURCE.name, second)
    repeat_write = time.monotonic() - t
    del second, state
    gc.collect()
    comparisons = {}
    for name in ["run.json", "results.jsonl"]:
        before = digest(committed_path(SOURCE, name))
        after = digest(committed_path(root / SOURCE.name, name))
        assert before == after, name
        comparisons[name] = {"sha256": before, "decompressed_matches": True}
    assert (SOURCE / "snapshot-commit.json").read_bytes() == pointer
    result = {
        "items": 64,
        "load_seconds": loaded - began,
        "initial_freeze_seconds": frozen - loaded,
        "initial_write_seconds": initial_write,
        "repeat_copy_seconds": repeat_copy,
        "repeat_write_seconds": repeat_write,
        "pair": comparisons,
        "source_pointer_unchanged": True,
    }
    with (HERE / "benchmark-result.json").open("x") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result), flush=True)
