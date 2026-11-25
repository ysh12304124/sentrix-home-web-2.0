"""Chat follows product upgrades per new message, retaining frozen turn provenance."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fakes_deploy import FakeSentrixProcess
from test_interactive import chat_service, wait_chat
from test_run_execution import _execution

from photobench.application.deploy.service import SentrixModelSwitch


def verified_chat(tmp_path: Path):
    service, suites, client, requests = chat_service(tmp_path)
    observed: dict[str, Any] = {
        "build_revision": "product-before",
        "runtime_configuration": {"vlm_base_url": "http://127.0.0.1:8100/v1"},
    }
    client.diagnostics = lambda: copy.deepcopy(observed)

    class Preparation:
        def prepare_interactive_model(self, request, cancel):
            requests.append(request)
            execution, product, *_ = _execution()
            execution.runtime_snapshot = request.runtime_snapshot
            product.runtime_diagnostics = client.diagnostics()
            execution.sentrix_switch = SentrixModelSwitch(FakeSentrixProcess(current=request.model_base_url))
            verified = execution._sync_sentrix_endpoint(request)
            return {"model_name": "same-model", "phases": {"model_deploy": verified}}

    service.execution_for = lambda _: Preparation()
    return service, suites, client, requests, observed


@pytest.mark.parametrize("legacy", [False, True])
def test_chat_continues_after_upgrade_without_rewriting_old_turns(tmp_path: Path, legacy: bool) -> None:
    service, suites, client, requests, observed = verified_chat(tmp_path)
    session = service.create_chat({"scope_id": "space", "message": "first"})
    key = session["session_id"]
    before = wait_chat(service, key)
    assert before["status"] == "ready"
    if legacy:
        for field in ("runtime_snapshot", "build_revision"):
            before["turns"][0].pop(field)
        before.pop("turn_runtime_snapshot")
        before.pop("turn_build_revision")
        before.update(status="failed", error="Sentrix effective configuration mismatch: build_revision")
        before["pending_messages"] = ["previously rejected, never dispatched"]
        service.store.save_session(before)
    observed["build_revision"] = "product-after"
    suites.interactive_models.revision = "benchmark-after"
    service.send_chat(key, {"message": "second"})
    after = wait_chat(service, key)
    assert after["status"] == "ready" and after["error"] is None
    assert after["runtime_snapshot"] == before["runtime_snapshot"]
    assert after["build_revision"] == before["build_revision"]
    assert after["turns"][0] == before["turns"][0]
    assert after["turns"][1]["runtime_snapshot"]["required_build_revision"] == "product-after"
    assert after["turns"][1]["build_revision"] == "benchmark-after"
    assert [r.runtime_snapshot.connection()["required_build_revision"] for r in requests] == [
        "product-before",
        "product-after",
    ]
    assert [m["message"] for m in client.messages] == ["first", "second"]
    assert client.conversations == ["space"]
    assert not suites.work_status()["work"]


def test_empty_chat_uses_release_at_first_message(tmp_path: Path) -> None:
    service, _, _, _, observed = verified_chat(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    observed["build_revision"] = "new-product"
    service.send_chat(session["session_id"], {"message": "first"})
    after = wait_chat(service, session["session_id"])
    assert after["status"] == "ready"
    assert after["runtime_snapshot"]["required_build_revision"] == "product-before"
    assert after["turns"][0]["runtime_snapshot"]["required_build_revision"] == "new-product"


def test_chat_still_checks_frozen_product_settings_after_upgrade(tmp_path: Path) -> None:
    service, _, client, _, observed = verified_chat(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    session["runtime_snapshot"]["product_settings"] = {"agent_wall_time_s": 180}
    service.store.save_session(session)
    observed["build_revision"] = "new-product"
    observed["runtime_configuration"]["agent_wall_time_s"] = 300
    service.send_chat(session["session_id"], {"message": "first"})
    after = wait_chat(service, session["session_id"])
    assert after["status"] == "failed"
    assert "agent_wall_time_s" in after["error"]
    assert "build_revision" not in after["error"]
    assert not client.messages and not client.conversations


def test_chat_recovery_keeps_accepted_turn_version_and_never_prepares_again(tmp_path: Path) -> None:
    service, _, client, requests, _ = verified_chat(tmp_path)
    session = service.create_chat({"scope_id": "space", "message": "first"})
    key = session["session_id"]
    before = wait_chat(service, key)
    before["status"] = "running"
    before["turns"][0]["status"] = "running"
    service.store.save_session(before)
    client.diagnostics = lambda: pytest.fail("Recovery must not refresh the accepted request")
    service.recover_chats()
    after = wait_chat(service, key)
    assert after["status"] == "ready"
    assert len(requests) == len(client.messages) == 1
    assert after["turns"][0]["runtime_snapshot"] == before["turns"][0]["runtime_snapshot"]
    assert after["turns"][0]["build_revision"] == before["turns"][0]["build_revision"]


def test_chat_rejects_another_release_during_preparation(tmp_path: Path) -> None:
    service, _, client, _, observed = verified_chat(tmp_path)
    session = service.create_chat({"scope_id": "space"})
    readings = iter(
        [
            {**observed, "build_revision": "first-release"},
            {**observed, "build_revision": "second-release"},
        ]
    )
    client.diagnostics = lambda: next(readings)
    service.send_chat(session["session_id"], {"message": "first"})
    after = wait_chat(service, session["session_id"])
    assert after["status"] == "failed"
    assert "build_revision (expected=first-release, actual=second-release)" in after["error"]
    assert not client.messages and not client.conversations
