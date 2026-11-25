"""Analysis reads immutable format-3 facts while another run keeps checkpointing."""

from __future__ import annotations

import copy
import json
import threading
import time

import pytest
from test_api_routes import client as client
from test_error_analysis import await_job, item
from test_scoring_profiles import setup, state, task

from photobench.domain.error_analysis.rules import case_from_item
from photobench.domain.evaluation.profiles import ATM, OPEN, scoring_targets
from photobench.infrastructure.error_analysis.backend import FileAnalysisBackend
from photobench.infrastructure.error_analysis.split_source import with_excerpts
from photobench.infrastructure.error_analysis.worker import lines, run_worker, source
from photobench.infrastructure.results.split_json import ObjectReader
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader


def test_split_rules_preserve_call_facts_without_reading_original_io(tmp_path, monkeypatch):
    large = "__LONG_IO__" * 100000
    record = item(
        model_call_metrics=[
            {
                "call_id": "m1",
                "step_id": "s1",
                "status": "error",
                "error_type": "TimeoutError",
                "messages": [{"role": "user", "content": large}],
                "response": {"content": large},
            }
        ],
        tool_trace=[
            {
                "tool": "inspect_photo",
                "status": "partial",
                "result": {
                    "text": large,
                    "failure_reason": "model_unavailable",
                    "degraded": True,
                    "degraded_reason": "service unavailable",
                    "model_call_observations": [{"status": "error", "error_type": "TimeoutError"}],
                },
            }
        ],
        trace=[{"payload": {"code": "textual_tool_call", "step_id": "s1"}}],
        trace_bundle={"budget": {"max_steps": 100, "max_tool_calls": 600}},
    )
    saved = {"run_id": "b", "status": "completed", "qa_count": 1, "items": [record]}
    writer = SplitResultWriter(tmp_path)
    writer.write(tmp_path / "b", saved)
    writer.close()
    before = (tmp_path / "b" / "split-commit.json").read_bytes()
    original_raw = ObjectReader.raw

    def no_io(self, ref):
        raw = original_raw(self, ref)
        assert b"__LONG_IO__" not in raw
        return raw

    with monkeypatch.context() as patch:
        patch.setattr(ObjectReader, "raw", no_io)
        patch.setattr(SplitRunReader, "state", lambda self: pytest.fail("whole-state hydration"))
        target = tmp_path / "job"
        target.mkdir()
        run_worker(
            str(tmp_path),
            str(target),
            {"kind": "rules", "run_id": "b"},
            threading.Event(),
            threading.Event(),
            {},
        )
    job = json.loads((target / "job.json").read_text())
    assert job["status"] == "completed", job
    case = next(lines(target / "cases.jsonl"))
    assert {"request_timeout", "tool_error", "retrieval_degraded", "model_output_format"} <= {
        event["code"] for event in case["events"]
    }
    assert case["agent_limits"] == [{"max_steps": 100, "max_tool_calls": 600}]
    projection = next(lines(target / "input.jsonl"))
    assert projection["trace_storage"]["revision"] == json.loads(before)["revision"]
    assert "messages" not in projection["model_call_metrics"][0]
    assert (target / "input.jsonl").stat().st_size < 50000
    enriched = with_excerpts(tmp_path, projection)
    assert enriched["analysis_projection"]["excerpt_records"] > 0
    excerpt = enriched["model_call_metrics"][0]["saved_record_excerpt"]
    assert len(excerpt["text"]) <= 3000 and excerpt["next_offset"] is not None
    assert (tmp_path / "b" / "split-commit.json").read_bytes() == before


