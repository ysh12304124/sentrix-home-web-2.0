"""Error analysis: distinct denominators, uncertain legacy evidence and immutable reports."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from photobench.config import Settings
from photobench.domain.error_analysis.exports import export_report
from photobench.domain.error_analysis.rules import (
    Aggregator,
    case_from_item,
    classify,
    compare_reports,
    matches,
)
from photobench.infrastructure.error_analysis.backend import FileAnalysisBackend
from photobench.infrastructure.error_analysis.worker import atomic, run_worker
from photobench.main import create_app


def item(**extra: Any) -> dict[str, Any]:
    return {
        "qa_id": "q1",
        "question": "question",
        "reference_answer": "GT",
        "answer": "answer",
        "question_type": "fact",
        "termination_reason": "answered",
        "expected_action": "answer",
        "judge": {"score": 0, "input": {"model": "J", "messages": [{"role": "system", "content": "judge"}]}},
        "trace_bundle": {"budget": {"max_steps": 8, "max_tool_calls": 6}},
        "model_call_metrics": [{"status": "complete", "diagnostics": {"context_window_tokens": 8192}}],
        "task_judge": {"actual_action": "answer", "correct": True},
        **extra,
    }


def test_legacy_judge_and_internal_call_recording_gaps_are_explicit():
    case = case_from_item(item(tool_trace=[{"tool": "inspect_photo", "result": {}}]), 0)
    assert "judge_call_records" in case["missing"]
    assert "tool_model_call_records" in case["missing"]
    assert case["tool_model_calls"] == case["judge_calls"] == 0


@pytest.mark.parametrize("multi_turn", [False, True])
def test_disabled_evidence_judge_is_not_a_missing_call(multi_turn: bool):
    def turn():
        return {
            "judge": {"score": 1, "call_observations": [{"call_id": "quality"}]},
            "task_judge": {"actual_action": "answer", "call_observations": [{"call_id": "behavior"}]},
            "evidence_judge": {"score": None, "reason": "not_applicable"},
        }

    data = item(**turn())
    if multi_turn:
        data["runtime_turns"] = [turn(), turn()]
    case = case_from_item(data, 0)
    assert "judge_call_records" not in case["missing"]
    assert case["judge_calls"] == (4 if multi_turn else 2)
    # A genuinely attempted legacy judge with no observations must still be flagged.
    target = data["runtime_turns"][0] if multi_turn else data
    target["evidence_judge"] = {"score": None, "reason": "request_timeout"}
    assert "judge_call_records" in case_from_item(data, 0)["missing"]


def report(cases: list[dict[str, Any]], **source: Any) -> dict[str, Any]:
    a = Aggregator()
    for c in cases:
        a.add(c)
    r = a.report({"judge": {"model": "J"}, "budgets": {"execution": {"turn_timeout": 900}}, **source})
    r["source"].update(
        agent_limits=[{"max_steps": 8, "max_tool_calls": 6}],
        context_windows=[8192],
        judge_definition_fingerprints=["known-test-definition"],
    )
    return r


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"error_type": "PromptTooLongError", "error": "软上限"}, "context_soft_limit"),
        ({"error_type": "PromptTooLongError", "error": "只剩12，不足以写完"}, "context_output_insufficient"),
        ({"error_type": "PromptTooLongError"}, "context_unknown"),
        ({"error_type": "OutputTruncatedError"}, "output_truncated"),
        ({"diagnostics": {"error_category": "context_window_exceeded"}}, "context_window_exceeded"),
        ({"error_type": "TimeoutError"}, "request_timeout"),
        ({"error_type": "JSONDecodeError"}, "response_json_invalid"),
    ],
)
def test_error_taxonomy(raw: dict[str, Any], expected: str):
    assert classify(raw)[0] == expected


def test_retries_count_once_per_qa_and_do_not_cross_turns_or_roles():
    calls = [
        {
            "step_id": "step_1",
            "status": "error",
            "error_type": "PromptTooLongError",
            "conversation_turn": 0,
            "call_type": "agent",
        }
        for _ in range(3)
    ]
    calls += [
        {"step_id": "step_1", "status": "complete", "conversation_turn": 1, "call_type": "agent"},
        {"step_id": "step_1", "status": "complete", "conversation_turn": 0, "call_type": "planner"},
    ]
    c = case_from_item(item(model_call_metrics=calls), 0)
    assert len(c["events"]) == 3
    assert {e["recovery"] for e in c["events"]} == {"unknown"}
    calls.append({"step_id": "step_1", "status": "complete", "conversation_turn": 0, "call_type": "agent"})
    c = case_from_item(item(model_call_metrics=calls), 0)
    r = report([c])
    assert r["errors"][0]["events"] == 3
    assert r["errors"][0]["affected_items"] == 1
    assert r["errors"][0]["recovered_items"] == 1
    assert r["errors"][0]["final_failed_items"] == 0
    assert r["counts"]["score_0"] == 1
    assert r["counts"]["execution_normal"] == 1


def test_parse_failure_does_not_mean_invalid_tool_arguments():
    c = case_from_item(
        item(
            termination_reason="parse_failure",
            model_call_metrics=[
                {
                    "status": "complete",
                    "tool_argument_parse_facts": [{"json_object_valid": True}] * 6,
                }
            ],
        ),
        0,
    )
    assert [e["code"] for e in c["events"]] == ["parse_failure_unknown"]
    assert c["tool_json_total"] == 6 and c["tool_json_invalid"] == 0
    c = case_from_item(
        item(
            termination_reason="parse_failure",
            trace=[
                {
                    "payload": {"code": "textual_tool_call"},
                }
            ],
        ),
        0,
    )
    assert [e["code"] for e in c["events"]] == ["model_output_format"]


def test_missing_gt_behaviors_scores_are_missing_and_multilabel_overlaps_explicit():
    c = case_from_item({"qa_id": "q", "question_type": ["a", "b"]}, 0)
    r = report([c])
    assert {"gt", "retrieval_gt", "trace", "quality_score", "behavior"} <= set(c["missing"])
    assert r["denominators"]["quality_valid"] == 0
    assert r["denominators"]["behavior_valid"] == 0
    assert c["dimension_labels"]["question_type"] == ["a", "b"]
    assert "question_type" not in r["groups"]
    assert r["groups"]["content_type"][0]["value"] == "unknown"
    assert not c["signals"]
    assert matches(c, {"score": "None", "question_type": "a", "actual_action": "unknown"})


def test_turn_behavior_filter_requires_same_turn_and_gt_fingerprint_covers_conversation():
    turns = [
        {"message": "a", "expected_action": "answer", "task_judge": {"actual_action": "answer"}},
        {"message": "b", "expected_action": "clarify", "task_judge": {"actual_action": "refuse"}},
    ]
    c = case_from_item(item(runtime_turns=turns), 0)
    assert not matches(c, {"expected_action": "answer", "actual_action": "refuse"})
    changed = copy.deepcopy(turns)
    changed[1]["message"] = "other"
    assert c["identity"] != case_from_item(item(runtime_turns=changed), 0)["identity"]
    assert report([c])["denominators"]["turns"] == 2


@pytest.mark.parametrize("reason", ["step_budget_exhausted", "tool_budget_exhausted", "timeout", "cancelled"])
def test_final_execution_status_is_separate_from_score(reason: str):
    c = case_from_item(item(termination_reason=reason, judge={"score": 2}), 0)
    assert c["execution"] == ("cancelled" if reason == "cancelled" else "failed")
    assert c["score"] == 2


def test_judge_and_tool_errors_preserve_role_and_recovery():
    c = case_from_item(
        item(
            judge={
                "score": None,
                "judge_status": "failed",
                "call_observations": [
                    {
                        "status": "error",
                        "call_id": "j1",
                        "error_type": "TimeoutError",
                        "recovered_by_call_id": "j2",
                    }
                ],
            },
            tool_trace=[{"status": "error", "error": "timed out", "step_id": "t"}],
            model_call_metrics=[{"tool_argument_parse_facts": [{"json_object_valid": False}]}],
        ),
        0,
    )
    assert {e["code"] for e in c["events"]} == {
        "request_timeout",
        "tool_timeout",
        "tool_argument_json_invalid",
    }
    e = next(e for e in c["events"] if e["role"] == "judge")
    assert e["recovery"] == "recovered" and c["execution"] == "normal"


def test_comparison_excludes_changed_duplicate_and_unknown_configuration():
    a = [
        case_from_item(item(qa_id=q), i)
        for i, q in enumerate(["paired", "changed", "duplicate", "duplicate", "left"])
    ]
    b = [
        case_from_item(item(qa_id=q, judge={"score": 2}), i)
        for i, q in enumerate(["paired", "changed", "duplicate", "right"])
    ]
    b[1] = case_from_item(item(qa_id="changed", reference_answer="new GT"), 1)
    result = compare_reports(report(a), report(b), a, b)
    assert result["quality_changes"] == {"improved": 1}
    assert result["duplicates"] == ["duplicate"]
    assert result["pairing_counts"] == {"changed": 1, "unmatched": 2, "paired": 1}
    assert next(r for r in result["rows"] if r["qa_id"] == "changed")["gt_changed"] is True
    bad = compare_reports(report(a), report(b, judge={"model": "other"}), a, b)
    assert bad["quality_changes"] is None and not bad["comparable"]
    unknown = compare_reports(report(a, budgets=None), report(b, budgets=None), a, b)
    assert not unknown["comparable"]


def test_offline_html_escapes_text_and_csv_prevents_formulas():
    c = case_from_item(item(question="<script>alert(1)</script>", answer="=SUM(A1)"), 0)
    r = report([c])
    out = export_report(r, [c], "html", {"score": "0"})
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "https://" not in out and "<meter" not in out  # no exception chart when none recorded
    assert "'=SUM(A1)" in export_report(r, [c], "csv", {})
    assert json.loads(export_report(r, [c], "json", {}))["cases"][0]["index"] == 0


def write_run(root: Path, name: str = "r", **extra: Any) -> Path:
    p = root / name
    p.mkdir(parents=True, exist_ok=True)
    state = {"run_id": name, "status": "completed", "mode": "full", "items": [item()], **extra}
    atomic(p / "run.json", state)
    (p / "results.jsonl").write_text("".join(json.dumps(i) + "\n" for i in state["items"]))
    return p


def await_job(backend: FileAnalysisBackend, key: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        j = backend.result(key)
        if j["status"] not in {"queued", "running"}:
            return j
        time.sleep(0.02)
    pytest.fail("analysis worker did not finish within 15s")


def test_spawn_priority_cancellation_restart_staleness_and_raw_preservation(tmp_path: Path):
    p = write_run(tmp_path)
    original = (p / "results.jsonl").read_bytes()
    busy = threading.Event()
    busy.set()
    backend = FileAnalysisBackend(tmp_path)
    backend.start(busy.is_set)
    try:
        j = backend.submit("r", "rules", {})
        assert backend.submit("r", "rules", {})["job_id"] == j["job_id"]
        assert backend.work()[0]["status"] == "queued"
        backend.cancel(j["job_id"])
        assert backend.get(j["job_id"])["status"] == "cancelled"
        busy.clear()
        j = backend.submit("r", "rules", {})
        done = await_job(backend, j["job_id"])
        assert done["status"] == "completed", done
        assert done["worker_pid"] != __import__("os").getpid()
        assert done["report"]["item_count"] == 1 and not done["stale"]
        assert backend.cases(j["job_id"], {"score": "0"}, 1, 20)["total"] == 1
        assert len(backend.history("r")) == 2
        assert "<html" in backend.export(j["job_id"], "html", {})
        assert (p / "results.jsonl").read_bytes() == original
        (p / "results.jsonl").write_text(json.dumps(item(judge={"score": 2})) + "\n")
        assert backend.result(j["job_id"])["stale"] is True
        busy.set()
        interrupted = backend.submit("r", "rules", {})
        # Simulate a lost supervisor by persisting an active job from a previous process.
        directory = backend.artifacts / ("a" * 32)
        directory.mkdir()
        atomic(directory / "job.json", {**interrupted, "job_id": "a" * 32, "status": "running"})
    finally:
        backend.close()
    restarted = FileAnalysisBackend(tmp_path)
    restarted.start(lambda: False)
    try:
        assert restarted.get("a" * 32)["status"] == "interrupted"
        assert restarted.latest("r")["status"] in {"interrupted", "cancelled"}
    finally:
        restarted.close()


def test_failed_and_cancelled_analysis_does_not_change_run(tmp_path: Path):
    p = write_run(tmp_path, status="cancelled")
    before = (p / "run.json").read_bytes()
    directory = tmp_path / "job"
    directory.mkdir()
    job = {"job_id": "b" * 32, "kind": "rules", "run_id": "r"}
    cancel = threading.Event()
    cancel.set()
    run_worker(str(tmp_path), str(directory), dict(job), cancel, threading.Event(), {})
    assert json.loads((directory / "job.json").read_text())["status"] == "cancelled"
    (p / "results.jsonl").write_text("bad json")
    run_worker(str(tmp_path), str(directory), dict(job), threading.Event(), threading.Event(), {})
    assert json.loads((directory / "job.json").read_text())["status"] == "failed"
    assert (p / "run.json").read_bytes() == before


def test_api_contract_pagination_cancel_and_legacy_source(tmp_path: Path):
    root = tmp_path / "results"
    p = write_run(root, items=[item(task_type="T5_event_summary", angle="activity", difficulty="medium")])
    (p / "results.jsonl").unlink()
    config = tmp_path / "config"
    config.mkdir()
    atomic(
        config / "judge_providers.json",
        {
            "default_provider_id": "test",
            "providers": {
                "test": {"url": "http://127.0.0.1:1/v1", "model": "judge"},
            },
        },
    )
    atomic(config / "vllm_targets.json", {"default_target_id": "local", "targets": {"local": {}}})
    app = create_app(
        Settings(config_root=tmp_path / "config", results_root=root, data_root=tmp_path / "data"),
        scan_in_background=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/runs/r/analysis").json()["status"] == "not_generated"
        response = client.post("/api/v1/runs/r/analysis", json={"kind": "rules"})
        assert response.status_code == 202
        key = response.json()["job_id"]
        done = await_job(app.state.container.analysis_backend, key)
        assert done["status"] == "completed", done
        r = client.get(f"/api/v1/analyses/{key}")
        assert r.status_code == 200 and r.json()["report"]["denominators"]["qa"] == 1
        assert client.get(f"/api/v1/analyses/{key}/cases?score=2").json()["total"] == 0
        pinned = client.get(f"/api/v1/analyses/{key}/evidence/0").json()
        assert pinned["item"]["reference_answer"] == "GT"
        assert pinned["result_fingerprint"] == r.json()["report"]["result_fingerprint"]
        view = client.get(f"/api/v1/analyses/{key}/view?score=0&page_size=1").json()
        assert view["total"] == view["summary"]["item_count"] == 1
        assert client.get(f"/api/v1/analyses/{key}/view?score=2").json()["summary"]["item_count"] == 0
        current = json.loads((p / "run.json").read_text())
        current["items"][0]["reference_answer"] = "new GT after rejudge"
        (p / "run.json").write_text(json.dumps(current))
        assert client.get(f"/api/v1/analyses/{key}/evidence/0").json()["item"]["reference_answer"] == "GT"

        assert client.get(f"/api/v1/analyses/{key}/cases?page_size=101").status_code == 422
        selected = "task_type=T5_event_summary&angle=activity&difficulty=medium&score=low&anomaly=no"
        assert client.get(f"/api/v1/analyses/{key}/cases?{selected}").json()["total"] == 1
        exported = client.get(f"/api/v1/analyses/{key}/export?format=json&{selected}").json()
        assert len(exported["cases"]) == 1 and exported["filters"]["task_type"] == "T5_event_summary"
        assert client.get(f"/api/v1/analyses/{key}/cases?task_type=T3_enumeration").json()["total"] == 0
        selected = "content_type=T5_event_summary&expected_action=answer&behavior_result=correct&score=0"
        assert client.get(f"/api/v1/analyses/{key}/cases?{selected}").json()["total"] == 1
        exported = client.get(f"/api/v1/analyses/{key}/export?format=json&{selected}").json()
        assert len(exported["cases"]) == 1 and exported["filters"]["behavior_result"] == "correct"
        assert client.get(f"/api/v1/analyses/{key}/cases?behavior_result=incorrect").json()["total"] == 0
        assert client.get(f"/api/v1/analyses/{key}/cases?test_scenario=prompt_injection").json()["total"] == 0
        assert client.get(f"/api/v1/analyses/{key}/cases?behavior_result=bad").status_code == 422

        assert client.get(f"/api/v1/analyses/{key}/cases?anomaly=yes").json()["total"] == 0

        assert (
            client.get(f"/api/v1/analyses/{key}/export?format=html")
            .headers["content-type"]
            .startswith("text/html")
        )
        assert len(client.get("/api/v1/runs/r/analyses").json()) == 1
        assert client.post("/api/v1/runs/r/analysis", json={"kind": "ai"}).status_code == 422
        assert client.get("/api/v1/active-work").status_code == 200


@pytest.mark.parametrize(
    ("judge_url", "thinking"),
    [
        ("http://judge/v1/chat/completions", {"enable_thinking": False}),
        ("https://ark.cn-beijing.volces.com/api/plan/v3", {"thinking": {"type": "disabled"}}),
    ],
)
def test_ai_requires_known_refs_provider_explicit_selection_and_never_sends_secret_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, judge_url: str, thinking: dict[str, Any]
):
    import httpx

    import photobench.infrastructure.error_analysis.ai_worker as worker

    p = write_run(tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    c = case_from_item(item(), 0)
    (parent / "cases.jsonl").write_text(json.dumps(c) + "\n")
    (parent / "input.jsonl").write_text(json.dumps(item()) + "\n")
    (parent / "report.json").write_text(json.dumps({"result_fingerprint": "f", "rule_version": 9}))
    original = (p / "results.jsonl").read_bytes()
    targets: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        targets.append(json.loads(request.content))
        answer = {
            "claims": [
                {
                    "kind": "hypothesis",
                    "text": "diagnosis",
                    "limitations": "待验证",
                    "evidence_refs": ["invented-step"] if len(targets) == 1 else ["i0:case"],
                }
            ],
            "missing_evidence": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        worker.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    for i in range(2):
        target = tmp_path / f"ai{i}"
        target.mkdir()
        job = {
            "job_id": str(i) * 32,
            "kind": "ai",
            "run_id": "r",
            "options": {"parent_job": "p", "parent_path": str(parent), "indices": [0]},
        }
        run_worker(
            str(tmp_path),
            str(target),
            job,
            threading.Event(),
            threading.Event(),
            {
                "url": judge_url,
                "key": "secret-value",
                "model": "J",
                "provider_id": "test",
            },
        )
        result = json.loads((target / "diagnosis.json").read_text())
        assert result["cases"][0]["valid"] is bool(i)
        assert result["covered"] == i
        assert "secret-value" not in (target / "job.json").read_text()
        assert "secret-value" not in (target / "diagnosis.json").read_text()
    assert all(all(payload.get(key) == value for key, value in thinking.items()) for payload in targets)
    assert len(targets) == 2
    assert (p / "results.jsonl").read_bytes() == original


def test_backend_compare_and_ai_validation(tmp_path: Path):
    write_run(tmp_path, "left", execution_budgets={"turn_timeout": 900}, judge_model="J")
    write_run(
        tmp_path,
        "right",
        execution_budgets={"turn_timeout": 900},
        judge_model="J",
        items=[
            item(
                judge={
                    "score": 2,
                    "input": {"model": "J", "messages": [{"role": "system", "content": "judge"}]},
                }
            )
        ],
    )
    backend = FileAnalysisBackend(tmp_path)
    backend.start(lambda: False)
    try:
        left = backend.submit("left", "rules", {})["job_id"]
        right = backend.submit("right", "rules", {})["job_id"]
        assert await_job(backend, left)["status"] == "completed"
        assert await_job(backend, right)["status"] == "completed"
        compared = backend.submit("left", "compare", {"left_job": left, "right_job": right})["job_id"]
        done = await_job(backend, compared)
        assert done["status"] == "completed", done
        assert done["report"]["quality_changes"] == {"improved": 1}
        assert "逐题对比" in backend.export(compared, "html", {})
        with pytest.raises(ValueError, match="different run"):
            backend.submit("right", "ai", {"parent_job": left, "indices": [0]})
        for indices in [[], [0, 0], list(range(21)), [99]]:
            with pytest.raises(ValueError):
                backend.submit("left", "ai", {"parent_job": left, "indices": indices})
        (tmp_path / "right" / "results.jsonl").write_text(json.dumps(item(judge={"score": 1})) + "\n")
        assert backend.result(compared)["stale"] is True
    finally:
        backend.close()


def test_tool_internal_model_is_attributed_to_tool_and_not_main_agent():
    c = case_from_item(
        item(
            model_call_metrics=[],
            tool_trace=[
                {
                    "status": "partial",
                    "step_id": "tool1",
                    "result": {
                        "model_call_observations": [
                            {
                                "call_id": "v1",
                                "status": "error",
                                "diagnostics": {
                                    "error_category": "model_output_format",
                                    "request_sent": True,
                                },
                                "error": "not JSON",
                            }
                        ],
                    },
                }
            ],
        ),
        0,
    )
    event = next(e for e in c["events"] if e["code"] == "model_output_format")
    assert event["role"] == "tool_internal" and event["step_id"] == "tool1"
    assert c["model_calls"] == 0 and c["tool_calls"] == 1


def test_snapshot_pointer_change_cannot_mix_metadata_and_results(tmp_path, monkeypatch):
    from photobench.infrastructure.error_analysis import worker
    from photobench.infrastructure.results.store import write_committed_snapshot

    directory = tmp_path / "r"
    write_committed_snapshot(directory, {"run_id": "r", "status": "completed", "items": [item()]})
    old_rows = worker.committed_path(directory, "results.jsonl")
    write_committed_snapshot(directory, {"run_id": "r", "status": "failed", "items": []})
    actual = worker.committed_path
    reads = 0

    def flip(run_dir, name):
        nonlocal reads
        reads += 1
        return old_rows if reads == 1 else actual(run_dir, name)

    monkeypatch.setattr(worker, "committed_path", flip)
    selected = worker.source(tmp_path, "r")
    assert selected["status"] == "completed"
    assert selected["path"] == str(old_rows)
    assert selected["revision"] != worker.revision(tmp_path, "r")


def test_quality_and_classification_filters_include_correct_and_partial_without_inventing_labels():
    cases = [
        case_from_item(item(qa_id=f"q{i}", judge={"score": score}, task_type=task), i)
        for i, (score, task) in enumerate(
            [(2, "T5_event_summary"), (1, "T3_enumeration"), (0, "T1_retrieve_media"), (None, None)]
        )
    ]
    # Correctness and runtime anomalies remain independent.
    cases[0]["events"] = [{"code": "request_timeout", "recovery": "recovered", "turn": 0}]
    assert [c["index"] for c in cases if matches(c, {"score": "low"})] == [1, 2]
    assert [c["index"] for c in cases if matches(c, {"score": "2", "anomaly": "yes"})] == [0]
    assert [c["index"] for c in cases if matches(c, {"task_type": "T3_enumeration", "score": "low"})] == [1]
    assert [c["index"] for c in cases if matches(c, {"task_type": "unknown", "score": "None"})] == [3]
    assert [c["index"] for c in cases if matches(c, {"anomaly": "no"})] == [1, 2, 3]
    assert not matches(cases[0], {"difficulty": "hard"})
    r = report(cases)
    assert r["counts"]["score_2"] == 1 and r["counts"]["affected_items"] == 1
    assert r["denominators"]["quality_valid"] == 3
    assert r["groups"]["content_type"][0]["score_2"] == 1


def test_task_aliases_merge_before_counting_and_filters_exports_share_the_category():
    raw = [
        item(qa_id="a", task_type="T1_retrieve_media", judge={"score": 2}),
        item(qa_id="b", task_type="T1_media_retrieval", judge={"score": 0}),
        item(qa_id="c", task_type=["T1_retrieve_media", "T1_media_retrieval"], judge={"score": 1}),
        item(qa_id="d", task_type="behavior", judge={"score": None}),
    ]
    before = copy.deepcopy(raw)
    cases = [case_from_item(row, i) for i, row in enumerate(raw)]
    r = report(cases)
    assert raw == before
    assert r["rule_version"] == 10
    media, behavior = r["groups"]["content_type"]
    assert media == {
        "value": "T1_retrieve_media",
        "total": 3,
        "failed": 0,
        "affected": 0,
        "score_2": 1,
        "score_0": 1,
        "score_1": 1,
    }
    assert behavior["total"] == 1 and r["item_count"] == 4
    assert cases[2]["dimension_labels"]["task_type"] == ["T1_retrieve_media"]
    for alias in ["T1_retrieve_media", "T1_media_retrieval", "媒体检索"]:
        selected = [c for c in cases if matches(c, {"task_type": alias})]
        assert len(selected) == 3
        assert len(json.loads(export_report(r, selected, "json", {"task_type": alias}))["cases"]) == 3
    # Old saved case indices and links are also matched by the same category.
    legacy = copy.deepcopy(cases[0])
    legacy["dimension_labels"]["task_type"] = ["T1_media_retrieval"]
    assert matches(legacy, {"task_type": "T1_retrieve_media"})
    # Annotation-only changes are distinct from changes to questions or evidence.
    assert cases[0]["identity"] == cases[1]["identity"]
    assert cases[0]["annotation_fingerprint"] != cases[1]["annotation_fingerprint"]
    assert (
        case_from_item(item(question_type="media_retrieval"), 0)["dimensions"]["question_type"]
        == "media_retrieval"
    )


def test_old_category_rule_report_is_stale_without_changing_saved_artifacts(tmp_path: Path):
    write_run(tmp_path)
    backend = FileAnalysisBackend(tmp_path)
    backend.start(lambda: False)
    try:
        job = backend.submit("r", "rules", {})
        done = await_job(backend, job["job_id"])
        assert not done["stale"]
        path = backend.artifacts / job["job_id"] / "report.json"
        saved = json.loads(path.read_text())
        saved["rule_version"] = 2
        atomic(path, saved)
        original = path.read_bytes()
        assert backend.latest("r")["stale"]
        assert backend.result(job["job_id"])["stale"]
        assert path.read_bytes() == original
    finally:
        backend.close()


def test_content_behavior_and_scenarios_have_separate_denominators_and_preserve_source():
    raw = [
        item(qa_id="wrong-fact", task_type="T2_fact_qa", judge={"score": 0}),
        item(
            qa_id="safety",
            task_type="behavior",
            question_type="prompt_injection",
            expected_action="refuse",
            judge={"score": 2},
            task_judge={"actual_action": "refuse", "correct": True},
        ),
        item(
            qa_id="multi",
            task_type="behavior",
            question_type="multi_turn_clarify",
            expected_action="answer",
            runtime_turns=[
                {
                    "expected_action": "clarify",
                    "termination_reason": "answered",
                    "judge": {"score": 0},
                    "task_judge": {"actual_action": "answer", "correct": False},
                },
                {
                    "expected_action": "answer",
                    "termination_reason": "answered",
                    "judge": {"score": 2},
                    "task_judge": {"actual_action": "answer", "correct": True},
                },
            ],
        ),
    ]
    before = copy.deepcopy(raw)
    cases = [case_from_item(row, i) for i, row in enumerate(raw)]
    r = report(cases)
    assert raw == before
    assert set(r["groups"]) == {
        "ability_taxonomy_version",
        "ability_family",
        "ability_type",
        "benchmark_group",
        "content_type",
        "content_subtype",
        "difficulty",
        "answerability",
        "media_type",
        "conversation_type",
    }
    content = {g["value"]: g for g in r["groups"]["content_type"]}
    assert content["T2_fact_qa"]["total"] == 1 and content["unknown"]["total"] == 2
    assert cases[1]["source_labels"]["task_type"] == ["behavior"]
    assert cases[1]["dimensions"]["test_scenario"] == "prompt_injection"
    behaviors = {g["value"]: g for g in r["behavior_groups"]}
    assert behaviors["answer"]["total"] == 2
    assert behaviors["answer"]["correct"] == 2  # Wrong fact still has the correct action.
    assert behaviors["clarify"]["total"] == behaviors["clarify"]["incorrect"] == 1
    assert behaviors["refuse"]["total"] == behaviors["refuse"]["correct"] == 1
    assert sum(g["total"] for g in behaviors.values()) == r["denominators"]["turns"] == 4
    assert not matches(cases[2], {"expected_action": "clarify", "behavior_result": "correct"})
    assert matches(cases[2], {"expected_action": "clarify", "behavior_result": "incorrect"})
    assert matches(cases[1], {"test_scenario": "prompt_injection", "expected_action": "refuse"})
    assert not matches(cases[1], {"test_scenario": "prompt_injection", "expected_action": "answer"})
    assert matches(cases[0], {"score": "0", "behavior_result": "correct"})
    html = export_report(r, cases, "html", {})
    assert "行为判断" not in html and "按得分看内容题型" in html
    assert "未标注内容题型" in html and "回答行为（按对话轮）" in html
    assert "behavior_turns" in export_report(r, cases, "csv", {})


def test_multilabel_heatmap_uses_deduplicated_content_labels_not_joined_keys():
    c = case_from_item(
        item(
            task_type=["T2_fact_qa", "T1_retrieve_media", "T1_media_retrieval"],
            question_type=["prompt_injection", "evidence_insufficient"],
            angle="event_summary",
        ),
        0,
    )
    c["events"] = [{"code": "request_timeout", "recovery": "recovered", "turn": 0}]
    r = report([c])
    assert {x["content_type"] for x in r["heatmap"]} == {"T2_fact_qa", "T1_retrieve_media"}
    assert all(x["count"] == 1 for x in r["heatmap"])
    assert c["dimensions"]["angle"] == "unknown" and c["source_labels"]["angle"] == ["event_summary"]
    assert {x["value"] for x in r["behavior_scenarios"]} == {"prompt_injection", "evidence_insufficient"}
    assert len(r["behavior_groups"]) == 1 and r["denominators"]["turns"] == 1


@pytest.mark.parametrize(
    "task",
    [
        {"actual_action": "answer", "correct": True, "judge_status": "failed"},
        {"actual_action": "unexpected", "correct": True},
        {"actual_action": "answer"},
    ],
)
def test_failed_or_missing_behavior_judge_does_not_become_valid_from_residual_fields(task):
    c = case_from_item(item(task_judge=task), 0)
    r = report([c])
    assert r["denominators"]["behavior_valid"] == 0
    assert r["denominators"]["behavior_missing"] == 1
    assert r["behavior_groups"][0]["missing"] == 1
    assert matches(c, {"behavior_result": "missing"})
