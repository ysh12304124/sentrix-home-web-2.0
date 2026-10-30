"""Cooperative cancellation response; stopped acknowledges worker exit."""

from typing import Literal

from pydantic import BaseModel


class OperationCancellationResponse(BaseModel):
    operation_id: str
    cancel_requested: bool
    cleanup_status: Literal["pending", "stopped"]
