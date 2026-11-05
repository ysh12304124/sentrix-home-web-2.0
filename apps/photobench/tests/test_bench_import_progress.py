"""Synchronous import, live observations and bounded observer teardown, without models."""

import copy
import threading
import time
from types import SimpleNamespace

import pytest

from photobench.application.run_execution.import_progress import import_with_progress
from photobench.infrastructure.sentrix.client import HttpxSentrixClient


class TrackedClient:
    def __init__(self, *, query_error=False, post_error=False, wrong_scope=False):
        self.post_calls = 0
        self.query_calls = 0
        self.operation_id = None
        self.query_error = query_error
        self.post_error = post_error
        self.wrong_scope = wrong_scope
        self.finished = False
        self.read_during_request = threading.Event()

    def import_album_tracked(self, **kwargs):
        self.post_calls += 1
        self.operation_id = kwargs["operation_id"]
        assert self.read_during_request.wait(2)
        self.finished = True
        if self.post_error:
            raise RuntimeError("POST outcome unknown")
        return {"imported": 2}

    def import_progress(self, *, operation_id, timeout):
        assert timeout == 3
        self.query_calls += 1
        if self.operation_id:
            self.read_during_request.set()
        if self.query_error:
            raise RuntimeError("GET disconnected")
        return {
            "operation_id": operation_id,
            "scope_id": "wrong" if self.wrong_scope else "s",
            "completed": 2,
            "total": 2,
            "imported": 1,
            "deduplicated": 1,
            "failed": 0,
            "status": "completed" if self.finished else "running",
            "stage": "completed" if self.finished else "person_insights",
            "sequence": self.query_calls,
        }


def execute(client, observe):
    return import_with_progress(
        client,
        scope_id="s",
        directory="/d",
        limit=9,
        timeout=2,
        interval=0.005,
        observe=observe,
    )


def test_live_media_complete_does_not_complete_request_and_final_fact_is_saved():
    client, observations = TrackedClient(), []
    assert execute(client, lambda p: observations.append(copy.deepcopy(p))) == {"imported": 2}
    assert client.post_calls == 1
    live = [p["observation"] for p in observations if p.get("observation")]
    assert any(p["status"] == "running" and p["completed"] == p["total"] for p in live)
    assert live[-1]["status"] == "completed"
    assert not any(t.name == "photobench-import-progress" for t in threading.enumerate())


@pytest.mark.parametrize("options", [{"query_error": True}, {"wrong_scope": True}])
def test_query_failure_does_not_replay_post_or_invent_counts(options):
    client, observations = TrackedClient(**options), []
    assert execute(client, observations.append)["imported"] == 2
    assert client.post_calls == 1
    assert observations[-1]["availability"] == "unavailable"
    assert "observation" not in observations[-1]
    assert observations[-1]["query_error"]


def test_lost_post_response_stops_observer_without_claiming_product_was_cancelled():
    client = TrackedClient(post_error=True)
    with pytest.raises(RuntimeError, match="outcome unknown"):
        execute(client, lambda p: None)
    calls = client.query_calls
    time.sleep(0.02)
    assert client.query_calls == calls and client.post_calls == 1
    assert not any(t.name == "photobench-import-progress" for t in threading.enumerate())


def test_legacy_client_remains_compatible_and_marks_progress_unsupported():
    observations = []
    client = SimpleNamespace(import_album=lambda **kw: {"imported": 0})
    assert execute(client, observations.append) == {"imported": 0}
    assert observations[0] == {
        "availability": "unsupported",
        "query_error": "client has no progress capability",
    }
    assert observations[-1]["request_status"] == "returned"


def test_tracked_http_calls_use_the_same_frozen_endpoint_and_quote_ids():
    class Http:
        def __init__(self):
            self.calls = []

        def request_json(self, url, *args, **kwargs):
            self.calls.append((url, args, kwargs))
            return {}

    http = Http()
    client = HttpxSentrixClient(http, lambda: "http://frozen:8291")
    client.import_album_tracked(scope_id="s", directory="/d", limit=1, timeout=9, operation_id="one")
    client.import_progress(operation_id="a/b")
    assert http.calls[0][0] == "http://frozen:8291/api/v1/imports"
    assert http.calls[0][1][0]["operation_id"] == "one"
    assert http.calls[1][0] == "http://frozen:8291/api/v1/imports/a%2Fb/progress"


def test_observer_stop_deadline_is_visible_and_late_network_result_cannot_mutate_state():
    from photobench.application.runs.suites import RunSuites

    entered, release = threading.Event(), threading.Event()

    class SlowClient:
        def import_progress(self, **kwargs):
            entered.set()
            assert release.wait(2)
            return {"operation_id": kwargs["operation_id"], "scope_id": "s"}

        def import_album_tracked(self, **kwargs):
            assert entered.wait(1)
            return {"imported": 1}

    observations = []
    try:
        with pytest.raises(TimeoutError, match="stop before deadline"):
            import_with_progress(
                SlowClient(),
                scope_id="s",
                directory="d",
                limit=1,
                timeout=1,
                stop_timeout=0.01,
                observe=observations.append,
            )
        assert observations[-1]["observer_stop_pending"] is True
        assert RunSuites._stop_pending({"phases": {"photo_import": {"progress": observations[-1]}}})
        count = len(observations)
    finally:
        release.set()
    for thread in threading.enumerate():
        if thread.name == "photobench-import-progress":
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert len(observations) == count


def test_import_cancel_retries_registration_race_without_replaying_post():
    cancel = threading.Event()
    cancel.set()
    released = threading.Event()

    class Client(TrackedClient):
        attempts = 0

        def import_album_tracked(self, **kwargs):
            self.post_calls += 1
            self.operation_id = kwargs["operation_id"]
            assert released.wait(3)
            self.finished = True
            return {"imported": 1}

        def cancel_operation(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise KeyError("registration not visible yet")
            released.set()
            return {"operation_id": kwargs["operation_id"], "cleanup_status": "pending"}

    client = Client()
    import_with_progress(
        client,
        scope_id="s",
        directory="d",
        limit=1,
        timeout=3,
        observe=lambda _value: None,
        interval=0.005,
        cancel_event=cancel,
    )
    assert client.attempts == 2
    assert client.post_calls == 1
    assert not any(t.name == "photobench-import-progress" for t in threading.enumerate())
