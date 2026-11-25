"""Single-question scope, model binding, conversation continuity and immutable provenance."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import pytest
from test_suites import FakeExecution, FakeRunStore, _suites, _wait_until

from photobench.application.interactive.chat import ChatService
from photobench.domain.datasets.qa_taxonomy import qa_fingerprint
from photobench.domain.errors import ChatBusyError, InvalidRequestError
from photobench.domain.runs.analysis import run_list_entry
from photobench.domain.runs.task_history import task_families
from photobench.infrastructure.chat.store import FileChatStore


def saved_run() -> dict[str, Any]:
    rows = [
        {"qa_id": "a", "question": "one", "answer": "secret-one"},
        {
            "qa_id": "b",
            "question": "two",
            "answer": "secret-two",
            "conversation": [
                {"message": "first", "reference_answer": "hidden1"},
                {"message": "second", "reference_answer": "hidden2"},
            ],
        },
    ]
    return {
        "run_id": "original",
        "status": "completed",
        "album_id": "album-x",
        "qa_set": "qa-1",
        "scope_id": "space",
        "sentrix_url": "http://sentrix:8191",
        "created_at": "2026-11-01",
        "qa_snapshot": {
            "album_id": "album-x",
            "qa_set": "qa-1",
            "rows": rows,
            "definition_sha256": qa_fingerprint(rows),
        },
        "items": [{"qa_id": "a", "answer": "old"}],
        "summary": {"total": 2},
        "judge_provider_id": "cloud",
        "judge_url": "http://judge:1/v1",
        "judge_model": "judge-model",
    }


@pytest.mark.parametrize("options,enabled", [({}, True), ({"reasoning": False}, False)])
def test_retest_is_one_frozen_question_with_no_inherited_answers(tmp_path: Path, options, enabled) -> None:
    saved = saved_run()
    before = copy.deepcopy(saved)
    requests = []
    suites = _suites(
        tmp_path,
        store=FakeRunStore({"original": saved}),
        sentrix=ChatClient(),
        new_execution=lambda r: requests.append(r) or FakeExecution(r),
    )
    result = suites.start_retest(
        "original",
        {
            "qa_id": "b",
            **options,
            "model": {
                "profile": "other",
                "base_url": "http://models:1/v1",
                "manager_url": "http://manager:2",
            },
        },
    )
    request = requests[0]
    assert request.scope_id == "space" and request.mode == "reuse"
    assert request.model_profile == "other" and request.switch_model
    assert request.reasoning is enabled
    assert request.turn_timeout == (3660 if enabled else 900)
    assert request.prior_items == () and request.resume_context is None
    assert request.frozen_qa_snapshot["rows"] == [saved["qa_snapshot"]["rows"][1]]
    assert request.frozen_qa_snapshot["definition_sha256"] == qa_fingerprint(
        request.frozen_qa_snapshot["rows"]
    )
    assert result["retest_context"]["source_item_index"] == 1
    assert saved == before
    assert _wait_until(lambda: not suites.work_status()["work"])


def test_retest_rejects_missing_question_active_work_and_changed_embedding(tmp_path: Path) -> None:
    saved = saved_run()
    suites = _suites(tmp_path, store=FakeRunStore({"original": saved}))
    with pytest.raises(InvalidRequestError, match="一道题"):
        suites.start_retest("original", {"qa_id": "absent"})
    suites.reserve_interactive("chat-active", threading.Event())
    with pytest.raises(InvalidRequestError, match="still active"):
        suites.start_retest("original", {"qa_id": "a"})
    suites.release_interactive("chat-active")
    saved["scope_build"] = {"status": "ready", "embedding": {"mode": "different"}}
    with pytest.raises(InvalidRequestError, match="嵌入配置"):
        suites.start_retest("original", {"qa_id": "a"})


def test_retest_provenance_does_not_replace_original_task() -> None:
    saved = saved_run()
    retest = {
        **saved,
        "run_id": "new",
        "qa_count": 1,
        "retest_context": {"source_run_id": "original", "qa_id": "b"},
    }
    entries = [run_list_entry(retest), run_list_entry(saved)]
    assert entries[0]["retest_context"]["qa_id"] == "b"
    assert len(task_families(entries)) == 2


class Spaces:
    def list_spaces(self) -> dict[str, Any]:
        return {
            "spaces": [{"id": "space", "name": "album"}],
            "reuse_bases": [{"scope_id": "space", "album_id": "album-x", "ready": True}],
        }


class ChatClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.conversations: list[str] = []
        self.deleted_conversations: list[str] = []
        self.cancelled: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()
        self.transient_get_error = False

    def diagnostics(self) -> dict[str, Any]:
        return {"runtime_configuration": {"vlm_base_url": "http://127.0.0.1:8100/v1"}}

    def start_conversation(self, *, scope_id: str, title: str) -> dict[str, str]:
        self.conversations.append(scope_id)
        return {"id": "conversation-" + str(len(self.conversations))}

    def start_turn(self, **kwargs: Any) -> dict[str, Any]:
        self.messages.append(kwargs)
        return {"turn_id": "turn-" + str(len(self.messages)), "status": "pending"}

    def get_turn(self, *, turn_id: str) -> dict[str, Any]:
        if self.transient_get_error:
            self.transient_get_error = False
            raise TimeoutError("temporary network error")
        status = "cancelled" if turn_id in self.cancelled else "complete" if self.gate.is_set() else "running"
        return {"status": status, "result": {"answer": "answer", "trace": {}}, "public_progress": []}

    def cancel_turn(self, *, turn_id: str) -> dict[str, Any]:
        self.cancelled.add(turn_id)
        return {"status": "cancelled"}

    def delete_conversation(self, *, conversation_id: str, timeout: int = 30) -> None:
        self.deleted_conversations.append(conversation_id)

    def list_assets(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_asset(self, *, asset_id: str, timeout: int = 5) -> dict[str, Any] | None:
        return None


class ModelPreparation:
    def __init__(self, requests: list[Any]) -> None:
        self.requests = requests

    def prepare_interactive_model(self, request: Any, cancel: threading.Event) -> dict[str, Any]:
        self.requests.append(request)
        return {"model_name": request.model_profile, "model_identity": {"status": "verified"}}


def chat_service(tmp_path: Path) -> tuple[ChatService, Any, ChatClient, list[Any]]:
    client, requests = ChatClient(), []
    suites = _suites(tmp_path, sentrix=client)
    suites._albums.rows = saved_run()["qa_snapshot"]["rows"]
    service = ChatService(
        store=FileChatStore(tmp_path / "chat"),
        suites=suites,
        albums=suites._albums,
        spaces=Spaces(),
        client_for=lambda _: client,
        execution_for=lambda _: ModelPreparation(requests),
    )
    return service, suites, client, requests


def wait_chat(service: ChatService, key: str) -> dict[str, Any]:
    assert _wait_until(lambda: key not in service.workers)
    return service.get_chat(key)


def test_free_chat_continues_and_survives_new_reader(tmp_path: Path) -> None:
    service, suites, client, requests = chat_service(tmp_path)
    first = service.create_chat({"scope_id": "space", "message": "hello"})
    key = first["session_id"]
    assert wait_chat(service, key)["status"] == "ready"
    service.send_chat(key, {"message": "follow up"})
    result = wait_chat(service, key)
    assert len(result["items"]) == 2
    assert [m["conversation_id"] for m in client.messages] == ["conversation-1"] * 2
    assert all(m["include_debug"] for m in client.messages)
    assert all(m["reasoning"] is True for m in client.messages)
    assert all(r.reasoning is True and r.turn_timeout == 3660 for r in requests)
    assert result["source"] is None and len(requests) == 2
    assert FileChatStore(tmp_path / "chat").load_session(key)["turns"] == result["turns"]
    assert suites.work_status()["cleanup_status"] == "stopped"


def test_chat_message_reads_and_cached_trace_need_no_product_requests(tmp_path, monkeypatch):
    service, _, client, _ = chat_service(tmp_path)
    key = service.create_chat({"scope_id": "space", "message": "hello"})["session_id"]
    finished = wait_chat(service, key)

    def unexpected(**kwargs):
        pytest.fail("saved chat reads must not contact the product or scan the album")

    monkeypatch.setattr(client, "get_turn", unexpected)
    monkeypatch.setattr(client, "list_assets", unexpected)
    light = service.get_chat(key, include_items=False)
    assert "items" not in light
    assert light["turns"][0]["answer"] == "answer"
    assert light["turns"] == finished["turns"]
    core = service.get_chat_turn(key, 0)
    assert core["final_answer"] == "answer"
    assert core["trace_storage"]["base_url"] == f"/chat/sessions/{key}/turns/0"
    assert "trace_bundle" not in core["runtime_turns"][0]
    assert service.get_chat_trace(key, 0, revision=core["trace_storage"]["revision"])["total"] == 0
    with pytest.raises(KeyError):
        service.get_chat_turn(key, -1)
    service.delete_chat(key)
    assert not (tmp_path / "chat" / key).exists()


def test_legacy_chat_reads_only_one_turn_and_referenced_assets(tmp_path, monkeypatch):
    service, _, client, _ = chat_service(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    key = session["session_id"]
    session["turns"] = [
        {"index": i, "turn_id": f"old-{i}", "status": "complete", "message": str(i)} for i in range(3)
    ]
    service.store.save_session(session)
    calls, asset_calls = [], []
    assets = {
        "frame": {
            "id": "frame",
            "file_name": "frame.jpg",
            "media_type": "image",
            "derived_kind": "video_keyframe",
            "parent_asset_id": "video",
        },
        "video": {"id": "video", "file_name": "clip.mp4", "media_type": "video"},
        "mail": {
            "id": "mail",
            "file_name": "mail.eml",
            "media_type": "text",
            "external_source_id": "email1",
            "source_metadata": {"document_type": "email"},
        },
    }

    def get_turn(**kwargs):
        calls.append(kwargs["turn_id"])
        return {
            "status": "complete",
            "result": {
                "answer": "旧回答" * 3000,
                "attachments": [{"asset_id": "frame"}],
                "retrieved_candidates": ["frame", "mail"],
                "trace": {
                    "source_reads": [{"asset_id": "mail"}],
                    "debug_calls": [
                        {
                            "call_id": "c1",
                            "duration_ms": 1000,
                            "messages": [{"role": "user", "content": "原始输入"}],
                        }
                    ],
                },
            },
            "public_progress": [],
        }

    def get_asset(**kwargs):
        asset_calls.append(kwargs["asset_id"])
        return assets.get(kwargs["asset_id"])

    monkeypatch.setattr(client, "get_turn", get_turn)
    monkeypatch.setattr(client, "get_asset", get_asset)
    monkeypatch.setattr(client, "list_assets", lambda **_: pytest.fail("no album scan"))
    assert "items" not in service.get_chat(key, include_items=False)
    assert not calls and not asset_calls
    core = service.get_chat_turn(key, 1)
    assert core["final_answer"] == "旧回答" * 3000
    assert calls == ["old-1"] and sorted(asset_calls) == ["frame", "mail", "video"]
    assert core["predicted_media"][0]["asset_id"] == "video"
    assert core["read_source_refs"][0]["source_id"] == "email1"
    revision = core["trace_storage"]["revision"]
    page = service.get_chat_trace(key, 1, revision=revision)
    node = next(n for n in page["nodes"] if n["kind"] == "model")
    text = service.get_chat_trace(key, 1, revision=revision, node_id=node["node_id"], field="messages")
    assert "原始输入" in text["text"]
    assert (
        service.get_chat_trace(key, 1, revision=revision, node_id=node["node_id"], view="timing")["node"][
            "duration_ms"
        ]
        == 1000
    )
    assert service.get_chat_turn(key, 1)["index"] == 1
    assert calls == ["old-1"]


def test_light_chat_remains_readable_when_legacy_product_is_offline(tmp_path, monkeypatch):
    service, _, client, _ = chat_service(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    session["turns"] = [{"index": 0, "turn_id": "old", "status": "cancelled", "message": "saved question"}]
    service.store.save_session(session)
    monkeypatch.setattr(client, "get_turn", lambda **_: (_ for _ in ()).throw(TimeoutError("offline")))
    assert (
        service.get_chat(session["session_id"], include_items=False)["turns"][0]["message"]
        == "saved question"
    )
    with pytest.raises(TimeoutError):
        service.get_chat_turn(session["session_id"], 0)


def test_delete_chat_removes_product_conversation_and_only_its_session(tmp_path: Path) -> None:
    service, _, client, _ = chat_service(tmp_path)
    other = service.create_chat({"scope_id": "space"})
    first = service.create_chat({"scope_id": "space", "message": "hello"})
    finished = wait_chat(service, first["session_id"])
    service.delete_chat(first["session_id"])
    assert client.deleted_conversations == [finished["conversation_id"]]
    assert [s["session_id"] for s in service.list_chats()["items"]] == [other["session_id"]]
    with pytest.raises(KeyError):
        service.get_chat(first["session_id"])
    with pytest.raises(KeyError):
        service.delete_chat(first["session_id"])
    service.delete_chat(other["session_id"])
    assert not service.list_chats()["items"]


@pytest.mark.parametrize("status", ["preparing", "running", "cancelling", "outcome_unknown"])
def test_delete_chat_rejects_unfinished_work(tmp_path: Path, status: str) -> None:
    service, _, client, _ = chat_service(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    session["status"] = status
    service.store.save_session(session)
    with pytest.raises(ChatBusyError):
        service.delete_chat(session["session_id"])
    assert service.store.load_session(session["session_id"])["status"] == status
    assert not client.deleted_conversations


def test_failed_product_delete_keeps_chat_for_retry(tmp_path: Path, monkeypatch) -> None:
    service, _, client, _ = chat_service(tmp_path)
    session = service.create_chat({"scope_id": "space", "message": "hello"})
    finished = wait_chat(service, session["session_id"])

    def fail(**kwargs):
        raise TimeoutError("product unreachable")

    monkeypatch.setattr(client, "delete_conversation", fail)
    with pytest.raises(TimeoutError):
        service.delete_chat(session["session_id"])
    assert service.get_chat(session["session_id"])["turns"] == finished["turns"]


def test_chat_can_toggle_reasoning_per_turn_and_keeps_previous_modes(tmp_path: Path) -> None:
    service, _, client, requests = chat_service(tmp_path)
    first = service.create_chat({"scope_id": "space", "message": "one"})
    key = first["session_id"]
    assert wait_chat(service, key)["reasoning"] is True
    service.send_chat(key, {"message": "two", "reasoning": False})
    assert wait_chat(service, key)["reasoning"] is False
    service.send_chat(key, {"message": "three"})
    result = wait_chat(service, key)
    assert [m["reasoning"] for m in client.messages] == [True, False, False]
    assert [t["reasoning"] for t in result["turns"]] == [True, False, False]
    assert [r.turn_timeout for r in requests] == [3660, 900, 900]
    assert result["runtime_snapshot"]["execution_budgets"]["reasoning"] is False


def test_new_chat_can_disable_reasoning_and_legacy_chat_preserves_mode(tmp_path: Path) -> None:
    service, _, client, requests = chat_service(tmp_path)
    first = service.create_chat({"scope_id": "space", "message": "one", "reasoning": False})
    key = first["session_id"]
    result = wait_chat(service, key)
    assert result["reasoning"] is False and requests[0].turn_timeout == 900
    del result["reasoning"]
    service.store.save_session(result)
    service.send_chat(key, {"message": "old session"})
    wait_chat(service, key)
    assert [m["reasoning"] for m in client.messages] == [False, False]


def test_question_chat_freezes_gt_and_sends_only_user_messages(tmp_path: Path) -> None:
    service, _, client, _ = chat_service(tmp_path)
    result = service.create_chat({"scope_id": "space", "album_id": "album-x", "qa_set": "qa-1", "qa_id": "b"})
    final = wait_chat(service, result["session_id"])
    assert final["source"]["row"]["answer"] == "secret-two"
    assert [m["message"] for m in client.messages] == ["first", "second"]
    assert client.conversations == ["space"]
    assert all("secret" not in str(m) and "hidden" not in str(m) for m in client.messages)


def test_chat_cancellation_keeps_suite_busy_until_product_stops(tmp_path: Path) -> None:
    service, suites, client, _ = chat_service(tmp_path)
    client.gate.clear()
    result = service.create_chat({"scope_id": "space", "message": "hello"})
    key = result["session_id"]
    assert _wait_until(lambda: bool(client.messages))
    assert suites.work_status()["work"][0]["kind"] == "chat"
    with pytest.raises(InvalidRequestError):
        service.create_chat({"scope_id": "space", "message": "another"})
    service.cancel_chat(key)
    assert wait_chat(service, key)["status"] == "cancelled"
    assert client.cancelled == {"turn-1"}
    assert not suites.work_status()["work"]


def test_chat_storage_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        FileChatStore(tmp_path).load_session("../run")


def test_explicit_model_switch_is_not_skipped_for_reused_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_run_execution import _execution, _request

    from photobench.application.run_execution.state import RunState

    execution, client, _, lifecycle, _ = _execution()
    monkeypatch.setattr(execution, "_sync_sentrix_endpoint", lambda _: {"ok": True})
    monkeypatch.setattr(execution, "_wait_model_ready", lambda _: None)
    monkeypatch.setattr(execution, "_probe_model_endpoint", lambda *_: None)
    monkeypatch.setattr(execution, "_model_probe", lambda *_: {})
    monkeypatch.setattr(execution.inference, "list_models", lambda: {"models": ["qwen"]})
    request = _request(mode="reuse", scope_id="space", switch_model=True, manager_url="http://manager:2")
    execution._phase_model_deploy(RunState({"phases": {}}), request)
    assert "start" in lifecycle.call_names()
    assert next(args for name, args in lifecycle.calls if name == "start")["profile"] == "qwen"
    assert not set(client.call_names()) & {"import_album", "import_assets"}


def test_live_default_model_and_product_version_are_frozen(tmp_path: Path) -> None:
    service, _, _, _ = chat_service(tmp_path)
    service.suites.interactive_models.diagnostics = lambda: {
        "build_revision": "actual-product",
        "runtime_configuration": {"vlm_base_url": "http://cloud:6/v1"},
    }
    request = service.suites.interactive_models.resolve(
        {}, album_id="album-x", qa_set="", scope_id="space", mode="reuse"
    )
    assert request.model_base_url == "http://cloud:6/v1"
    assert service.suites.interactive_models.deployed_endpoint() == (
        request.model_base_url,
        request.endpoint_model,
    )
    assert request.runtime_snapshot.connection()["required_build_revision"] == "actual-product"


def test_transient_poll_failure_keeps_ownership_and_does_not_resend(tmp_path: Path) -> None:
    service, suites, client, _ = chat_service(tmp_path)
    client.transient_get_error = True
    result = service.create_chat({"scope_id": "space", "message": "only once"})
    assert _wait_until(lambda: bool(client.messages))
    assert suites.work_status()["work"]
    assert wait_chat(service, result["session_id"])["status"] == "ready"
    assert len(client.messages) == 1


def test_single_question_execution_uses_only_frozen_row_and_preserves_scope() -> None:
    from test_run_execution import _execution, _request, _rows

    row = _rows()[0]
    snapshot = {
        "album_id": "album-x",
        "qa_set": "qa-1",
        "rows": [row],
        "definition_sha256": qa_fingerprint([row]),
    }
    execution, client, _, _, _ = _execution()
    result = execution.execute(
        _request(
            mode="reuse",
            scope_id="existing",
            frozen_qa_snapshot=snapshot,
            retest_context={"source_run_id": "original", "qa_id": row["qa_id"]},
        )
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["qa_id"] == row["qa_id"]
    assert result["retest_context"]["source_run_id"] == "original"
    assert client.call_names().count("start_turn") == 1
    assert not set(client.call_names()) & {"import_album", "import_assets", "create_scope"}


def test_retest_copies_selected_scoring_config_and_gt_metadata(tmp_path: Path) -> None:
    saved = saved_run()
    batch = {
        "batch_id": "selected",
        "profile_id": "atm_qs_v1",
        "config": {"use_gt_images": True},
        "rule_version": "old-rule",
        "judge": {"provider_id": "cloud", "url": "http://judge:1/v1", "model": "judge-model"},
        "gt_metadata_snapshot": {"image/a": {"location": "frozen"}},
    }
    saved["scoring_batches"] = [batch]
    requests = []
    suites = _suites(
        tmp_path,
        store=FakeRunStore({"original": saved}),
        sentrix=ChatClient(),
        new_execution=lambda r: requests.append(r) or FakeExecution(r),
    )
    suites.start_retest("original", {"qa_id": "a", "scoring_batch": "selected"})
    request = requests[0]
    assert request.scoring_configs == {"atm_qs_v1": {"use_gt_images": True}}
    assert request.prior_scoring == {"gt_metadata_snapshot": batch["gt_metadata_snapshot"]}
    assert request.retest_context["source_rule_versions"] == ["old-rule"]
    assert _wait_until(lambda: not suites.work_status()["work"])


def test_restart_reconciles_dispatched_turn_without_sending_again(tmp_path: Path) -> None:
    service, suites, client, _ = chat_service(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    session.update(
        status="outcome_unknown",
        conversation_id="conversation-1",
        dispatching_message="once",
        pending_messages=["once"],
    )
    service.store.save_session(session)
    client.get_conversation = lambda **_: {"turns": [{"id": "already-accepted", "question": "once"}]}
    service.recover_chats()
    result = wait_chat(service, session["session_id"])
    assert result["status"] == "ready"
    assert result["turns"][0]["turn_id"] == "already-accepted"
    assert result["turns"][0]["reasoning"] is True
    assert not client.messages and not suites.work_status()["work"]


def test_benchmark_release_identity_is_separate_from_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from photobench.infrastructure.config.files import read_build_revision

    (tmp_path / "build-info.json").write_text('{"revision": "product"}')
    monkeypatch.setenv("PHOTOBENCH_BUILD_REVISION", "benchmark")
    assert read_build_revision(tmp_path) == "benchmark"
    assert (tmp_path / "build-info.json").read_text() == '{"revision": "product"}'


def test_retest_progress_reads_live_state_and_saved_cancelled_trace(tmp_path):
    from types import SimpleNamespace

    from photobench.application.run_execution.state import RunState

    original = saved_run()
    live = RunState(
        {
            "run_id": "single",
            "status": "running",
            "retest_context": {"source_run_id": "original"},
            "interactive_item": {"runtime_turns": [{"index": 0, "trace": [{"text": "search started"}]}]},
            "model_name": "actual-model",
            "phases": {"qa_eval": {"status": "running"}},
        }
    )
    store = FakeRunStore({"original": original})
    suites = _suites(tmp_path, store=store)
    suites._active["single"] = SimpleNamespace(state=live)
    progress = suites.get_retest_progress("single")
    assert progress["status"] == "running"
    assert progress["model"]["model_name"] == "actual-model"
    progress["item"]["runtime_turns"].clear()
    assert len(live.data["interactive_item"]["runtime_turns"]) == 1
    live.set("status", "cancelled")
    store.states["single"] = live.data
    del suites._active["single"]
    saved = suites.get_retest_progress("single")
    assert saved["status"] == "cancelled"
    assert saved["item"]["runtime_turns"][0]["trace"] == [{"text": "search started"}]
    assert original == saved_run()
    with pytest.raises(InvalidRequestError):
        suites.get_retest_progress("original")
    with pytest.raises(KeyError):
        suites.get_retest_progress("missing")
