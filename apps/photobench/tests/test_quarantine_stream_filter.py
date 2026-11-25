from __future__ import annotations

import gzip
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/photobench/20261119-q4-quarantine/build_seed.py"
SPEC = importlib.util.spec_from_file_location("q4_quarantine_builder", SCRIPT)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required by the production seed builder")
def test_stream_filter_drops_only_target_traces_regardless_of_key_order(tmp_path: Path):
    state = {
        "items": [
            {
                "tool_trace": [{"payload": "large"}],
                "answer": "preserve this answer",
                "runtime_turns": [{"trace_bundle": [{"step": 1}], "answer": "turn answer"}],
                "judge": {"score": 1},
                "qa_id": "target",
            },
            {
                "qa_id": "other",
                "tool_trace": [{"payload": "keep"}],
                "runtime_turns": [{"trace": [{"step": 2}]}],
            },
        ],
        "qa_count": 2,
    }
    source = tmp_path / "source.json.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        json.dump(state, handle)
    rows = [{"qa_id": "target", "reason": "oversized_trace_requires_review", "raw_sha256": "a" * 64}]
    indexes = BUILDER.find_item_indexes(source, rows)
    assert indexes == [0]

    filter_path = tmp_path / "filter.jq"
    filter_path.write_text(BUILDER.JQ_FILTER, encoding="utf-8")
    result = subprocess.run(
        [
            "jq",
            "--stream",
            "-n",
            "--argjson",
            "q",
            json.dumps(rows),
            "--argjson",
            "indexes",
            json.dumps(indexes),
            "--argjson",
            "heavy",
            json.dumps(["tool_trace", "execution_trace", "model_call_metrics"]),
            "--argjson",
            "turn_heavy",
            json.dumps(["trace", "debug_trace", "trace_bundle", "agent2_trace"]),
            "--arg",
            "archive_run_id",
            "archive-run",
            "--arg",
            "source_generation",
            "a" * 32,
            "-f",
            str(filter_path),
        ],
        input=gzip.decompress(source.read_bytes()),
        capture_output=True,
        check=True,
    )
    filtered = json.loads(result.stdout)
    target, other = filtered["items"]
    assert target["answer"] == "preserve this answer"
    assert target["judge"] == {"score": 1}
    assert target["runtime_turns"] == [{"answer": "turn answer"}]
    assert target["trace_quarantine"]["status"] == "execution_anomaly"
    assert other["tool_trace"] == [{"payload": "keep"}]
    assert other["runtime_turns"] == [{"trace": [{"step": 2}]}]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required by the production seed builder")
def test_item_index_scan_rejects_missing_target_ids(tmp_path: Path):
    source = tmp_path / "source.json.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        json.dump({"items": [{"qa_id": "present"}]}, handle)
    with pytest.raises(RuntimeError, match="missing or duplicated"):
        BUILDER.find_item_indexes(source, [{"qa_id": "absent"}])
