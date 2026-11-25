"""Verify inherited answers, retained failures, live call budgets and lazy HTTP reads."""

from __future__ import annotations

import json
import sqlite3
import time

from inventory import identity
from recover import BASE, FAILED, HERE, ORIGINAL, REMAINING, ROOT, SEED, api, save, sha

from photobench.infrastructure.results.split_store import SplitRunReader


def main():
    response = json.loads((HERE / "resume-response.json").read_text())
    intent = json.loads((HERE / "recover-intent.json").read_text())
    mapping = json.loads((BASE / "active-runs.json").read_text())
    assert mapping["qwen37"]["run_id"] == response["run_id"]
    seed = SplitRunReader(ROOT, ROOT / SEED)
    current = SplitRunReader(ROOT, ROOT / response["run_id"])
    before, now = seed.state(), current.state()
    expected = {i["qa_id"]: i for i in before["items"]}
    actual = {i["qa_id"]: i for i in now["items"]}
    assert len(expected) == 1010 and expected.keys() <= actual.keys()
    assert actual.keys() - expected.keys() <= REMAINING
    fields = [
        "question",
        "reference_answer",
        "answer",
        "execution_status",
        "termination_reason",
        "attachments",
        "predicted_media",
        "scope_id",
        "model_call_metrics",
        "tool_trace",
    ]
    for qa, item in expected.items():
        for field in fields:
            assert identity(item.get(field)) == identity(actual[qa].get(field)), (qa, field)
        prior_turns, new_turns = item.get("runtime_turns") or [], actual[qa].get("runtime_turns") or []
        assert len(prior_turns) == len(new_turns), qa
        for old_turn, new_turn in zip(prior_turns, new_turns, strict=True):
            for field in (
                "index",
                "message",
                "answer",
                "turn_id",
                "conversation_id",
                "status",
                "termination_reason",
                "attachments",
                "predicted_media",
                "trace",
                "debug_trace",
                "trace_bundle",
                "agent2_trace",
            ):
                assert identity(old_turn.get(field)) == identity(new_turn.get(field)), (qa, field)
    scores = {k: now["scoring_results"][k] for k in before["scoring_results"]}
    assert seed.reader.fingerprint(before["scoring_results"]) == current.reader.fingerprint(scores)
    assert sha(ROOT / FAILED / "split-commit.json") == intent["failed_manifest_sha256"]
    assert sha(ROOT / ORIGINAL / "split-commit.json") == intent["original_manifest_sha256"]
    assert actual["atm-995f8ba1-43ad-423b-82fc-c89d9cb073c9"]["execution_status"] == "failed"
    assert actual["atm-995f8ba1-43ad-423b-82fc-c89d9cb073c9"]["termination_reason"] == "model_error"
    database = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
    with sqlite3.connect("file:" + database + "?mode=ro", uri=True) as db:
        turns = db.execute(
            "SELECT t.id,c.title,t.status FROM agent_turns t "
            "JOIN agent_conversations c ON c.id=t.conversation_id "
            "WHERE t.scope_id=? AND julianday(t.created_at)>=julianday(?)",
            (now["scope_id"], now["started_at"]),
        ).fetchall()
        assert len(turns) == 3 and {r[1] for r in turns} == REMAINING, turns
        budgets = []
        for turn, qa, status in turns:
            rows = db.execute(
                "SELECT payload_json FROM agent_turn_events_v2 WHERE turn_id=? ORDER BY event_seq DESC",
                (turn,),
            )
            event = next(e for (raw,) in rows if (e := json.loads(raw)).get("kind") == "request_budget")
            budget = event["payload"]
            assert [
                budget[k]
                for k in ("max_model_calls", "max_tool_calls", "max_search_calls", "max_other_tool_calls")
            ] == [100, 600, 100, 500]
            budgets.append({"turn_id": turn, "qa_id": qa, "status": status, "budget": budget})
    work = api("active-work")["work"]
    assert any(w["id"] == response["run_id"] and w["worker_alive"] for w in work)
    started = time.monotonic()
    core = api(f"runs/{response['run_id']}/items/0")["item"]
    seconds = time.monotonic() - started
    assert "process_diagnostics" not in core
    assert all(f["path"] != "process_diagnostics" for f in core.get("content_fields", []))
    assert core["trace_read_summary"]["model_call_count"] == 25
    revision = core["trace_storage"]["revision"]
    page = api(f"runs/{response['run_id']}/items/0/trace?turn=0&kind=calls&revision={revision}")
    node = next(n for n in page["nodes"] if n.get("message_count"))
    body = api(
        f"runs/{response['run_id']}/items/0/trace/{node['node_id']}?field=messages.0&limit=1024&revision={revision}"
    )
    assert len(body["text"]) <= 1024 and "$photobench_object_v3" not in body["text"]
    result = {
        "run_id": response["run_id"],
        "inherited_answers_verified": len(expected),
        "inherited_score_receipts_verified": len(scores),
        "source_manifests_unchanged": True,
        "retained_failed_attempt": "atm-995f8ba1-43ad-423b-82fc-c89d9cb073c9",
        "only_three_never_started_questions_executed": True,
        "product_turns": budgets,
        "worker_alive": True,
        "http_core_seconds": seconds,
        "diagnostics_not_in_http_core": True,
        "trace_read_summary": core["trace_read_summary"],
        "node_content_characters": len(body["text"]),
    }
    save("verify-resume-response.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
