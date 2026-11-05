"""Import observations, independent of any benchmark or HTTP transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ImportProgress:
    operation_id: str
    scope_id: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    stage: str = "scanning"
    total: int | None = None
    completed: int = 0
    imported: int = 0
    deduplicated: int = 0
    failed: int = 0
    current_file: str | None = None
    updated_at: str = ""
    sequence: int = 0
    error: str | None = None


class ImportProgressStore(Protocol):
    def create(self, progress: ImportProgress) -> None:
        """Reserve an ID; ValueError on duplicate, OverflowError at active capacity."""
        ...

    def save(self, progress: ImportProgress) -> None: ...

    def get(self, operation_id: str) -> ImportProgress | None: ...


class OperationCancelled(RuntimeError):
    """A caller requested cooperative cancellation, not a model failure."""
