"""Exercise deployed chat logic with real diagnostics and isolated session copies; no model calls."""

import copy
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from photobench.application.interactive.chat import ChatService
from photobench.application.run_execution.service import RunExecution
from photobench.infrastructure.chat.store import FileChatStore

port = int(sys.argv[1])
product_port = {8772: 8191, 8872: 8291, 8874: 8294}[port]


def get(service_port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{service_port}{path}", timeout=30) as response:
        return json.load(response)


diagnostics = get(product_port, "/api/v1/diagnostics")
sessions = get(port, "/api/v1/chat/sessions")["items"]
if sessions:
    session = get(port, "/api/v1/chat/sessions/" + sessions[0]["session_id"])
else:
    effective = diagnostics["runtime_configuration"]
    session = {
        "session_id": "chat-" + "0" * 24,
        "created_at": "isolated probe",
        "status": "ready",
        "album_id": "probe",
        "scope_id": "probe",
        "title": "probe",
        "conversation_id": "probe",
        "turns": [],
        "model_profile": "__current__",
        "manager_url": "",
        "model_base_url": effective["vlm_base_url"],
        "endpoint_model": "",
        "build_revision": "previous-benchmark",
        "turn_timeout": 900,
        "runtime_snapshot": {
            "sentrix_url": f"http://127.0.0.1:{product_port}",
            "model_base_url": effective["vlm_base_url"],
            "required_build_revision": "previous-product",
            "product_build_revision": "previous-product",
        },
    }
original = copy.deepcopy(session)
assert original["runtime_snapshot"]["required_build_revision"] != diagnostics["build_revision"]
messages = []
prepared = []


class Client:
    def diagnostics(self):
        return get(product_port, "/api/v1/diagnostics")

    def start_turn(self, **inputs):
        messages.append(inputs)
        return {"turn_id": "isolated-verification-turn"}

    def get_turn(self, **inputs):
        return {"status": "complete", "result": {"answer": "isolated verification"}}


client = Client()


class Preparation:
    def prepare_interactive_model(self, request, cancel):
        execution = object.__new__(RunExecution)
        execution.client = client
        execution.runtime_snapshot = request.runtime_snapshot
        execution.sentrix_switch = SimpleNamespace()
        verified = execution._sync_sentrix_endpoint(request)
        prepared.append(verified)
        return {"model_name": original.get("model_name"), "phases": {"model_deploy": verified}}


with tempfile.TemporaryDirectory(prefix="chat-revision-verification-") as temporary:
    store = FileChatStore(Path(temporary))
    store.save_session(session)
    service = ChatService(
        store=store,
        suites=SimpleNamespace(
            reserve_interactive=lambda *args, **kwargs: None,
            release_interactive=lambda *args, **kwargs: None,
            interactive_models=SimpleNamespace(revision=get(port, "/api/v1/config")["build_revision"]),
        ),
        albums=None,
        spaces=None,
        client_for=lambda _: client,
        execution_for=lambda _: Preparation(),
    )
    key = session["session_id"]
    service.send_chat(key, {"message": "isolated verification"})
    for _ in range(300):
        if key not in service.workers:
            break
        time.sleep(0.1)
    after = store.load_session(key)
    assert after["status"] == "ready", after.get("error")
    assert len(messages) == len(prepared) == 1
    assert after["turns"][:-1] == original["turns"]
    assert after["runtime_snapshot"] == original["runtime_snapshot"]
    assert after["turns"][-1]["runtime_snapshot"]["required_build_revision"] == diagnostics["build_revision"]
    assert get(port, "/api/v1/chat/sessions")["items"] == sessions
    if sessions:
        assert get(port, "/api/v1/chat/sessions/" + key) == original
    print(
        json.dumps(
            {
                "port": port,
                "old_required_version": original["runtime_snapshot"]["required_build_revision"],
                "new_turn_product_version": prepared[0]["build_revision"],
                "benchmark_version": after["turns"][-1]["build_revision"],
                "old_turns_unchanged": len(original["turns"]),
                "source": "live diagnostics + isolated session + simulated turn; no generation requests",
                "status": after["status"],
            }
        )
    )
