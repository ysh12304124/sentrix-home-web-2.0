"""Stage boundaries are isolated per request, include failure, and retain overlap."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier

import pytest

from sentrix.application.media.stage_timing import StageTimings, stage_span
from sentrix.domain.media.progress import OperationCancelled


def test_parallel_workers_keep_request_context_and_overlapping_boundaries():
    recorder = StageTimings()
    ready = Barrier(2)

    def worker(asset_id):
        with stage_span("source_memory_prepare", asset_id):
            ready.wait(timeout=5)

    with recorder.activate(), ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(copy_context().run, worker, asset_id) for asset_id in ("a", "b")]
        for future in tasks:
            future.result(timeout=5)
    spans = recorder.spans()
    assert {span["asset_id"] for span in spans} == {"a", "b"}
    assert max(span["started_offset_seconds"] for span in spans) < min(
        span["finished_offset_seconds"] for span in spans
    )
    assert all(span["status"] == "completed" for span in spans)
    elapsed = max(span["finished_offset_seconds"] for span in spans) - min(
        span["started_offset_seconds"] for span in spans
    )
    assert recorder.walls()["source_memory_prepare_wall"] == round(elapsed, 3)
    # A subsequent request and calls outside the scope cannot inherit old nodes.
    with stage_span("unrelated"):
        pass
    assert len(recorder.spans()) == 2
    assert StageTimings().spans() == []


@pytest.mark.parametrize(
    "error,status", [(ValueError("broken"), "failed"), (OperationCancelled("cancelled"), "cancelled")]
)
def test_failed_and_cancelled_nodes_close_without_swallowing_error(error, status):
    recorder = StageTimings()
    with pytest.raises(type(error)), recorder.activate(), stage_span("video_keyframe_extraction", "video-a"):
        raise error
    span = recorder.spans()[0]
    assert span["status"] == status
    assert span["finished_at"] >= span["started_at"]
    assert span["elapsed_seconds"] >= 0
    assert recorder.walls()["video_keyframe_extraction_wall"] >= 0
