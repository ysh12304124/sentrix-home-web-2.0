"""Durable receipts for synchronous pipeline requests."""

from collections.abc import Mapping
from typing import Any, Protocol


class PipelineReceiptStore(Protocol):
    def get(self, operation_id: str) -> dict[str, Any] | None: ...

    def create(self, operation_id: str, scope_id: str, asset_ids: list[str], kind: str) -> None: ...

    def finish(self, operation_id: str, result: Mapping[str, Any] | None, error: str | None) -> None: ...
