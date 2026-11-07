"""AI comparison uses full saved facts and mock-only model IO."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from photobench.api.routes.error_analysis import AnalysisRequest
from photobench.application.error_analysis.service import ErrorAnalysis
from photobench.domain.error_analysis.ai_comparison import pair_indices, validate_comparison
from photobench.domain.error_analysis.exports import export_auxiliary
from photobench.infrastructure.error_analysis.backend import FileAnalysisBackend
from photobench.infrastructure.error_analysis.worker import atomic, run_worker


def finding(refs: list[str]) -> dict[str, Any]:
    return {
        "similarities": [],
        "divergences": [],
        "evaluation": [
            {
                "kind": "observation",
                "text": "两侧均有保存记录，仍需核对任务要求",
                "limitations": "不能推断因果",
                "evidence_refs": refs,
            }
        ],
        "missing_evidence": [],
    }


def prepared(
    tmp_path: Path,
    *,
    image: bool = True,
    scores: tuple[Any, Any] = (0, 2),
    gt_refs: list[dict[str, str]] | None = None,
    expected_action: str = "answer",
) -> tuple[FileAnalysisBackend, dict[str, Any]]:
    from photobench.infrastructure.datasets.albums import JsonAlbumSource

    album_dir = tmp_path / "datasets" / "album"
    (album_dir / "photos").mkdir(parents=True)
    (album_dir / "manifest.json").write_text(json.dumps({"photos": ["photos/gt.png"]}))
    if image:
        (album_dir / "photos" / "gt.png").write_bytes(b"mock-png-exact-original")
    albums = JsonAlbumSource(tmp_path / "datasets", tmp_path / "policy.json")
    backend = FileAnalysisBackend(tmp_path, albums=albums)
    backend._thread = threading.Thread()  # Manual deterministic dispatch, no supervisor/network.
    backend.artifacts.mkdir()
    ids = []
    for side, score in zip(("left", "right"), scores, strict=True):
        run = tmp_path / side
        run.mkdir()
        row = {
            "qa_id": "q",
            "question": "question",
            "answer": "textGT",
            "answer_evidence_media_refs": (
                [{"media_type": "image", "media_id": "nested/gt.png"}] if gt_refs is None else gt_refs
            ),
            "expected_action": expected_action,
        }
        item = {
            "qa_id": "q",
            "question": "question",
            "reference_answer": "textGT",
            "expected_action": expected_action,
            "answer": side,
            "judge": {"score": score},
            "execution_status": "completed",
            "model_call_metrics": [
                {"input": {"messages": [{"content": "x" * 90000}]}, "output": {"content": "TAIL_SENTINEL"}}
            ],
            "runtime_turns": [
                {
                    "question": "followup",
                    "expected_action": expected_action,
                    "answer": "complete",
                    "tool_trace": [{"tool": "inspect", "result": {"tail": "FULL_TOOL_TAIL"}}],
                }
            ],
        }
        atomic(
            run / "run.json",
            {"status": "completed", "qa_snapshot": {"album_id": "album", "rows": [row]}, "items": [item]},
        )
        key = ("a" if side == "left" else "b") * 32
        target = backend.artifacts / key
        target.mkdir()
        job = {
            "job_id": key,
            "run_id": side,
            "kind": "rules",
            "status": "queued",
            "options": {},
            "created_at": 1,
        }
        run_worker(str(tmp_path), str(target), job, threading.Event(), threading.Event(), {})
        assert backend.get(key)["status"] == "completed"
        ids.append(key)
    options = {
        "left_job": ids[0],
        "right_job": ids[1],
        "scope": "filter",
        "filters": {},
        "model_role": "judge",
        "secret": {
            "model": "J",
            "provider_id": "judge",
            "url": "https://mock.invalid/v1",
            "key": "PRIVATE_TEST_KEY",
        },
    }
    return backend, options


def execute(backend: FileAnalysisBackend, job: dict[str, Any]) -> dict[str, Any]:
    job.pop("owner_pid", None)
    run_worker(
        str(backend.root),
        str(backend._directory(job["job_id"])),
        job,
        threading.Event(),
        threading.Event(),
        backend._secret.pop(job["job_id"]),
    )
    return backend.result(job["job_id"])


def mock_model(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_pairing_keeps_duplicates_unmatched_and_selected_scope():
    left = [{"index": i, "qa_id": qa} for i, qa in enumerate(["a", "a", "b", "c", ""])]
    right = [{"index": i, "qa_id": qa} for i, qa in enumerate(["b", "d"])]
    pairs = pair_indices(left, right, {0, 2, 3, 4})
    assert [p["pairing"] for p in pairs] == ["ambiguous", "paired", "unmatched", "ambiguous"]
    assert pairs[1]["right_index"] == 0


def test_citations_require_both_sides_and_known_refs():
    raw = json.dumps(finding(["left:i0:item"]))
    assert validate_comparison(raw, {"left:i0:item"}, paired=True)[1] == "comparison_requires_both_sides"
    assert validate_comparison(raw, set())[1] == "invalid_references"
    assert (
        validate_comparison(
            json.dumps(finding(["left:i0:item", "right:i0:item"])),
            {"left:i0:item", "right:i0:item"},
            paired=True,
        )[1]
        is None
    )


def test_full_inputs_images_summary_and_export_are_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend, options = prepared(tmp_path)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        content = body["messages"][1]["content"]
        payload = json.loads(content[0]["text"])
        if "left" in payload:
            assert (
                len(payload["left"]["item"]["model_call_metrics"][0]["input"]["messages"][0]["content"])
                == 90000
            )
            assert payload["right"]["item"]["model_call_metrics"][0]["output"]["content"] == "TAIL_SENTINEL"
            assert (
                payload["left"]["item"]["runtime_turns"][0]["tool_trace"][0]["result"]["tail"]
                == "FULL_TOOL_TAIL"
            )
            assert payload["left"]["qa"]["answer"] == "textGT"
            assert len([p for p in content if p["type"] == "image_url"]) == 2
            refs = ["left:i0:item", "right:i0:item"]
        else:
            refs = payload["allowed_evidence_refs"]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(finding(refs))}}],
                "usage": {"total_tokens": 3},
            },
        )

    mock_model(monkeypatch, handler)
    job = backend.submit("left", "ai_compare", options)
    assert "PRIVATE_TEST_KEY" not in (backend._directory(job["job_id"]) / "job.json").read_text()
    result = execute(backend, job)
    assert result["status"] == "completed"
    report = result["report"]
    assert report["coverage"] == {"valid": 1, "failed": 0, "unmatched": 0, "unprocessed": 0}
    assert report["overview"] and len(requests) == 2
    exported = json.loads(backend.export(job["job_id"], "json", {}))
    assert exported["overview"] == report["overview"]
    assert exported["cases"] == report["cases"]
    assert not result["stale"]
    for path in backend._directory(job["job_id"]).rglob("*.json"):
        assert "PRIVATE_TEST_KEY" not in path.read_text()


@pytest.mark.parametrize("status", [400, 429, 500])
def test_http_failure_does_not_retry_or_lose_original_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
):
    backend, options = prepared(tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text="original-error-body")

    mock_model(monkeypatch, handler)
    job = backend.submit("left", "ai_compare", options)
    result = execute(backend, job)
    assert result["status"] == "failed" and len(calls) == 1
    assert result["report"]["coverage"]["failed"] == 1
    assert "original-error-body" in (backend._directory(job["job_id"]) / "pair-0-response.json").read_text()
    assert json.loads(backend.export(job["job_id"], "json", {}))["coverage"]["failed"] == 1


def test_missing_required_gt_image_never_silently_runs_text_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend, options = prepared(tmp_path, image=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("missing GT must not call a model")

    mock_model(monkeypatch, handler)
    result = execute(backend, backend.submit("left", "ai_compare", options))
    assert result["status"] == "failed"
    assert result["report"]["cases"][0]["missing_images"]


@pytest.mark.parametrize(
    "scores,accepted", [((0, 2), True), ((0, 0), False), ((None, 1), False), ((2, None), False)]
)
def test_score_changed_filter_excludes_equal_and_missing(
    tmp_path: Path, scores: tuple[Any, Any], accepted: bool
):
    backend, options = prepared(tmp_path, scores=scores)
    options["comparison_filter"] = "score_changed"
    if accepted:
        job = backend.submit("left", "ai_compare", options)
        assert job["options"]["pairs"][0]["index"] == 0
        assert job["options"]["scope_count"] == 1
    else:
        with pytest.raises(ValueError, match="没有符合"):
            backend.submit("left", "ai_compare", options)


def test_request_only_accepts_known_role_filter_and_scope():
    payload = {
        "kind": "ai_compare",
        "left_job": "a",
        "right_job": "b",
        "model_role": "judge",
        "scope": "filter",
    }
    assert AnalysisRequest(**payload).comparison_filter == "all"
    for changes in ({"model_role": "other"}, {"scope": "selection"}, {"comparison_filter": "anything"}):
        with pytest.raises(ValidationError):
            AnalysisRequest(**(payload | changes))


def test_model_roles_resolve_active_judge_and_main_without_persisting_secret():
    captured = []
    config = SimpleNamespace(
        judge_url="https://judge.invalid",
        judge_model="J",
        judge_api_key="private",
        model_base_url="http://main.invalid",
        public_runtime_connection_config=lambda: {"endpoint_model": "M"},
    )
    service = ErrorAnalysis(
        SimpleNamespace(submit=lambda run, kind, options: captured.append(options) or options), config
    )
    for role in ("judge", "main"):
        service.create("r", {"kind": "ai_compare", "model_role": role})
    assert captured[0]["secret"]["model"] == "J" and captured[0]["secret"]["key"] == "private"
    assert captured[1]["secret"]["model"] == "M" and captured[1]["secret"]["key"] == ""


def test_cancel_before_io_keeps_partial_artifact(tmp_path: Path):
    backend, options = prepared(tmp_path)
    job = backend.submit("left", "ai_compare", options)
    cancel = threading.Event()
    cancel.set()
    run_worker(
        str(tmp_path),
        str(backend._directory(job["job_id"])),
        job,
        cancel,
        threading.Event(),
        backend._secret.pop(job["job_id"]),
    )
    result = backend.result(job["job_id"])
    assert result["status"] == "cancelled"
    assert result["report"]["coverage"]["unprocessed"] == 1


def test_comparison_html_export_removed():
    report = {"note": "safe", "coverage": {}, "cases": [], "overview": finding(["evidence"])}
    with pytest.raises(ValueError, match="不提供HTML导出"):
        export_auxiliary(report, "ai_compare", "html")
    assert json.loads(export_auxiliary(report, "ai_compare", "json")) == report


def test_summary_failure_retains_successful_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend, options = prepared(tmp_path)
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(finding(["left:i0:item", "right:i0:item"]))}}
                    ]
                },
            )
        return httpx.Response(429, text="quota")

    mock_model(monkeypatch, handler)
    result = execute(backend, backend.submit("left", "ai_compare", options))
    assert result["status"] == "failed" and count == 2
    assert result["report"]["cases"][0]["valid"] is True
    assert result["report"]["coverage"]["valid"] == 1


def test_main_model_name_discovery_is_only_on_explicit_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend, options = prepared(tmp_path)
    options["secret"]["model"] = ""
    options["model_role"] = "main"
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "served-main"}]})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(finding(["left:i0:item", "right:i0:item"]))}}]
            },
        )

    mock_model(monkeypatch, handler)
    job = backend.submit("left", "ai_compare", options)
    assert methods == []
    result = execute(backend, job)
    assert result["status"] == "completed"
    assert result["report"]["model"] == "served-main"
    assert methods == ["GET", "POST", "POST"]


def test_api_response_retains_typed_comparison_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from photobench.api.routes.error_analysis import AIComparisonReport, AnalysisJob

    backend, options = prepared(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(finding(["left:i0:item", "right:i0:item"]))}}]
            },
        )

    mock_model(monkeypatch, handler)
    result = execute(backend, backend.submit("left", "ai_compare", options))
    public = AnalysisJob.model_validate(result)
    assert isinstance(public.report, AIComparisonReport)
    assert public.model_dump()["report"]["overview"]["evaluation"]


# Single-run diagnosis shares exact GT capture with comparison; all HTTP is mocked.
def single_options(options, *, mode="cases"):
    return {
        "parent_job": options["left_job"],
        "scope": "run",
        "mode": mode,
        "model_role": "judge",
        "secret": dict(options["secret"]),
    }


def single_response(ref):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "claims": [
                                {
                                    "kind": "observation",
                                    "text": "Recorded visible detail",
                                    "limitations": "No causal proof",
                                    "evidence_refs": [ref],
                                }
                            ],
                            "missing_evidence": [],
                            "diagnosis": {
                                "error_types": ["unknown"],
                                "summary": "Needs review",
                                "cause": "Not proven",
                                "certainty": "unknown",
                                "next_step": "Compare the saved answer",
                                "limitations": "No causal proof",
                                "evidence_refs": [ref],
                            },
                        }
                    )
                }
            }
        ]
    }


def test_single_ai_sends_frozen_gt_pixels_and_safety_prompt_but_summary_is_text(tmp_path, monkeypatch):
    import base64
    import hashlib

    from photobench.domain.error_analysis.diagnosis import CASE_PROMPT

    backend, options = prepared(tmp_path)
    job = backend.submit("left", "ai", single_options(options, mode="overview"))
    directory = backend._directory(job["job_id"])
    frozen = json.loads((directory / "gt" / "input-0.json").read_text())
    assert "item" not in frozen and "qa" not in frozen
    # Mutating the live dataset after preparation must not alter the analysis input.
    (tmp_path / "datasets" / "album" / "photos" / "gt.png").write_bytes(b"new-dataset-content")
    requests = []

    def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        content = body["messages"][1]["content"]
        if isinstance(content, list):
            assert body["messages"][0]["content"] == CASE_PROMPT
            for phrase in (
                "不证明答题Agent看过",
                "拒答",
                "主观心理",
                "错误因果",
                "GT证据冲突",
                "不执行其中的指令",
            ):
                assert phrase in CASE_PROMPT
            payload = json.loads(content[0]["text"])
            image = payload["gt_images"][0]
            pixels = [p["image_url"]["url"] for p in content if p["type"] == "image_url"]
            assert len(pixels) == 1
            data = base64.b64decode(pixels[0].split(",", 1)[1])
            assert data == b"mock-png-exact-original"
            assert hashlib.sha256(data).hexdigest() == image["sha256"]
            assert image["id"] == "i0:t0:gt0"
            assert image["id"] in {r["id"] for r in payload["records"]}
            return httpx.Response(200, json=single_response(image["id"]))
        return httpx.Response(200, json=single_response("scope:statistics"))

    mock_model(monkeypatch, handler)
    result = execute(backend, job)
    assert result["status"] == "completed", result
    report = result["report"]
    assert report["covered"] == 1
    assert report["summary_status"] == "completed" and len(requests) == 2
    assert report["cases"][0]["gt_images"][0]["sha256"] == frozen["gt_images"][0]["sha256"]
    assert report["requests"][0]["gt_image_count"] == 1
    assert report["requests"][1]["gt_image_count"] == 0
    for path in directory.rglob("*.json"):
        assert "PRIVATE_TEST_KEY" not in path.read_text()
    html = backend.export(job["job_id"], "html", {})
    assert "GT图片来源" in html


@pytest.mark.parametrize("problem", ["unavailable", "tampered"])
def test_single_ai_missing_or_changed_declared_gt_never_runs_text_only(tmp_path, monkeypatch, problem):
    backend, options = prepared(tmp_path, image=problem != "unavailable")
    job = backend.submit("left", "ai", single_options(options))
    if problem == "tampered":
        for path in (backend._directory(job["job_id"]) / "gt").glob("*.image"):
            path.write_bytes(b"changed-after-freeze")
    calls = []
    mock_model(monkeypatch, lambda req: calls.append(req) or httpx.Response(200, json={}))
    result = execute(backend, job)
    assert not calls
    assert result["report"]["coverage"]["failed"] == 1
    assert result["report"]["cases"][0]["error"] == "gt_images_unavailable_or_changed"


def test_single_ai_resume_keeps_frozen_images_and_does_not_repeat_valid_case(tmp_path, monkeypatch):
    backend, options = prepared(tmp_path)
    calls = []

    def handler(req):
        content = json.loads(req.content)["messages"][1]["content"]
        calls.append(content)
        return httpx.Response(
            200, json=single_response("i0:t0:gt0" if isinstance(content, list) else "scope:statistics")
        )

    mock_model(monkeypatch, handler)
    original = backend.submit("left", "ai", single_options(options))
    report = execute(backend, original)["report"]
    (tmp_path / "datasets" / "album" / "photos" / "gt.png").write_bytes(b"new-dataset")
    continued = backend.submit(
        "left", "ai", {**single_options(options, mode="overview"), "resume_job": original["job_id"]}
    )
    resumed = execute(backend, continued)["report"]
    assert resumed["coverage"]["reused"] == 1 and len(calls) == 2
    assert resumed["cases"][0]["gt_images"] == report["cases"][0]["gt_images"]
    assert isinstance(calls[1], str)


def test_single_ai_unsupported_vision_fails_without_text_retry(tmp_path, monkeypatch):
    backend, options = prepared(tmp_path)
    calls = []
    mock_model(
        monkeypatch,
        lambda req: calls.append(req) or httpx.Response(400, json={"error": "images unsupported"}),
    )
    job = backend.submit("left", "ai", single_options(options))
    result = execute(backend, job)
    assert result["status"] == "failed" and len(calls) == 1
    assert result["report"]["covered"] == 0
    assert result["report"]["requests"][0]["gt_image_count"] == 1


@pytest.mark.parametrize(
    "refs,expected_images,missing",
    [
        ([], 0, False),
        ([{"media_type": "video", "media_id": "same.png"}], 0, True),
        (
            [
                {"media_type": "image", "media_id": "same.png"},
                {"media_type": "video", "media_id": "same.png"},
            ],
            1,
            True,
        ),
    ],
)
def test_single_gt_typed_refs_and_explicit_empty_do_not_expand(tmp_path, refs, expected_images, missing):
    from photobench.infrastructure.error_analysis.comparison_inputs import freeze_side

    parent = tmp_path / "parent"
    parent.mkdir()
    raw = {
        "qa_id": "q",
        "answer_evidence_media_refs": refs,
        "retrieval_media_refs": [{"media_type": "image", "media_id": "unrelated.png"}],
    }
    (parent / "input.jsonl").write_text(json.dumps(raw) + "\n")
    image = tmp_path / "same.png"
    image.write_bytes(b"pixels")
    freeze_side(
        tmp_path,
        "gt",
        parent,
        {"qa_snapshot": {"album_id": "a"}, "_qa_rows": []},
        SimpleNamespace(
            manifest=lambda _album: {"photos": ["photos/same.png"]},
            media_file=lambda _album, _kind, mid: str(image) if mid == "same.png" else None,
        ),
        {0},
        include_raw=False,
    )
    package = json.loads((tmp_path / "gt" / "input-0.json").read_text())
    assert len(package["gt_images"]) == expected_images
    assert any("video_gt_not_attached" in value for value in package["missing_evidence"]) is missing
    assert all(image["media_id"] != "unrelated.png" for image in package["gt_images"])


def test_single_refusal_without_gt_image_is_normally_analyzed_as_text(tmp_path, monkeypatch):
    backend, options = prepared(tmp_path, gt_refs=[], expected_action="refuse")
    calls = []

    def handler(req):
        body = json.loads(req.content)
        content = body["messages"][1]["content"]
        assert isinstance(content, str)
        payload = json.loads(content)
        assert payload["gt_images"] == []
        contract = next(r for r in payload["records"] if r["id"] == "i0:task_contract")
        assert json.loads(contract["excerpt"])["turns"][0]["expected_action"] == "refuse"
        calls.append(body)
        return httpx.Response(200, json=single_response("i0:task_contract"))

    mock_model(monkeypatch, handler)
    job = backend.submit("left", "ai", single_options(options))
    report = execute(backend, job)["report"]
    assert report["covered"] == 1 and len(calls) == 1
    assert report["cases"][0]["valid"] and report["cases"][0]["gt_images"] == []


@pytest.mark.parametrize(
    "media_id,photos,attached",
    [
        ("album3/gt.png", ["photos/gt.png"], True),
        ("album3/gt.png", ["photos/one/gt.png", "photos/two/gt.png"], False),
        ("album3/gt.png", ["photos/gt.png", "photos/GT.png"], False),
        ("../../gt.png", ["photos/gt.png"], False),
        ("/gt.png", ["photos/gt.png"], False),
        ("album3/gt.png", ["photos/../gt.png"], False),
        ("album3/absent.png", ["photos/gt.png"], False),
    ],
)
def test_logical_gt_identity_resolves_only_unique_manifest_image(tmp_path, media_id, photos, attached):
    backend, options = prepared(tmp_path, gt_refs=[{"media_type": "image", "media_id": media_id}])
    album = tmp_path / "datasets" / "album"
    (album / "manifest.json").write_text(json.dumps({"photos": photos}))
    for relative in photos:
        if ".." not in Path(relative).parts:
            path = album / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"exact-manifest-image")
    job = backend.submit("left", "ai", single_options(options))
    frozen = json.loads((backend._directory(job["job_id"]) / "gt" / "input-0.json").read_text())
    image = frozen["gt_images"][0]
    assert (image["status"] == "attached") is attached
    assert image["media_id"] == media_id
    if attached:
        assert image["dataset_path"] == "photos/gt.png"
        assert (
            backend._directory(job["job_id"]) / "gt" / image["file"]
        ).read_bytes() == b"exact-manifest-image"


@pytest.mark.parametrize("mode", ["cases", "overview"])
def test_all_gt_failures_skip_overview_request_and_fail_job(tmp_path, monkeypatch, mode):
    backend, options = prepared(tmp_path, image=False)
    calls = []
    mock_model(monkeypatch, lambda req: calls.append(req) or httpx.Response(200, json={}))
    result = execute(backend, backend.submit("left", "ai", single_options(options, mode=mode)))
    assert not calls
    assert result["status"] == "failed"
    assert result["report"]["summary_status"] == ("failed" if mode == "overview" else "not_requested")
    assert result["report"]["covered"] == 0
    if mode == "overview":
        assert "没有有效逐题诊断" in result["report"]["summary_error"]
    assert not result["report"].get("overview")


def test_comparison_evidence_returns_both_frozen_sides_and_original_reference_paths(tmp_path, monkeypatch):
    from photobench.api.routes.error_analysis import ComparisonEvidenceSnapshot

    backend, options = prepared(tmp_path)
    mock_model(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(finding(["left:i0:item", "right:i0:item"]))}}]
            },
        ),
    )
    job = backend.submit("left", "ai_compare", options)
    execute(backend, job)
    # The endpoint reads comparison-time evidence, even when a live run changes.
    (tmp_path / "left" / "run.json").write_text(json.dumps({"status": "failed", "items": []}))
    evidence = backend.evidence(job["job_id"], 0)
    public = ComparisonEvidenceSnapshot.model_validate(evidence).model_dump()
    assert public["sides"]["left"]["item"]["answer"] == "left"
    assert public["sides"]["right"]["item"]["answer"] == "right"
    assert public["sides"]["left"]["source"]["run_id"] == "left"
    assert public["sides"]["right"]["source"]["run_id"] == "right"
    assert (
        public["sides"]["right"]["evidence_locations"]["right:i0:item.runtime_turns[0].tool_trace[0]"]
        == "item.runtime_turns[0].tool_trace[0]"
    )
    assert public["sides"]["left"]["gt_images"][0]["status"] == "attached"
    with pytest.raises(KeyError):
        backend.evidence(job["job_id"], -1)
    with pytest.raises(KeyError):
        backend.evidence(job["job_id"], 20)
    (backend._directory(job["job_id"]) / "pair-0-evidence.json").unlink()
    with pytest.raises(ValueError, match="尚未就绪"):
        backend.evidence(job["job_id"], 0)


def test_nested_model_and_tool_calls_have_stable_comparison_citations():
    from photobench.domain.error_analysis.ai_comparison import PAIR_PROMPT, comparison_evidence_ids

    record = {
        "index": 7,
        "item": {
            "runtime_turns": [
                {
                    "trace_bundle": {
                        "debug_calls": [
                            {"call_type": "planner", "step_id": "s"},
                            {"call_type": "agent", "step_id": "s", "attempt": 2},
                        ]
                    },
                    "tool_trace": [{"tool": "inspect"}],
                }
            ]
        },
    }
    refs = comparison_evidence_ids(record, "right")
    path = "item.runtime_turns[0].trace_bundle.debug_calls[1]"
    assert refs[path] == "right:i7:" + path
    assert refs["item.runtime_turns[0].tool_trace[0]"] == "right:i7:item.runtime_turns[0].tool_trace[0]"
    assert len(refs.values()) == len(set(refs.values()))
    for phrase in ("本次测评", "对照测评", "具体调用节点", "各栏最多3条", "同名工具不证明"):
        assert phrase in PAIR_PROMPT
