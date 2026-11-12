"""普通模式并发提交与持久恢复必须使用同一份执行所有权。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from threading import enumerate as enumerate_threads

from fastapi.testclient import TestClient

from sentrix.domain.assistant.models import TurnRecord, TurnStatus


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
