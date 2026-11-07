"""当前回合尝试的运行时上下文。

检索结果集写入时用它标注 ``turn_id``/``attempt_no``；只读工具在应用层执行，
与执行器同一线程，不需要跨线程传播。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnContext:
    turn_id: str
    attempt_no: int


_context: ContextVar[TurnContext | None] = ContextVar("sentrix_turn_context", default=None)


def current_turn() -> TurnContext | None:
    return _context.get()


@contextmanager
def turn_attempt(turn_id: str, attempt_no: int):
    token = _context.set(TurnContext(turn_id=turn_id, attempt_no=attempt_no))
    try:
        yield
    finally:
        _context.reset(token)
