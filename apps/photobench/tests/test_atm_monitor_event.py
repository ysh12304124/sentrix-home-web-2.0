import importlib.util
import json
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[3] / "deploy/photobench/20261118-atm1013-full/monitor_event.py"
    spec = importlib.util.spec_from_file_location("atm_monitor_event", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_large_summary_and_trace_never_enter_wake_payload(tmp_path):
    mod = module()
    snapshot = {
        "run_id": "exact-run",
        "status": "interrupted",
        "item_count": 95,
        "summary": {"budget_outcomes": ["长尾轨迹" * 100000]},
        "runtime_snapshot": {"private": "not-for-event"},
        "active": [{"id": "exact-run", "worker_alive": False, "cleanup": {"large": "x" * 100000}}],
    }
    evidence = tmp_path / "full-evidence.json"
    evidence.write_text(json.dumps(snapshot))
    result = mod.terminal_event("run stopped", snapshot, evidence)
    assert len(json.dumps(result).encode()) < 65536
    assert result["evidence"] == str(evidence)
    assert result["snapshot"]["run_id"] == "exact-run"
    assert "summary" not in result["snapshot"]
    assert json.loads(evidence.read_text()) == snapshot


def test_local_emit_uses_registration_state_and_exact_wait(tmp_path, monkeypatch):
    mod = module()
    private = tmp_path / "registration.json"
    private.write_text(
        json.dumps(
            {"id": "wait-uuid", "service": {"state": str(tmp_path)}, "callback_token": "private-token"}
        )
    )
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kwargs: calls.append((cmd, kwargs)))
    event = tmp_path / "event.json"
    mod.emit_event(private, event)
    command = calls[0][0]
    assert command[-5:] == [str(tmp_path), "emit", "wait-uuid", "--file", str(event)]
    assert "private-token" not in command
