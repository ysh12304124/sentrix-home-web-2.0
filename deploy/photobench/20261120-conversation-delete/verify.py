"""Verify deletion online using new empty sessions without invoking a model."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
import urllib.request

from release import BASE, DATABASE, HERE, PROTECTED, api, idle, pid, result_hashes, save, sha


def request(port, path, expected, payload=None, method="GET"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    assert status == expected, (path, status, body[:300])
    return json.loads(body) if body else None


def conversation_inventory():
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT id, state, updated_at FROM agent_conversations WHERE state = 'active' ORDER BY id"
        ).fetchall()
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ["agent_conversation_messages", "agent_turns"]
        }
    return {"active_sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(), **counts}


def main():
    assert not (HERE / "verify-post-intent.json").exists()
    idle()
    activation = json.loads((HERE / "activate-intent.json").read_text())
    before_results = result_hashes()
    before_sessions = api(8874, "chat/sessions")
    before_product = conversation_inventory()
    save("verify-post-intent.json", {"results": before_results, "product": before_product})
    missing = "chat-" + "0" * 24
    request(8874, f"chat/sessions/{missing}", 404, method="DELETE")
    request(8294, "assistant/conversations/conv_delete_verification_missing", 404, method="DELETE")
    mapping = json.loads((BASE / "active-runs.json").read_text())
    scope = mapping["qwen37"]["scope_id"]
    chat = request(8874, "chat/sessions", 202, {"scope_id": scope}, method="POST")
    assert not chat["turns"] and not chat.get("conversation_id") and chat["status"] == "ready"
    save("verify-chat-created.json", {"session_id": chat["session_id"]})
    request(8874, "chat/sessions/" + chat["session_id"], 204, method="DELETE")
    request(8874, "chat/sessions/" + chat["session_id"], 404)
    conversation = request(
        8294,
        "assistant/conversations",
        201,
        {"scope_id": scope, "title": "DELETE API verification (empty)"},
        method="POST",
    )
    save("verify-conversation-created.json", {"conversation_id": conversation["id"]})
    target = "assistant/conversations/" + conversation["id"]
    request(8294, target, 204, method="DELETE")
    request(8294, target, 404)
    assert api(8874, "chat/sessions") == before_sessions
    assert conversation_inventory() == before_product
    assert result_hashes() == before_results
    assert {unit: pid(unit) for unit in PROTECTED} == activation["protected"]
    manifest = json.loads((HERE / "manifest.json").read_text())
    for name, spec in manifest["files"].items():
        assert sha(HERE / spec["kind"] / name) == spec["sha256"], name
        marker = "/dist/"
        if marker in name:
            path = name.split(marker, 1)[1]
            port = 8294 if spec["kind"] == "product" else 8874
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=30) as response:
                assert hashlib.sha256(response.read()).hexdigest() == spec["sha256"], name
    idle()
    save(
        "verify-response.json",
        {
            "ports": [8294, 8874],
            "empty_session_delete": 204,
            "empty_conversation_delete": 204,
            "deleted_get": 404,
            "missing_delete": 404,
            "existing_chats_unchanged": True,
            "existing_conversations_and_turns_unchanged": True,
            "results_unchanged": True,
            "protected_pids_unchanged": True,
            "candidate_and_online_static_hashes_verified": True,
            "model_requests": 0,
        },
    )


if __name__ == "__main__":
    main()
