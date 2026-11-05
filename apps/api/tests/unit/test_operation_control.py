"""Cancellation requests and acknowledged worker exit are distinct, bounded facts."""

import pytest

from sentrix.domain.media.progress import OperationCancelled
from sentrix.infrastructure.media.operations import InProcessOperationControl


def test_cancellation_is_idempotent_but_not_early_exit_acknowledgement():
    control = InProcessOperationControl(active_limit=1, history_limit=1)
    control.start("turn:a")
    assert control.request_cancel("turn:a")["cleanup_status"] == "pending"
    assert control.request_cancel("turn:a")["cancel_requested"] is True
    with pytest.raises(OperationCancelled):
        control.check("turn:a")
    with pytest.raises(OverflowError):
        control.start("turn:b")
    control.finish("turn:a")
    assert control.request_cancel("turn:a")["cleanup_status"] == "stopped"
    control.start("turn:b")
    control.finish("turn:b")
    with pytest.raises(KeyError):
        control.request_cancel("turn:a")
    assert control.request_cancel("turn:b")["cancel_requested"] is False


def test_unknown_duplicate_and_same_raw_id_in_different_kinds():
    control = InProcessOperationControl()
    with pytest.raises(KeyError):
        control.request_cancel("turn:missing")
    control.start("turn:a")
    control.start("import:a")
    with pytest.raises(ValueError):
        control.start("turn:a")
    control.check("import:a")
    control.finish("unknown")
