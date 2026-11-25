from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from photobench.infrastructure.results.resume_quarantine import load_resume_quarantine_state


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    from photobench.infrastructure.results import resume_quarantine

    run_id = "q4-resume-test"
    generation = "a" * 32
    monkeypatch.setattr(resume_quarantine, "AUTHORIZED_RUN_ID", run_id)
    monkeypatch.setattr(resume_quarantine, "AUTHORIZED_GENERATION", generation)
    monkeypatch.setattr(resume_quarantine, "AUTHORIZED_SCOPE_ID", "scope-1")
    monkeypatch.setattr(resume_quarantine, "AUTHORIZED_ARCHIVE_RUN_ID", "archive-run")
    monkeypatch.setattr(resume_quarantine, "AUTHORIZED_QA_IDS", frozenset({"large-q"}))
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state = {
        "run_id": run_id,
        "scope_id": "scope-1",
        "qa_count": 2,
        "status": "failed",
        "scoring_results": {"key": {"score": 1}},
        "items": [
            {
                "qa_id": "large-q",
                "execution_status": "completed",
                "answer": "kept answer",
                "judge": {"score": 1},
                "trace_quarantine": {"status": "execution_anomaly"},
                "runtime_turns": [{"index": 0, "answer": "kept answer"}],
            },
            {"qa_id": "small-q", "execution_status": "completed", "answer": "small"},
        ],
    }
    with gzip.open(run_dir / "resume-quarantine-state.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(state, handle)
    state_sha = hashlib.sha256((run_dir / "resume-quarantine-state.json.gz").read_bytes()).hexdigest()
    (run_dir / "snapshot-commit.json").write_text(json.dumps({"generation": generation}))
    manifest = {
        "version": 1,
        "status": "ready",
        "run_id": run_id,
        "scope_id": "scope-1",
        "source_generation": generation,
        "qa_count": 2,
        "item_count": 2,
        "archive_run_id": "archive-run",
        "items": [{"qa_id": "large-q", "reason": "repeated_calls"}],
        "state_sha256": state_sha,
    }
    (run_dir / "resume-quarantine.json").write_text(json.dumps(manifest))
    return run_dir, manifest


def test_loads_only_matching_seed_and_preserves_answer_and_score(tmp_path: Path, monkeypatch) -> None:
    run_dir, _ = _seed(tmp_path, monkeypatch)
    state = load_resume_quarantine_state(run_dir, run_dir.name)
    assert state is not None
    item = state["items"][0]
    assert item["answer"] == "kept answer"
    assert item["execution_status"] == "completed"
    assert item["judge"] == {"score": 1}
    assert state["status"] == "failed"
    assert state["scoring_results"]["key"]["score"] == 1


def test_rejects_source_generation_change_and_bad_checksum(tmp_path: Path, monkeypatch) -> None:
    run_dir, manifest = _seed(tmp_path, monkeypatch)
    (run_dir / "snapshot-commit.json").write_text(json.dumps({"generation": "b" * 32}))
    with pytest.raises(ValueError, match="generation changed"):
        load_resume_quarantine_state(run_dir, run_dir.name)

    (run_dir / "snapshot-commit.json").write_text(json.dumps({"generation": manifest["source_generation"]}))
    with (run_dir / "resume-quarantine-state.json.gz").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_resume_quarantine_state(run_dir, run_dir.name)


def test_rejects_any_quarantined_trace_left_in_seed(tmp_path: Path, monkeypatch) -> None:
    run_dir, manifest = _seed(tmp_path, monkeypatch)
    with gzip.open(run_dir / "resume-quarantine-state.json.gz", "wt", encoding="utf-8") as handle:
        state = {
            "run_id": run_dir.name,
            "scope_id": "scope-1",
            "qa_count": 2,
            "items": [
                {
                    "qa_id": "large-q",
                    "trace_quarantine": {"status": "execution_anomaly"},
                    "tool_trace": [{"tool": "should-not-be-loaded"}],
                },
                {"qa_id": "small-q"},
            ],
        }
        json.dump(state, handle)
    manifest["state_sha256"] = hashlib.sha256(
        (run_dir / "resume-quarantine-state.json.gz").read_bytes()
    ).hexdigest()
    (run_dir / "resume-quarantine.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="still contains top-level trace"):
        load_resume_quarantine_state(run_dir, run_dir.name)


def test_missing_manifest_does_not_change_ordinary_resume(tmp_path: Path) -> None:
    run_dir = tmp_path / "ordinary-run"
    run_dir.mkdir()
    assert load_resume_quarantine_state(run_dir, run_dir.name) is None


def test_default_store_read_uses_reduced_seed_without_opening_original(tmp_path: Path, monkeypatch):
    from photobench.infrastructure.results.store import FileRunStore

    run_dir, _ = _seed(tmp_path, monkeypatch)
    # No original run.json exists. This read must use the verified derived state.
    state = FileRunStore(tmp_path).load_state(run_dir.name)
    assert state["items"][0]["answer"] == "kept answer"
    assert state["items"][0]["judge"] == {"score": 1}