def test_split_analysis_pins_selected_batch_and_keeps_original_scores(tmp_path):
    saved = state("atm_number", source_qtype="number")
    service, batches, _, _ = setup(
        saved, [OPEN, ATM], [task(), {"score": 2}, {"score": 0, "reason": "units"}]
    )
    for batch in batches:
        service.score(batch, scoring_targets(saved, batch, "all")[0])
    writer = SplitResultWriter(tmp_path)
    writer.write(tmp_path / "r", saved)
    writer.close()
    original = copy.deepcopy(saved)
    pinned = tmp_path / "input.jsonl"
    selected = source(tmp_path, "r", ATM, batches[1]["batch_id"], pinned)
    row = next(lines(pinned))
    assert selected["scoring_batch"] == batches[1]["batch_id"]
    assert selected["scoring_definition"]["config"] == batches[1]["config"]
    assert selected["qa_snapshot"]["definition_sha256"] == saved["qa_snapshot"]["definition_sha256"]
    assert row["evaluation_result"]["score"] == 0
    assert row["evaluation_result"]["profile_id"] == ATM
    assert saved == original
    with pytest.raises(ValueError, match="Unknown scoring batch"):
        source(tmp_path, "r", ATM, "missing", pinned)


def test_split_rules_keep_long_gt_identity_and_legacy_judge_definition(tmp_path):
    record = item(reference_answer="GT" * 5000, answer="answer" * 1600)
    writer = SplitResultWriter(tmp_path)
    writer.write(tmp_path / "b", {"run_id": "b", "status": "completed", "items": [record]})
    writer.close()
    pinned = tmp_path / "input.jsonl"
    source(tmp_path, "b", pinned=pinned)
    original, projected = case_from_item(record, 0), case_from_item(next(lines(pinned)), 0)
    for key in ("identity", "gt_fingerprint", "judge_definitions", "text_truncated", "answer"):
        assert projected[key] == original[key], key


def test_other_running_benchmark_does_not_block_rules_or_checkpoint_writes(tmp_path):
    writer = SplitResultWriter(tmp_path)
    writer.write(tmp_path / "b", {"run_id": "b", "status": "completed", "items": [item()]})
    writer.close()
    stop = threading.Event()
    writes = []
    errors = []

    def checkpoint():
        writer = SplitResultWriter(tmp_path)
        try:
            while not stop.is_set():
                writer.write(
                    tmp_path / "a",
                    {"run_id": "a", "status": "running", "items": [], "summary": {"heartbeat": len(writes)}},
                )
                writes.append(time.monotonic())
                stop.wait(0.02)
        except Exception as error:
            errors.append(error)
        finally:
            writer.close()

    thread = threading.Thread(target=checkpoint)
    thread.start()
    backend = FileAnalysisBackend(tmp_path)
    backend.start(lambda: True)  # A owns inference resources for the whole test.
    try:
        assert backend.latest("b") == {"status": "not_generated"}
        job = backend.submit("b", "rules", {})
        done = await_job(backend, job["job_id"])
        assert done["status"] == "completed", done
        assert len(writes) >= 2 and not errors
        ai = backend.submit(
            "b", "ai", {"parent_job": job["job_id"], "indices": [0], "secret": {"model": "unused"}}
        )
        time.sleep(0.2)
        assert backend.get(ai["job_id"])["status"] == "queued"
        # The AI at the queue head must not prevent a later rules job.
        rule = backend.submit("b", "rules", {})
        assert await_job(backend, rule["job_id"])["status"] == "completed"
        assert backend.get(ai["job_id"])["status"] == "queued"
        assert backend.evidence(rule["job_id"], 0)["item"]["trace_storage"]["revision"]
    finally:
        backend.close()
        stop.set()
        thread.join(5)


def test_split_running_source_rejects_rules_without_loading_items(tmp_path):
    writer = SplitResultWriter(tmp_path)
    writer.write(tmp_path / "a", {"run_id": "a", "status": "running", "items": []})
    writer.close()
    with pytest.raises(ValueError, match="not terminal"):
        source(tmp_path, "a", pinned=tmp_path / "input.jsonl")
    assert not (tmp_path / "input.jsonl").exists()


def test_split_analysis_http_accepts_same_existing_run_as_run_details(client, tmp_path):
    root = tmp_path / "results"
    writer = SplitResultWriter(root)
    writer.write(root / "format3", {"run_id": "format3", "status": "running", "items": []})
    writer.close()
    assert client.get("/api/v1/runs/format3?include_history=false").status_code == 200
    response = client.get("/api/v1/runs/format3/analysis")
    assert response.status_code == 200
    assert response.json()["status"] == "not_generated"
    assert client.get("/api/v1/runs/no-such-run/analysis").status_code == 404
