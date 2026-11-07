"""当前模型调用的工作类型（问答 / 导入）。

``contextvars`` 不会自动传进 ``ThreadPoolExecutor`` 的 worker；导入流水线必须用
``import_task`` 装饰入口，或在提交给线程池的调用点显式包一层。
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

WORK_CHAT = "chat"
WORK_IMPORT = "import"

_work: ContextVar[str] = ContextVar("sentrix_model_work", default=WORK_CHAT)
_T = TypeVar("_T")


def current_work() -> str:
    return _work.get()


@contextmanager
def import_work():
    token = _work.set(WORK_IMPORT)
    try:
        yield
    finally:
        _work.reset(token)


def import_task(method: Callable[..., _T]) -> Callable[..., _T]:
    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> _T:
        with import_work():
            return method(*args, **kwargs)

    return wrapper
