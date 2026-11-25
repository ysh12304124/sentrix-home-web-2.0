from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace

import pytest

from sentrix.config import Settings
from sentrix.domain.assistant.models import ToolCall
from sentrix.domain.assistant.profile import REASONING_PROFILE
from sentrix.domain.assistant.request_budget import (
    RequestBudget,
    RequestBudgetExceeded,
    admit_model_attempt,
    final_model_request,
    reset_request_budget,
    set_request_budget,
)
from sentrix.domain.assistant.task_state import TaskState
from sentrix.domain.assistant.tool_policy import ToolAction, ToolPolicy


def test_100_model_attempts_include_two_final_requests_and_block_101():
    budget = RequestBudget()
    token = set_request_budget(budget)
    try:
        for _ in range(98):
            admit_model_attempt(120)
        with pytest.raises(RequestBudgetExceeded):
            admit_model_attempt(120)
        with final_model_request():
            admit_model_attempt(120)
            admit_model_attempt(120)
            with pytest.raises(RequestBudgetExceeded):
                admit_model_attempt(120)
        assert budget.model_attempts == 100
    finally:
        reset_request_budget(token)


def test_search_is_part_of_600_and_categories_cannot_borrow():
    budget = RequestBudget()
    assert all(budget.admit_tool("search_memories") for _ in range(100))
    assert not budget.admit_tool("search_memories")
    assert all(budget.admit_tool("read_source_text") for _ in range(498))
    assert not budget.admit_tool("read_source_text")
    assert budget.admit_tool("final_answer") and budget.admit_tool("final_answer")
    assert not budget.admit_tool("final_answer")
    assert budget.tool_admitted == 600
    assert budget.other_admitted == 500 and budget.search_admitted == 100


def test_invalid_unknown_and_duplicate_proposals_all_consume_admission():
    state = TaskState(
        "t", "c", "s", "q", replace(REASONING_PROFILE, max_tool_calls=6, max_other_tool_calls=6)
    )
    policy = ToolPolicy()
    decisions = [policy.decide(ToolCall(str(i), "unknown", "broken"), state) for i in range(700)]
    assert all(d.action is ToolAction.DENY for d in decisions)
    assert state.request_budget is not None
    assert state.request_budget.other_admitted == 4
    assert state.request_budget.tool_proposed == 700
    assert state.request_budget.tool_denied == 696


def test_parallel_internal_models_share_one_atomic_counter():
    budget = RequestBudget(max_model_calls=12)
    token = set_request_budget(budget)
    try:

        def attempt():
            try:
                admit_model_attempt(30, kind="tool_utility_vlm")
                return True
            except RequestBudgetExceeded:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(copy_context().run, attempt) for _ in range(50)]
            assert sum(f.result() for f in futures) == 10
        assert budget.model_attempts == 10
    finally:
        reset_request_budget(token)


def test_four_turns_do_not_share_budget_and_recovery_does_not_refill():
    def one_turn(_):
        budget = RequestBudget(max_model_calls=7)
        token = set_request_budget(budget)
        try:
            for _ in range(5):
                admit_model_attempt(30)
            return budget.snapshot()
        finally:
            reset_request_budget(token)

    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(one_turn, range(4)))
    assert [x["model_attempts"] for x in saved] == [5] * 4
    restored = RequestBudget(max_model_calls=7)
    restored.restore(saved[0])
    assert not restored.can_model() and restored.can_model(final=True)
    with pytest.raises(ValueError):
        RequestBudget().restore(saved[0])


def test_admission_is_persisted_before_request_and_persistence_failure_blocks_send():
    budget = RequestBudget()
    observed = []
    budget.on_change = lambda payload: observed.append(dict(payload))
    budget.admit_model(10, kind="chat")
    assert observed[0]["model_attempts"] == 1

    def fail(_):
        raise OSError("disk full")

    budget.on_change = fail
    with pytest.raises(OSError):
        budget.admit_model(10, kind="chat")


@pytest.mark.parametrize("value", [None, 0, -1])
def test_reasoning_settings_do_not_allow_unlimited(value):
    with pytest.raises(ValueError):
        Settings(reasoning_max_model_calls=value)
