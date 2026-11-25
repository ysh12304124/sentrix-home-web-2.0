"""Large completed traces must survive checkpoints without repeated encoding."""

import copy
import gzip
import json

import pytest

from photobench.application.run_execution.state import RunState
from photobench.domain.runs.frozen_trace import TRACE_TYPES
from photobench.infrastructure.results.encoding import read_snapshot
from photobench.infrastructure.results.store import FileResultWriter, committed_path


def state_data():
    trace = [{"messages": [{"role": "user", "content": "上下文" * 1000}], "answer": "原始回答"}]
    return {
        "run_id": "r",
        "status": "running",
        "items": [
            {
                "qa_id": "q",
                "answer": "原始回答",
                "judge_status": "pending",
                "tool_trace": trace,
                "runtime_turns": [
                    {"answer": "原始回答", "trace_bundle": {"debug_calls": trace}, "judge": {"score": None}}
                ],
            }
        ],
    }


def test_checkpoint_shares_only_immutable_traces_and_detaches_judge_updates(monkeypatch):
    monkeypatch.setenv("PHOTOBENCH_REUSE_TRACE_CHECKPOINTS", "1")
    state = RunState(state_data())
    first = state.snapshot()
    live = state.data["items"][0]
    with pytest.raises(TypeError, match="immutable"):
        live["tool_trace"][0]["messages"].append({"content": "不能追加"})
    with pytest.raises(TypeError, match="immutable"):
        live["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["answer"] = "不能改写"
    live["judge_status"] = "completed"
    live["runtime_turns"][0]["judge"]["score"] = 1
    second = state.snapshot()
    assert first["items"][0]["judge_status"] == "pending"
    assert first["items"][0]["runtime_turns"][0]["judge"]["score"] is None
    assert second["items"][0]["runtime_turns"][0]["judge"]["score"] == 1
    assert first["items"][0]["tool_trace"] is second["items"][0]["tool_trace"]
    assert (
        second["items"][0]["tool_trace"]
        is second["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"]
    )
    # A retest replaces execution data; the next checkpoint must include it.
    live["tool_trace"] = [{"answer": "新执行"}]
    assert state.snapshot()["items"][0]["tool_trace"][0]["answer"] == "新执行"
    assert first["items"][0]["tool_trace"][0]["answer"] == "原始回答"


@pytest.mark.parametrize("compressed", [False, True])
def test_score_only_commit_reuses_encoded_traces_and_keeps_both_exports(tmp_path, monkeypatch, compressed):
    monkeypatch.setenv("PHOTOBENCH_REUSE_TRACE_CHECKPOINTS", "1")
    monkeypatch.setenv("PHOTOBENCH_COMPRESS_SNAPSHOTS", "1" if compressed else "0")
    original = state_data()
    expected = copy.deepcopy(original)
    state = RunState(original)
    writer = FileResultWriter(tmp_path)
    writer.write_snapshot("r", state.snapshot())
    assert read_snapshot(committed_path(tmp_path / "r", "run.json")) == expected
    real_dumps = json.dumps

    def disallow_reencoding(value, *args, **kwargs):
        if isinstance(value, TRACE_TYPES):
            raise AssertionError("unchanged execution trace encoded again")
        return real_dumps(value, *args, **kwargs)

    state.data["items"][0]["runtime_turns"][0]["judge"]["score"] = 1
    expected["items"][0]["runtime_turns"][0]["judge"]["score"] = 1
    monkeypatch.setattr(json, "dumps", disallow_reencoding)
    writer.write_snapshot("r", state.snapshot())
    assert read_snapshot(committed_path(tmp_path / "r", "run.json")) == expected
    for path in [
        committed_path(tmp_path / "r", "results.jsonl"),
        tmp_path / "r" / ("results.jsonl.gz" if compressed else "results.jsonl"),
    ]:
        raw = gzip.decompress(path.read_bytes()) if compressed else path.read_bytes()
        assert raw.decode() == real_dumps(expected["items"][0], ensure_ascii=False) + "\n"


def test_discarded_trace_fragments_are_released_after_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBENCH_REUSE_TRACE_CHECKPOINTS", "1")
    writer = FileResultWriter(tmp_path)
    state = RunState(state_data())
    writer.write_snapshot("r", state.snapshot())
    state.data["items"] = []
    writer.write_snapshot("r", state.snapshot())
    assert writer._trace_caches == {"r": {}}
    state.data["status"] = "completed"
    writer.write_snapshot("r", state.snapshot())
    assert writer._trace_caches == {}
