"""Live telemetry must not rewrite question traces or supersede final results."""

import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_run_execution import _execution
from test_run_read import _NoQaMetadata

from photobench.application.run_execution.state import RunState
from photobench.application.runs.index import RunIndex
from photobench.application.runs.read_runs import ReadRuns
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore


def _running():
    return {"run_id": "r", "status": "running", "started_at": "session-a", "items": [], "phases": {}}


def _view(value=1):
    return {
        "run_id": "r",
        "started_at": "session-a",
        "telemetry_live": {"latest": {"temperature_c": value}},
        "gpu_metrics": {"status": "running", "samples_count": value},
    }


def test_sampling_and_publishing_never_copy_or_persist_question_traces():
    execution, _, writer, _, _ = _execution()

    class UncopyableTrace:
        def __deepcopy__(self, memo):
            raise AssertionError("telemetry copied question trace")

    state = RunState({**_running(), "items": [UncopyableTrace()]}, run_id="r")
    state.persist = lambda **kwargs: pytest.fail("telemetry requested a full checkpoint")
    sample = execution._sample_writer(state)
    for index in range(100):
        sample({"index": index, "temperature_c": 50})
    execution._publish_partial_gpu_metrics(state, {"samples_count": 100})
    assert len(writer.gpu_samples) == 100
    assert not writer.snapshots
    assert writer.telemetry_views[0][1]["gpu_metrics"]["samples_count"] == 100
    assert "items" not in writer.telemetry_views[0][1]
    state.data["finished_at"] = "done"
    sample({"index": 101})
    execution._publish_partial_gpu_metrics(state, {"samples_count": 101})
    assert len(writer.gpu_samples) == 100
    assert len(writer.telemetry_views) == 1


def test_live_view_updates_overview_and_history_cache_without_changing_checkpoint(tmp_path):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    writer.write_snapshot("r", _running())
    revision = store.revision("r")
    reader = ReadRuns(store, RunIndex(), _NoQaMetadata(), SimpleNamespace(), lambda: "unused")
    for count in (1, 2):
        writer.write_telemetry_view("r", _view(count))
        assert reader.get_run("r")["telemetry_live"]["latest"]["temperature_c"] == count
        assert reader.get_run("r", include_history=True)["phases"]["gpu_metrics"]["samples_count"] == count
    assert store.revision("r") == revision
    assert store.load_state("r") == _running()


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_late_live_view_cannot_override_terminal_metrics(tmp_path, status):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    state = {
        **_running(),
        "status": status,
        "telemetry_live": {"status": "stopped"},
        "phases": {"gpu_metrics": {"status": "done", "samples_count": 200}},
    }
    writer.write_snapshot("r", state)
    writer.write_telemetry_view("r", _view())
    result = store.load_overview("r")
    assert result["telemetry_live"] == state["telemetry_live"]
    assert result["phases"] == state["phases"]


def test_wrong_session_ignored_and_reset_removes_sidecar(tmp_path):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    writer.write_snapshot("r", {**_running(), "started_at": "session-b"})
    writer.write_telemetry_view("r", _view())
    assert "telemetry_live" not in store.load_overview("r")
    writer.reset_gpu_samples("r")
    assert not (tmp_path / "r" / "telemetry-live.json").exists()


def test_live_disk_full_preserves_last_view_and_answer_checkpoint(tmp_path, monkeypatch):
    writer, store = FileResultWriter(tmp_path), FileRunStore(tmp_path)
    state = {**_running(), "items": [{"qa_id": "q", "answer": "original", "judge": {"score": 2}}]}
    writer.write_snapshot("r", state)
    writer.write_telemetry_view("r", _view())
    original = Path.write_text

    def fail_temporary(path, *args, **kwargs):
        if path.name == "telemetry-live.tmp":
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temporary)
    with pytest.raises(OSError, match="disk full"):
        writer.write_telemetry_view("r", _view(2))
    assert store.load_state("r") == state
    assert json.loads((tmp_path / "r" / "telemetry-live.json").read_text()) == _view()
    assert not list(tmp_path.rglob("*.tmp"))
