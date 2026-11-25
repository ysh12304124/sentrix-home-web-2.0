"""Small wake messages point to full evidence; local emission persists before dispatch."""

import json
import subprocess
import sys
from pathlib import Path


def terminal_event(reason: str, snapshot: dict, evidence: Path) -> dict:
    fields = (
        "stage",
        "run_id",
        "status",
        "current_phase",
        "item_count",
        "qa_count",
        "model_matches",
        "failed_phase",
        "fatal_error",
        "disk_free",
        "active",
        "consecutive_failures",
        "probe_error",
        "probe_returncode",
    )
    result = {key: snapshot[key] for key in fields if key in snapshot}
    for key in ("probe_error", "fatal_error"):
        if key in result and result[key] is not None:
            result[key] = str(result[key])[:2000]
    if isinstance(result.get("active"), list):
        result["active"] = [
            {k: row.get(k) for k in ("id", "status", "worker_alive")} for row in result["active"][:20]
        ]
    return {"status": "needs_review", "reason": reason, "snapshot": result, "evidence": str(evidence)}


def emit_event(registration: Path, event: Path) -> None:
    private = json.loads(registration.read_text())
    state = private["service"]["state"]
    wake = Path.home() / ".codex/skills/wakecodex/scripts/wakecodex.py"
    subprocess.run(
        [sys.executable, str(wake), "--state", state, "emit", private["id"], "--file", str(event)], check=True
    )
