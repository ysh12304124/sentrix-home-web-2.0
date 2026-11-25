"""Read real immutable QA records with the candidate; never write production results."""

from __future__ import annotations

import hashlib
import json
import resource
import time
from pathlib import Path

from photobench.infrastructure.results.split_store import SplitRunReader

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")


def main():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    assert not (HERE / "real-read-response.json").exists()
    # This completed source run is immutable while the separate 17-attempt run proceeds.
    root = BASE / "cloud-results"
    run = root / "20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686"
    before = hashlib.sha256((run / "split-commit.json").read_bytes()).hexdigest()
    reader = SplitRunReader(root, run)
    rows = reader.index()
    assert len(rows) == 1013
    # Include the longest archived trace, not only the first small answer.
    longest = max(range(len(rows)), key=lambda index: rows[index].get("model_call_count") or 0)
    observations = []
    for index in dict.fromkeys([0, longest]):
        started = time.monotonic()
        core = reader.core(index, preview=True)
        assert "process_diagnostics" not in core
        assert not any(field["path"] == "process_diagnostics" for field in core.get("content_fields", []))
        summary = core["trace_read_summary"]
        page = reader.nodes(index)
        assert len(page["nodes"]) <= 50
        contents = []
        for node in page["nodes"][:3]:
            field = (
                "messages.1"
                if node.get("message_count", 0) > 1
                else "messages.0"
                if node.get("message_count")
                else ""
            )
            body = reader.content(index, node["node_id"], field=field, limit=1024)
            assert len(body["text"]) <= 1024
            assert "$photobench_object_v3" not in body["text"]
            contents.append({"node_id": node["node_id"], "field": field, "characters": len(body["text"])})
        if index == 0:
            assert summary["model_call_count"] == 25 and summary["tool_call_count"] == 16
            assert [node["kind"] for node in page["nodes"][:3]] == ["planner", "model", "tool"]
        observations.append(
            {
                "index": index,
                "summary": summary,
                "node_count": page["total"],
                "seconds": time.monotonic() - started,
                "content_reads": contents,
            }
        )
    assert hashlib.sha256((run / "split-commit.json").read_bytes()).hexdigest() == before
    result = {
        "run_id": run.name,
        "items": len(rows),
        "observations": observations,
        "manifest_unchanged": True,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    with (HERE / "real-read-response.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
