"""Read-only candidate acceptance and exact service-idle evidence on server 100."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

UNITS = {
    8872: ("sentrix-atm100-photobench.service", "sentrix-atm100-api.service", 8291),
    8874: ("sentrix-atm100-doubao-photobench.service", "sentrix-atm100-doubao-api.service", 8294),
}


def process(unit):
    pid = int(subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]))
    environment = dict(
        part.split("=", 1)
        for part in Path(f"/proc/{pid}/environ").read_bytes().decode().split("\0")
        if "=" in part
    )
    return pid, environment


def api(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=15) as response:
        return json.load(response)


def idle(port):
    evaluator, product, product_port = UNITS[port]
    evaluator_pid, _ = process(evaluator)
    product_pid, environment = process(product)
    work = api(port, "active-work")["work"]
    progress = api(product_port, "imports/progress")
    turns = None
    if not work:
        database = Path(environment["SENTRIX_DATA_DIR"]) / "sentrix.db"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            turns = db.execute(
                "select count(*) from agent_turns where status in ('pending','running')"
            ).fetchone()[0]
    return {
        "port": port,
        "service_pids": {evaluator: evaluator_pid, product: product_pid},
        "work": work,
        "imports": progress,
        "live_turn_count": turns,
        "idle": not work
        and turns == 0
        and progress["library"]["processing"] == 0
        and progress["postprocess"]["state"] == "idle",
    }


def accept(root, run_id):
    from photobench.domain.error_analysis.rules import Aggregator, case_from_item, snapshot_annotations
    from photobench.infrastructure.error_analysis.worker import lines, source
    from photobench.infrastructure.results.split_store import SplitRunReader

    path = root / run_id / "split-commit.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="chat-trace-accept-", dir="/var/tmp") as temporary:
        pinned = Path(temporary) / "input.jsonl"
        src = source(root, run_id, pinned=pinned)
        rows = {r["qa_id"]: r for r in src.pop("_qa_rows", [])}
        aggregate = Aggregator()
        count = 0
        first = None
        for index, item in enumerate(lines(pinned)):
            aggregate.add(case_from_item(snapshot_annotations(item, rows.get(item.get("qa_id"))), index))
            first = first or item
            count += 1
        report = aggregate.report(src)
        view = SplitRunReader(root, root / run_id)
        try:
            page = view.nodes(0, turn=0, kind="calls", limit=1)
            timing = view.timing(0, page["nodes"][0]["node_id"]) if page["nodes"] else None
        finally:
            view.reader.close()
        result = {
            "run_id": run_id,
            "status": src["status"],
            "items": count,
            "elapsed_s": round(time.monotonic() - started, 3),
            "input_bytes": pinned.stat().st_size,
            "projection": first["analysis_projection"] if first else None,
            "report_version": report.get("rule_version"),
            "timing_read": timing,
            "original_manifest_unchanged": before == hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        assert result["original_manifest_unchanged"]
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle-port", type=int, choices=list(UNITS))
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.idle_port:
        result = idle(args.idle_port)
    else:
        if not args.results_root or not args.run_id:
            parser.error("acceptance requires --results-root and --run-id")
        result = accept(args.results_root, args.run_id)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
