"""Measure one isolated lossless checkpoint against an immutable source generation."""

import argparse
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


def digest(path):
    value = hashlib.sha256()
    with open_snapshot(path) as handle:
        for text in iter(lambda: handle.read(1024 * 1024), ""):
            value.update(text.encode("utf-8"))
    return value.hexdigest()


parser = argparse.ArgumentParser(__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("--scratch", type=Path, required=True)
args = parser.parse_args()
original_pointer = (args.source / "snapshot-commit.json").read_bytes()
source = committed_path(args.source, "run.json")
started = time.monotonic()
with open_snapshot(source) as handle:
    state = json.load(handle)
loaded = time.monotonic()
snapshot = RunState(state).snapshot()
copied = time.monotonic()
del state
gc.collect()
os.environ["PHOTOBENCH_COMPRESS_SNAPSHOTS"] = "1"
with tempfile.TemporaryDirectory(prefix="checkpoint-benchmark-", dir=args.scratch) as directory:
    root = Path(directory)
    began_write = time.monotonic()
    FileResultWriter(root).write_snapshot(snapshot["run_id"], snapshot)
    written = time.monotonic()
    count = len(snapshot["items"])
    del snapshot
    gc.collect()
    target = root / args.source.name
    pair = {}
    for name in ("run.json", "results.jsonl"):
        before, after = committed_path(args.source, name), committed_path(target, name)
        pair[name] = {
            "original_bytes": before.stat().st_size,
            "compressed_bytes": after.stat().st_size,
            "original_sha256": digest(before),
            "decompressed_sha256": digest(after),
        }
        assert pair[name]["original_sha256"] == pair[name]["decompressed_sha256"], name
    assert (args.source / "snapshot-commit.json").read_bytes() == original_pointer
    print(
        json.dumps(
            {
                "run_id": args.source.name,
                "items": count,
                "load_seconds": loaded - started,
                "copy_seconds": copied - loaded,
                "write_seconds": written - began_write,
                "pair": pair,
                "source_pointer_unchanged": True,
            },
            indent=2,
        ),
        flush=True,
    )
