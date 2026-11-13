"""Optional inference lifetime guard injected by the composition root."""
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import TypeAlias

GenerationGuard: TypeAlias = Callable[[str], AbstractContextManager[None]]


def unguarded_generation(scope_id: str) -> AbstractContextManager[None]:
    return nullcontext()
