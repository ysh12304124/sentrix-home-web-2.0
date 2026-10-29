"""Scope/evidence and bounded AI contracts for error analysis v9."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from photobench.config import Settings
from photobench.domain.error_analysis.diagnosis import (
    evidence_package,
    representative,
    scope_facts,
    validate_claims,
)
from photobench.domain.error_analysis.query import filtered_view, select_case
from photobench.domain.error_analysis.rules import Aggregator, case_from_item
from photobench.infrastructure.error_analysis.worker import run_worker
from photobench.main import create_app


def case(index: int = 0, **changes: Any) -> dict[str, Any]:
    return case_from_item(
        {
            "qa_id": f"q{index}",
            "question": "find",
            "answer": "a",
            "reference_answer": "gt",
            "judge": {"score": 0},
            "termination_reason": "answered",
            **changes,
        },
        index,
    )


def test_same_event_and_turn_scope_and_tool_success():
    c = case(
        runtime_turns=[{"expected_action": "answer"}, {"expected_action": "refuse"}],
        tool_trace=[
            {
                "tool": "search_memory",
                "status": "error",
                "conversation_turn": 0,
                "result": {"failure_reason": "backend_down"},
            },
            {
                "tool": "read_photo_text",
                "status": "partial",
                "conversation_turn": 1,
                "result": {"failure_reason": "no_text_detected"},
            },
        ],
    )
    assert select_case(c, {"tool_name": "search_memory", "failure_reason": "no_text_detected"}) is None
    assert select_case(c, {"tool_name": "read_photo_text", "expected_action": "answer"}) is None
    selected = select_case(c, {"tool_name": "read_photo_text", "expected_action": "refuse"})
    assert selected and selected["matched_turns"] == [1]
    assert selected["events"][1]["code"] == "evidence_unavailable"
    success = case(tool_trace=[{"tool": "search_memory", "status": "completed"}])
    assert select_case(success, {"tool_name": "search_memory"})
    assert not select_case(success, {"tool_name": "search_memory", "anomaly": "yes"})


def test_full_filtered_aggregates_deduplicate_outer_internal_call():
    cases = [
        case(
            i,
            tool_trace=[
                {
                    "tool": "inspect_photo",
                    "status": "error",
                    "result": {
                        "failure_reason": "model_unavailable",
                        "model_call_observations": [
                            {"call_id": "inner", "status": "error", "error_type": "TimeoutError"}
                        ],
                    },
                }
            ],
        )
        for i in range(35)
    ]
    agg = Aggregator()
    for c in cases:
        agg.add(c)
    report = agg.report({})
    report.update(result_fingerprint="f", generated_at=1)
    view = filtered_view(cases, report, {"tool_name": "inspect_photo"}, 2, 20)
    assert len(view["items"]) == 15
    assert view["total"] == view["summary"]["item_count"] == 35
    assert view["tools"][0]["affected_calls"] == 35  # outer + internal => 35, not 70
    assert view["tools"][0]["recorded_calls"] == 35
    assert cases[0]["events"][1]["model_call_id"] == "inner"
    assert cases[0]["events"][0]["related_event_ids"] == ["i0:e1"]
    assert cases[0]["events"][0]["recovery"] == "unknown"


def test_missing_termination_does_not_infer_unrecovered():
    c = case(termination_reason=None, model_call_metrics=[{"status": "error", "error_type": "TimeoutError"}])
    assert c["events"][0]["recovery"] == "unknown"


def test_claim_validation_and_evidence_bounds():
    huge = {"tool_trace": [{"tool": "read_photo_text", "text": "x" * 100000}] * 40}
    package = evidence_package(case(), huge)
    assert package["omissions"]
    assert len(json.dumps(package)) < 60000
    raw = json.dumps(
        {
            "claims": [
                {
                    "kind": "hypothesis",
                    "text": "may be missing",
                    "limitations": "needs checking",
                    "evidence_refs": ["i0:case"],
                }
            ],
            "missing_evidence": [],
        }
    )
    assert validate_claims(raw, {"i0:case"})[1] is None
    assert validate_claims(raw, {"i1:case"})[1] == "invalid_references"
    assert validate_claims("bad", set())[1] == "invalid_json"
    assert validate_claims("{}", set())[1] == "invalid_schema"
    cases = [case(i) for i in range(10)] + [case(10, judge={"score": 2})]
    assert 10 in representative(cases, 2)


def setup_ai(tmp_path: Path, count: int = 4) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    parent = tmp_path / "parent"
    parent.mkdir()
    items = [
        {
            "qa_id": f"q{i}",
            "question": "question",
            "answer": "answer",
            "reference_answer": "gt",
            "judge": {"score": i % 3},
        }
        for i in range(count)
    ]
    (parent / "input.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items))
    (parent / "cases.jsonl").write_text(
        "".join(json.dumps(case_from_item(row, i)) + "\n" for i, row in enumerate(items))
    )
    (parent / "report.json").write_text(json.dumps({"result_fingerprint": "fixed", "rule_version": 9}))
    options = {
        "parent_path": str(parent),
        "parent_job": "parent",
        "indices": list(range(count)),
        "mode": "overview",
        "max_cases": count,
    }
    secret = {"provider_id": "test", "model": "m", "url": "http://test/v1", "key": "do-not-save"}
    return parent, options, secret


def run(
    tmp_path: Path, name: str, options: dict[str, Any], secret: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = tmp_path / name
    target.mkdir()
    run_worker(
        str(tmp_path),
        str(target),
        {"kind": "ai", "job_id": name, "options": options},
        threading.Event(),
        threading.Event(),
        secret,
    )
    return json.loads((target / "diagnosis.json").read_text()), json.loads((target / "job.json").read_text())


def install_mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    import photobench.infrastructure.error_analysis.ai_worker as worker

    original = httpx.Client
    monkeypatch.setattr(
        worker.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )


def answer(ref: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "claims": [
                                {
                                    "kind": "observation",
                                    "text": "recorded result",
                                    "limitations": "sample only",
                                    "evidence_refs": [ref],
                                }
                            ],
                            "missing_evidence": [],
                        }
                    )
                }
            }
        ],
        "usage": {"total_tokens": 30},
    }


def test_ai_partial_failure_then_summary_resume_without_case_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, options, secret = setup_ai(tmp_path)
    calls = []

    def respond(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        payload = json.loads(body["messages"][1]["content"])
        calls.append(payload)
        if "records" not in payload:
            return httpx.Response(200, json={"choices": [{"message": {"content": "invalid"}}]})
        i = payload["index"]
        if i == 1:
            raise httpx.ReadTimeout("timeout")
        if i == 2:
            return httpx.Response(200, json=answer("invented"))
        return httpx.Response(200, json=answer(f"i{i}:case"))

    install_mock(monkeypatch, respond)
    report, job = run(tmp_path, "first", options, secret)
    assert job["status"] == "completed"
    assert report["coverage"] == {
        "scope": 4,
        "attempted": 4,
        "reference_valid": 2,
        "invalid": 1,
        "failed": 1,
        "unprocessed": 0,
        "reused": 0,
    }
    assert report["summary_status"] == "failed"
    assert len(report["requests"]) == 6
    assert report["requests"][0]["usage"] == {"total_tokens": 30}
    assert all(r["duration_ms"] >= 0 for r in report["requests"])
    calls.clear()
    resumed, _ = run(
        tmp_path, "second", {**options, "resume_path": str(tmp_path / "first"), "summary_only": True}, secret
    )
    assert len(calls) == 1 and "groups" in calls[0]
    assert resumed["coverage"]["reused"] == 2
    assert "do-not-save" not in (tmp_path / "second" / "diagnosis.json").read_text()
    changed = tmp_path / "changed"
    changed.mkdir()
    run_worker(
        str(tmp_path),
        str(changed),
        {"kind": "ai", "options": {**options, "resume_path": str(tmp_path / "first")}},
        threading.Event(),
        threading.Event(),
        {**secret, "model": "new"},
    )
    assert json.loads((changed / "job.json").read_text())["status"] == "failed"


def test_auth_failure_stops_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, options, secret = setup_ai(tmp_path)
    calls = []

    def respond(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(401)

    install_mock(monkeypatch, respond)
    report, job = run(tmp_path, "auth", options, secret)
    assert job["status"] == "failed" and len(calls) == 1
    assert report["requests"][0]["http_status"] == 401


def test_public_query_rejects_unknown_keys_and_invalid_enum(tmp_path: Path):
    settings = Settings(
        config_root=tmp_path / "config", results_root=tmp_path / "results", data_root=tmp_path / "data"
    )
    settings.config_root.mkdir()
    (settings.config_root / "judge_providers.json").write_text(
        json.dumps(
            {
                "default_provider_id": "test",
                "providers": {"test": {"url": "http://127.0.0.1:1/v1", "model": "judge"}},
            }
        )
    )
    (settings.config_root / "vllm_targets.json").write_text(
        json.dumps({"default_target_id": "local", "targets": {"local": {}}})
    )
    with TestClient(create_app(settings=settings, scan_in_background=False)) as client:
        assert client.get("/api/v1/analyses/" + "a" * 32 + "/view?bogus=1").status_code == 422
        assert client.get("/api/v1/analyses/" + "a" * 32 + "/cases?score=3").status_code == 422
        assert (
            client.post(
                "/api/v1/runs/r/analysis",
                json={
                    "kind": "ai",
                    "parent_job": "p",
                    "provider_id": "p",
                    "indices": [0],
                    "filters": {"bogus": "1"},
                },
            ).status_code
            == 422
        )


def test_nested_trace_source_positions_and_multiturn_display():
    raw = {
        "question": "补充条件",
        "runtime_turns": [
            {"message": "最初的问题", "answer": "请澄清", "reference_answer": "需要澄清", "tool_trace": []},
            {
                "message": "补充条件",
                "answer": "答案",
                "reference_answer": "GT",
                "context_turn_count": 1,
                "trace_bundle": {"model_calls": [{"status": "error", "error_type": "TimeoutError"}]},
                "tool_trace": [
                    {
                        "tool": "read_photo_text",
                        "status": "error",
                        "result": {"failure_reason": "no_text_detected"},
                    }
                ],
            },
        ],
    }
    c = case_from_item(raw, 0)
    assert c["question"] == "最初的问题"
    assert c["turns"][1]["context_turn_count"] == 1
    assert c["turns"][0]["answer"] == "请澄清"
    assert c["events"][0]["evidence_refs"][0]["path"] == "runtime_turns[1].trace_bundle.model_calls[0]"
    assert c["events"][1]["evidence_refs"][0]["path"] == "runtime_turns[1].tool_trace[0]"
    package = evidence_package(c, raw)
    assert (
        next(r for r in package["records"] if r["id"] == "i0:tool0")["path"]
        == "runtime_turns[1].tool_trace[0]"
    )


def test_ai_cancellation_preserves_first_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, options, secret = setup_ai(tmp_path)
    cancel = threading.Event()
    calls = []

    def respond(req: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(req.content)["messages"][1]["content"])
        calls.append(payload)
        cancel.set()
        return httpx.Response(200, json=answer(f"i{payload['index']}:case"))

    install_mock(monkeypatch, respond)
    target = tmp_path / "cancel"
    target.mkdir()
    run_worker(
        str(tmp_path), str(target), {"kind": "ai", "options": options}, cancel, threading.Event(), secret
    )
    assert json.loads((target / "job.json").read_text())["status"] == "cancelled"
    saved = json.loads((target / "diagnosis.json").read_text())
    assert len(calls) == 1 and saved["covered"] == 1 and saved["coverage"]["unprocessed"] == 3


def test_ai_exports_keep_scope_and_escape_claim_text():
    from photobench.domain.error_analysis.exports import export_auxiliary

    report = {
        "schema_version": 2,
        "note": "引用待核",
        "coverage": {"reference_valid": 1},
        "scope": {"indices": [1]},
        "cases": [],
        "overview": {
            "claims": [
                {
                    "kind": "hypothesis",
                    "text": "<script>alert(1)</script>",
                    "limitations": "sample",
                    "evidence_refs": ["g0"],
                }
            ]
        },
    }
    html = export_auxiliary(report, "ai", "html")
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "可能原因（待验证）" in html and "g0" in html
    csv = export_auxiliary(report, "ai", "csv")
    assert "source_and_coverage" in csv and "reference_valid" in csv


def test_degraded_reason_is_preserved_without_extra_tool_failure():
    c = case(
        tool_trace=[
            {
                "tool": "search_memories",
                "status": "completed",
                "result": {"degraded": True, "degraded_reason": "embedder_unavailable"},
            }
        ]
    )
    assert len(c["events"]) == 1
    assert c["events"][0]["code"] == "retrieval_degraded"
    assert c["events"][0]["failure_reason"] == "embedder_unavailable"
    assert select_case(c, {"tool_name": "search_memories", "failure_reason": "embedder_unavailable"})


def test_evidence_includes_recorded_model_io_and_ledger_with_exact_paths():
    raw = {
        "runtime_turns": [
            {
                "message": "q",
                "trace_bundle": {
                    "debug_calls": [
                        {"messages": [{"role": "user", "content": "q"}], "response": {"content": "a"}}
                    ],
                    "evidence_ledger": [{"claim": "recorded evidence"}],
                },
            }
        ]
    }
    package = evidence_package(case_from_item(raw, 0), raw)
    records = {r["id"]: r for r in package["records"]}
    assert records["i0:turn0:debug0"]["path"] == "runtime_turns[0].trace_bundle.debug_calls[0]"
    assert '"messages"' in records["i0:turn0:debug0"]["excerpt"]
    assert records["i0:turn0:evidence_ledger"]["path"] == "runtime_turns[0].trace_bundle.evidence_ledger"
    assert package["availability"][0]["model_io_recorded"] is True
    facts = scope_facts([case(0), case(1, judge={"score": 2})])
    assert facts["item_count"] == 2 and facts["counts"]["score_0"] == facts["counts"]["score_2"] == 1


def labels_v2(primary=None, group="ordinary"):
    from photobench.domain.datasets.ability_taxonomy import V2_ABILITY_VERSION as ABILITY_VERSION

    primary = primary or []
    return dict(
        ability_taxonomy_version=ABILITY_VERSION,
        ability_primary_ids=primary,
        ability_supporting_ids=["P1"] if primary == ["P3"] else [],
        ability_family_ids=(
            ["H"]
            if group == "hallucination"
            else ["I"]
            if group == "injection"
            else sorted({p[0] for p in primary})
        ),
        benchmark_group=group,
    )


def aggregate(cases):
    agg = Aggregator()
    for row in cases:
        agg.add(row)
    return {**agg.report({}), "generated_at": 1, "result_fingerprint": "fixed"}


def test_partitions_detect_overlap_even_when_memberships_equal_labeled_population():
    cases = [case(0, **labels_v2(["P3", "R1"])), case(1), case(2, **labels_v2([], "hallucination"))]
    r = aggregate(cases)
    p = r["group_partitions"]["qa"]["ability_family"]
    assert p["total"] == 3 and p["labeled"] == 2
    assert p["multi_labeled"] == 1 and p["unknown"] == 1
    assert not p["exclusive"] and p["exhaustive"]
    # Counts of known memberships equal the entire population despite overlap + missing.
    assert sum(x["total"] for x in r["groups"]["ability_family"] if x["value"] != "unknown") == 3
    assert r["group_partitions"]["qa"]["benchmark_group"]["exclusive"]
    assert r["group_partitions"]["qa"]["ability_type"]["not_applicable"] == 1
    assert "P1" not in {x["value"] for x in r["groups"]["ability_type"]}  # supporting only
    assert not aggregate([])["group_partitions"]["qa"]


def test_tag_issue_matrix_deduplicates_events_and_respects_filtered_turns():
    c = case(
        **labels_v2(["P3", "R1"]),
        runtime_turns=[
            {"message": "look", "expected_action": "answer", "judge": {"score": 0}, **labels_v2(["P3"])},
            {"message": "count", "expected_action": "answer", "judge": {"score": 2}, **labels_v2(["R1"])},
        ],
        tool_trace=[
            {
                "tool": "read_photo_text",
                "status": "partial",
                "conversation_turn": 0,
                "result": {"failure_reason": "no_text_detected"},
            },
            {
                "tool": "read_photo_text",
                "status": "partial",
                "conversation_turn": 0,
                "result": {"failure_reason": "no_text_detected"},
            },
        ],
    )
    r = aggregate([c])
    key = {
        "unit": "turn",
        "dimension": "ability_type",
        "value": "P3",
        "code": "evidence_unavailable",
        "count": 1,
    }
    assert key in r["group_errors"]
    assert not any(x["value"] == "R1" and x["unit"] == "turn" for x in r["group_errors"])
    view = filtered_view([c], r, {"content_unit": "turn", "ability_type": "R1"}, 1, 1)
    assert view["summary"]["group_partitions"]["turn"]["ability_type"]["total"] == 1
    assert view["summary"]["group_errors"] == []
    row = view["summary"]["turn_groups"]["ability_type"][0]
    assert row["value"] == "R1" and row["score_2"] == 1 and row["affected"] == 0
    view = filtered_view(
        [c], r, {"content_unit": "turn", "ability_type": "P3", "code": "evidence_unavailable"}, 1, 1
    )
    assert view["summary"]["counts"]["events"] == 2
    assert view["summary"]["group_errors"][0]["count"] == 1


def test_snapshot_labels_use_frozen_source_and_keep_existing_versions():
    import copy

    from photobench.domain.error_analysis.rules import snapshot_annotations

    saved = {"qa_id": "q0", "question": "find", **labels_v2(["P3"])}
    raw = {"qa_id": "q0", "question": "find", "answer": "a", "judge": {"score": 2}}
    before = copy.deepcopy(raw)
    enriched = snapshot_annotations(raw, saved)
    assert raw == before
    assert enriched["ability_annotation_source"] == "run_qa_snapshot"
    assert case_from_item(enriched, 0)["dimension_labels"]["ability_type"] == ["P3"]
    assert snapshot_annotations(raw, {**saved, "question": "changed"}) == raw
    assert snapshot_annotations(raw, {**saved, "qa_id": "another"}) == raw
    existing = {**raw, "ability_taxonomy_version": "ability-2026.10.28-v1"}
    assert snapshot_annotations(existing, saved) == existing
    turns = [{"message": "first", **labels_v2(["P3"])}, {"message": "second", **labels_v2(["R1"])}]
    saved = {**saved, "conversation": turns}
    raw["runtime_turns"] = [{"message": "first"}, {"message": "second"}]
    enriched = snapshot_annotations(raw, saved)
    assert [t["ability_primary_ids"] for t in enriched["runtime_turns"]] == [["P3"], ["R1"]]
    assert (
        snapshot_annotations({**raw, "runtime_turns": [{"message": "first"}]}, saved).get(
            "ability_taxonomy_version"
        )
        is None
    )
    assert (
        snapshot_annotations(
            {**raw, "runtime_turns": [{"message": "changed"}, {"message": "second"}]}, saved
        ).get("ability_taxonomy_version")
        is None
    )


def test_version_filter_is_shared_by_api_statistics_and_export(tmp_path):
    from test_error_analysis import atomic, await_job, write_run

    from photobench.domain.datasets.ability_taxonomy import LEGACY_ABILITY_VERSION
    from photobench.domain.datasets.ability_taxonomy import V2_ABILITY_VERSION as ABILITY_VERSION

    root = tmp_path / "results"
    saved = {"qa_id": "q0", "question": "find", **labels_v2(["P3"])}
    raw = {"qa_id": "q0", "question": "find", "answer": "a", "judge": {"score": 2}}
    write_run(
        root,
        items=[
            raw,
            {**raw, "qa_id": "q1", **labels_v2(["P3"]), "ability_taxonomy_version": LEGACY_ABILITY_VERSION},
            {**raw, "qa_id": "unknown"},
        ],
    )
    path = root / "r" / "run.json"
    state = json.loads(path.read_text())
    state["qa_snapshot"] = {"rows": [saved]}
    atomic(path, state)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    atomic(
        cfg / "judge_providers.json",
        {
            "default_provider_id": "test",
            "providers": {"test": {"url": "http://127.0.0.1:1/v1", "model": "unused"}},
        },
    )
    atomic(cfg / "vllm_targets.json", {"default_target_id": "local", "targets": {"local": {}}})
    app = create_app(
        Settings(config_root=cfg, results_root=root, data_root=tmp_path / "empty"), scan_in_background=False
    )
    with TestClient(app) as client:
        key = client.post("/api/v1/runs/r/analysis", json={"kind": "rules"}).json()["job_id"]
        assert await_job(app.state.container.analysis_backend, key)["status"] == "completed"
        url = f"/api/v1/analyses/{key}"
        response = client.get(url + "/view?ability_taxonomy_version=" + ABILITY_VERSION)
        assert response.status_code == 200
        view = response.json()
        assert view["total"] == 1
        assert view["items"][0]["ability_annotation_source"] == "run_qa_snapshot"
        assert view["summary"]["ability_taxonomy_versions"] == [ABILITY_VERSION]
        assert view["summary"]["group_partitions"]["qa"]["ability_type"]["exclusive"]
        exported = client.get(url + "/export?format=json&ability_taxonomy_version=" + ABILITY_VERSION).json()
        assert [x["index"] for x in exported["cases"]] == [0]
        assert client.get(url + "/view?ability_taxonomy_version=unknown").json()["total"] == 1
    assert json.loads(path.read_text()) == state
