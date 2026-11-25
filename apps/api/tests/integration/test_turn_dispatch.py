"""普通模式并发提交与持久恢复必须使用同一份执行所有权。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from threading import enumerate as enumerate_threads

import pytest
from fastapi.testclient import TestClient

from sentrix.domain.assistant.models import TurnRecord, TurnStatus


@pytest.mark.parametrize("reasoning", [True, False])
def test_reasoning_survives_queued_dispatch_and_expired_lease(isolated_app, monkeypatch, reasoning):
    import threading

    original_start = threading.Thread.start

    def defer_dispatch(thread):
        if not thread.name.startswith("turn-turn_"):
            return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", defer_dispatch)
    client = TestClient(isolated_app)
    container = isolated_app.state.container
    delegate = container.chat_model
    selected_contexts = []

    class ContextModel:
        def __init__(self, context_tokens=16384):
            self.context_tokens = context_tokens

        def for_reasoning(self):
            return ContextModel(131072)

        def complete(self, messages, tools):
            selected_contexts.append(self.context_tokens)
            return delegate.complete(messages, tools)

    container = replace(container, chat_model=ContextModel())
    isolated_app.state.container = container
    cid = client.post("/api/v1/assistant/conversations", json={"scope_id": "album1"}).json()["id"]
    path = f"/api/v1/assistant/conversations/{cid}/turns"
    headers = {"Idempotency-Key": "reasoning-recovery"}
    payload = {"message": "你好", "reasoning": reasoning}
    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 202
    turn_id = first.json()["turn_id"]
    assert container.turn_repository.get(turn_id).trace["reasoning"] is reasoning
    assert client.post(path, json=payload, headers=headers).json()["turn_id"] == turn_id
    conflict = client.post(path, json={**payload, "reasoning": not reasoning}, headers=headers)
    assert conflict.status_code == 409

    # 旧尝试租约到期，新执行器只从 SQLite 读取模式。
    assert (
        container.turn_repository.claim_due(
            now=datetime.now(UTC) - timedelta(minutes=3), lease_s=1, turn_id=turn_id
        )
        is not None
    )
    executor = replace(container).build_turn_executor()
    assert executor.run_pending(turn_id)
    record = container.turn_repository.get(turn_id)
    assert record.attempt_no == 2 and record.status.is_terminal
    assert record.trace["reasoning"] is reasoning
    assert selected_contexts and set(selected_contexts) == {131072 if reasoning else 16384}
    budget = record.trace["budget"]
    if reasoning:
        assert budget["profile"] == "reasoning"
        assert budget["max_steps"] == 100 and budget["max_tool_calls"] == 600
        assert budget["max_search_calls"] == 100
        assert budget["request_limits"]["max_model_calls"] == 100
        assert budget["request_limits"]["max_other_tool_calls"] == 500
        assert budget["max_page_calls"] is None and budget["max_inspections"] is None
        assert budget["wall_time_s"] >= 3600
    else:
        assert budget["profile"] == "goal_driven_candidate"
        assert budget["max_steps"] == container.settings.agent_max_steps
        assert budget["max_tool_calls"] == container.settings.agent_max_tool_calls


def test_reasoning_survives_worker_start_failure_and_idempotent_retry(isolated_app):
    from sentrix.application.assistant.run_turn import TurnRequest
    from sentrix.bootstrap.assistant_wiring import build_run_turn

    client = TestClient(isolated_app)
    container = isolated_app.state.container
    cid = client.post("/api/v1/assistant/conversations", json={"scope_id": "album1"}).json()["id"]
    runner = build_run_turn(container, reasoning=True)
    request = TurnRequest(conversation_id=cid, question="你好", reasoning=True, idempotency_key="failed")
    record = runner.start(request)
    runner.fail_start(record.id)
    saved = runner.start(request)
    assert saved.id == record.id and saved.status is TurnStatus.FAILED
    assert saved.trace["reasoning"] is True
    assert saved.trace["worker_error"] == "worker_start_failed"


def test_reasoning_preserves_cloud_model_policies(isolated_app):
    from sentrix.bootstrap.assistant_wiring import build_run_turn
    from sentrix.bootstrap.model_roles import ModelProfile

    profile = ModelProfile(
        profile="cloud",
        roles={},
        agent={
            "max_steps": 10,
            "max_tool_calls": 4,
            "wall_time_s": 7200,
            "history_char_budget": 64000,
            "judge_message_chars": 3000,
            "claim_max_facts": 40,
            "read_text_chars": 4800,
        },
    )
    container = replace(isolated_app.state.container, model_profile=profile)
    normal = build_run_turn(container)
    reasoning = build_run_turn(container, reasoning=True)
    assert normal._profile.max_steps == 10 and normal._profile.max_tool_calls == 4
    assert reasoning._profile.max_steps == reasoning._profile.max_model_calls == 100
    assert reasoning._profile.max_tool_calls == 600
    assert reasoning._profile.max_search_calls == 100
    assert reasoning._profile.max_other_tool_calls == 500
    assert reasoning._profile.wall_time_s == 7200
    assert reasoning._profile.history_char_budget == normal._profile.history_char_budget == 64000
    assert reasoning._profile.judge_message_chars == normal._profile.judge_message_chars == 3000
    assert reasoning._profile.claim_max_facts == normal._profile.claim_max_facts == 40
    assert reasoning._profile.compaction_mode == normal._profile.compaction_mode == "drop_only"
    assert reasoning._model is container.chat_model
    assert reasoning._use_all_tools and not normal._use_all_tools


def test_specific_claim_keeps_other_turns_pending_and_fences_expired_attempts(isolated_app):
    container = isolated_app.state.container
    turns = container.turn_repository
    now = datetime.now(UTC)
    for number in range(2):
        conversation = container.conversation_repository.create(
            scope_id="album1", title="测试", created_at=now
        )
        turns.create(
            TurnRecord(
                id=f"turn_{number}",
                conversation_id=conversation.id,
                scope_id="album1",
                question="你好",
                status=TurnStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )

    claimed = turns.claim_due(now=now, lease_s=120, turn_id="turn_1")
    assert claimed.id == "turn_1"
    assert claimed.attempt_no == 1
    assert claimed.fence == 1
    assert turns.get("turn_0").status is TurnStatus.PENDING
    assert turns.claim_due(now=now, lease_s=120, turn_id="turn_1") is None
    assert turns.claim_due(now=now, lease_s=120, turn_id="missing") is None
    assert turns.claim_due(now=now, lease_s=120).id == "turn_0"

    recovered_at = now + timedelta(seconds=121)
    recovered = turns.claim_due(now=recovered_at, lease_s=120, turn_id="turn_1")
    assert recovered.attempt_no == 2
    assert recovered.fence == 2
    assert not turns.finish_with_fence(
        "turn_1", fence=claimed.fence, now=recovered_at, status="complete", termination_reason="answered"
    )
    assert turns.get("turn_1").status is TurnStatus.RUNNING


def test_immediate_dispatch_respects_dependency_wait_and_retry_time(isolated_app):
    container = isolated_app.state.container
    turns = container.turn_repository
    now = datetime.now(UTC)
    conversation = container.conversation_repository.create(scope_id="album1", title="测试", created_at=now)
    turns.create(
        TurnRecord(
            id="turn_wait",
            conversation_id=conversation.id,
            scope_id="album1",
            question="你好",
            status=TurnStatus.PENDING,
            created_at=now,
            updated_at=now,
            execution_state="waiting_dependency",
        )
    )
    executor = container.build_turn_executor()
    assert not executor.run_pending("turn_wait")
    assert turns.get("turn_wait").attempt_no == 0
    assert turns.wake_waiting(now=now) == 1
    claimed = turns.claim_due(now=now, lease_s=120, turn_id="turn_wait")
    turns.set_execution_state(
        "turn_wait",
        claimed.fence,
        now=now,
        execution_state="retry_scheduled",
        next_retry_at=now + timedelta(hours=1),
        pending=True,
    )
    assert not executor.run_pending("turn_wait")
    assert turns.get("turn_wait").attempt_no == 1


def test_http_idempotency_and_background_recovery_do_not_repeat_active_work(isolated_app):
    container = isolated_app.state.container
    delegate = container.chat_model
    entered, release = Event(), Event()

    class BlockedModel:
        def complete(self, messages, tools):
            entered.set()
            assert release.wait(5)
            return delegate.complete(messages, tools)

    container = replace(container, chat_model=BlockedModel())
    isolated_app.state.container = container
    client = TestClient(isolated_app)
    conversation = client.post("/api/v1/assistant/conversations", json={"scope_id": "album1"}).json()
    path = f"/api/v1/assistant/conversations/{conversation['id']}/turns"
    kwargs = {"json": {"message": "你好"}, "headers": {"Idempotency-Key": "active-repeat"}}
    turn_id = ""
    try:
        first = client.post(path, **kwargs)
        assert first.status_code == 202
        turn_id = first.json()["turn_id"]
        assert entered.wait(5)
        assert container.turn_repository.get(turn_id).attempt_no == 1
        executor = container.build_turn_executor()
        assert not executor._run_once()
        second = client.post(path, **kwargs)
        assert second.status_code == 202
        assert second.json()["turn_id"] == turn_id
        assert not executor.run_pending(turn_id)
        assert container.turn_repository.get(turn_id).attempt_no == 1
    finally:
        release.set()
        for thread in enumerate_threads():
            if thread.name == f"turn-{turn_id}":
                thread.join(timeout=5)
                assert not thread.is_alive()
    assert container.turn_repository.get(turn_id).status.is_terminal
