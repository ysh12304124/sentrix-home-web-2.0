"""Per-turn admission counters, shared by Agent and tool-internal model requests."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

VERSION = "reasoning-bounded-v1"
SEARCH_TOOLS = frozenset({"search_memories"})


class RequestBudgetExceeded(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class RequestBudget:
    max_model_calls: int = 100
    max_tool_calls: int = 600
    max_search_calls: int = 100
    max_other_tool_calls: int = 500
    deadline: float = field(default_factory=lambda: time.monotonic() + 3600)
    final_reserve_s: float = 10
    reserve: int = 2
    model_attempts: int = 0
    model_attempts_by_kind: dict[str, int] = field(default_factory=dict)
    search_admitted: int = 0
    other_admitted: int = 0
    tool_proposed: int = 0
    tool_denied: int = 0
    stop_code: str = ""
    on_change: Callable[[Mapping[str, Any]], None] | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        for value in (self.max_model_calls, self.max_tool_calls, self.max_other_tool_calls):
            if type(value) is not int or value <= self.reserve:
                raise ValueError("request limits must exceed the finalization reserve")
        if type(self.max_search_calls) is not int or self.max_search_calls <= 0:
            raise ValueError("search limit must be a positive integer")

    @property
    def tool_admitted(self) -> int:
        return self.search_admitted + self.other_admitted

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": VERSION,
                **{
                    name: getattr(self, name)
                    for name in (
                        "max_model_calls",
                        "max_tool_calls",
                        "max_search_calls",
                        "max_other_tool_calls",
                        "reserve",
                        "model_attempts",
                        "search_admitted",
                        "other_admitted",
                        "tool_proposed",
                        "tool_denied",
                        "stop_code",
                    )
                },
                "tool_admitted": self.tool_admitted,
                "model_attempts_by_kind": dict(self.model_attempts_by_kind),
            }

    def restore(self, saved: Mapping[str, Any]) -> None:
        if saved.get("version") != VERSION:
            raise ValueError("unsupported request budget version")
        for name in ("max_model_calls", "max_tool_calls", "max_search_calls", "max_other_tool_calls"):
            if saved.get(name) != getattr(self, name):
                raise ValueError("same-turn recovery cannot change the frozen request limits")
        for name in ("model_attempts", "search_admitted", "other_admitted", "tool_proposed", "tool_denied"):
            setattr(self, name, max(getattr(self, name), int(saved[name])))
        self.stop_code = str(saved.get("stop_code") or "")
        self.model_attempts_by_kind = dict(saved.get("model_attempts_by_kind") or {})

    def _changed(self) -> None:
        if self.on_change is not None:
            # Persist admission before sending; concurrent utility workers serialize here.
            self.on_change(self.snapshot())

    def can_model(self, *, final: bool = False) -> bool:
        with self._lock:
            return self.model_attempts < self.max_model_calls - (0 if final else self.reserve)

    def can_tool(self, name: str) -> bool:
        with self._lock:
            final = name == "final_answer"
            if self.tool_admitted >= self.max_tool_calls - (0 if final else self.reserve):
                return False
            if name in SEARCH_TOOLS:
                return self.search_admitted < self.max_search_calls
            return self.other_admitted < self.max_other_tool_calls - (0 if final else self.reserve)

    def admit_tool(self, name: str) -> bool:
        with self._lock:
            self.tool_proposed += 1
            if not self.can_tool(name):
                self.tool_denied += 1
                self.stop_code = "search_call_limit" if name in SEARCH_TOOLS else "other_tool_call_limit"
                if self.tool_admitted >= self.max_tool_calls - (
                    0 if name == "final_answer" else self.reserve
                ):
                    self.stop_code = "tool_call_limit"
                self._changed()
                return False
            if name in SEARCH_TOOLS:
                self.search_admitted += 1
            else:
                self.other_admitted += 1
            self._changed()
            return True

    def admit_model(self, timeout: float, *, kind: str, final: bool = False) -> float:
        with self._lock:
            remaining = self.deadline - time.monotonic() - (0 if final else self.final_reserve_s)
            if not self.can_model(final=final) or remaining <= 0:
                self.stop_code = "model_call_limit" if remaining > 0 else "wall_time_limit"
                self._changed()
                raise RequestBudgetExceeded(self.stop_code)
            self.model_attempts += 1
            self.model_attempts_by_kind[kind] = self.model_attempts_by_kind.get(kind, 0) + 1
            self._changed()
            return min(timeout, remaining)


_current: ContextVar[RequestBudget | None] = ContextVar("turn_request_budget", default=None)
_final: ContextVar[bool] = ContextVar("turn_request_final", default=False)


def current_request_budget() -> RequestBudget | None:
    return _current.get()


def set_request_budget(budget: RequestBudget | None) -> Token[RequestBudget | None]:
    return _current.set(budget)


def reset_request_budget(token: Token[RequestBudget | None]) -> None:
    _current.reset(token)


def admit_model_attempt(timeout: float, *, kind: str = "chat") -> float:
    budget = _current.get()
    return timeout if budget is None else budget.admit_model(timeout, kind=kind, final=_final.get())


@contextmanager
def final_model_request() -> Iterator[None]:
    token = _final.set(True)
    try:
        yield
    finally:
        _final.reset(token)
