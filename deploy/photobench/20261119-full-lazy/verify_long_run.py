"""Exercise complete historical traces with incremental saves and bounded concurrent reads."""

from __future__ import annotations
import argparse
import hashlib
import json
import resource
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader
from photobench.domain.runs.stream_exports import sft_json, trace_json
from photobench.domain.evaluation.profile_view import primary_profile_state


def verify(root: Path, run_id: str, output: Path, duration: int):
    reader = SplitRunReader(root, root / run_id)
    state = reader.state()
    result = {"source_revision": reader.revision, "items": len(state["items"]), "qa_count": state["qa_count"]}
    writer = SplitResultWriter(root)
    result["rebuild"] = writer.write(root / run_id, state, source=reader.manifest.get("source"))
    output.with_suffix(".prepare.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"prepared": result}), flush=True)
    pinned = SplitRunReader(root, root / run_id)
    # All original answer fields, score receipts and failure metadata stay identical.
    before = pinned.reader.fingerprint(
        {
            "items": [
                {
                    k: v
                    for k, v in item.items()
                    if k not in {"tool_trace", "execution_trace", "model_call_metrics", "runtime_turns"}
                }
                for item in state["items"]
            ],
            "scores": state.get("scoring_results"),
        }
    )
    counts = {
        "reads": 0,
        "writes": 0,
        "read_max_seconds": 0.0,
        "write_max_seconds": 0.0,
        "core_max_bytes": 0,
        "node_max_bytes": 0,
    }
    lock = threading.Lock()
    deadline = time.monotonic() + duration

    def read_loop(worker):
        index = worker % len(state["items"])
        while time.monotonic() < deadline:
            start = time.monotonic()
            current = SplitRunReader(root, root / run_id)
            overview = current.overview()
            assert overview["item_count"] == len(state["items"])
            current.index()
            core = current.core(index, preview=True)
            page = current.nodes(index)
            size = len(json.dumps(core, ensure_ascii=False).encode())
            node_size = len(json.dumps(page, ensure_ascii=False).encode())
            assert size < 256 * 1024, (index, size)
            assert len(page["nodes"]) <= 50
            if page["nodes"]:
                chunk = current.content(index, page["nodes"][0]["node_id"])
                assert len(json.dumps(chunk, ensure_ascii=False).encode()) < 256 * 1024
            with lock:
                counts["reads"] += 1
                counts["core_max_bytes"] = max(counts["core_max_bytes"], size)
                counts["node_max_bytes"] = max(counts["node_max_bytes"], node_size)
                counts["read_max_seconds"] = max(counts["read_max_seconds"], time.monotonic() - start)
            index = (index + 1) % len(state["items"])
            time.sleep(0.2)

    def write_loop(worker):
        local_writer = SplitResultWriter(root)
        # Four callers share one durable store, exactly as four QA completion threads do.
        while time.monotonic() < deadline:
            metrics = local_writer.write(root / run_id, state, source=reader.manifest.get("source"))
            assert metrics["inserted_bytes"] < 8192, metrics
            with lock:
                counts["writes"] += 1
                counts["write_max_seconds"] = max(counts["write_max_seconds"], metrics["seconds"])
            time.sleep(1)

    with ThreadPoolExecutor(max_workers=52) as pool:
        futures = [pool.submit(read_loop, i) for i in range(48)] + [
            pool.submit(write_loop, i) for i in range(4)
        ]
        for task in futures:
            task.result()
    after_reader = SplitRunReader(root, root / run_id)
    after_state = after_reader.state()
    after = after_reader.reader.fingerprint(
        {
            "items": [
                {
                    k: v
                    for k, v in item.items()
                    if k not in {"tool_trace", "execution_trace", "model_call_metrics", "runtime_turns"}
                }
                for item in after_state["items"]
            ],
            "scores": after_state.get("scoring_results"),
        }
    )
    assert before == after
    result.update(counts)
    result["answer_score_metadata_sha256"] = before
    result["duration_seconds"] = duration
    result["peak_rss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["exports"] = {}
    selected = primary_profile_state(after_state)
    selected["_process_diagnostics"] = {
        str(i["qa_id"]): after_reader.core(n)["process_diagnostics"]
        for n, i in enumerate(after_state["items"])
    }
    for name, export in [("trace", trace_json), ("sft", sft_json)]:
        digest, size = hashlib.sha256(), 0
        started = time.monotonic()
        for fragment in export(selected):
            chunk = fragment.encode()
            digest.update(chunk)
            size += len(chunk)
        result["exports"][name] = {
            "sha256": digest.hexdigest(),
            "bytes": size,
            "seconds": time.monotonic() - started,
        }
        output.write_text(json.dumps(result, indent=2))
        print(json.dumps({name: result["exports"][name]}), flush=True)
    result["status"] = "verified"
    result["peak_rss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=600)
    args = parser.parse_args()
    verify(args.results_root, args.run_id, args.output, args.duration)
