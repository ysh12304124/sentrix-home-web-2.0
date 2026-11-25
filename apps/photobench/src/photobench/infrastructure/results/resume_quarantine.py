"""Read the explicit, run-scoped reduced checkpoint used for one recovery."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_NAME = "resume-quarantine.json"
STATE_NAME = "resume-quarantine-state.json.gz"
_ITEM_TRACES = {"tool_trace", "execution_trace", "model_call_metrics"}
_TURN_TRACES = {"trace", "debug_trace", "trace_bundle", "agent2_trace"}
AUTHORIZED_RUN_ID = (
    "20261119-021603-2026.11.17-atm-sources-addresses-current-resume-62cc28"
)
AUTHORIZED_GENERATION = "6d296ab38f414887a55fc0aade1672b0"
AUTHORIZED_SCOPE_ID = "PhotoBench-20261118-043111-2026.11.17-atm-sources-addresses-current"
AUTHORIZED_ARCHIVE_RUN_ID = (
    "20261118-175624-2026.11.17-atm-sources-addresses-current-resume-7955c3"
)
AUTHORIZED_QA_IDS = frozenset(
    {
        "atm-abcd9ba4-1978-4c5e-83ed-20ee52d44064",
        "atm-23a4e35d-ec2f-479a-b269-46f9ae5fb5bf",
        "atm-ba3c77e1-a455-447f-b0c8-4053ae7604d7",
        "atm-988c300f-d262-4954-af22-faf0e667e766",
        "atm-f05d1f2b-5b05-4386-8f32-1c0545cd1763",
        "atm-7db5214d-93df-44ed-be84-ecda6f8dfb84",
        "atm-5cbc8901-703d-43a0-a23b-81da625f6b1b",
        "atm-cf6d66d4-038e-4418-8557-f91a63b86a95",
        "atm-26ad398f-84e8-4043-8f5a-04eeb7c89db0",
        "atm-e388993e-fac9-44a2-a83d-d6ee44be5ee8",
        "atm-ef844997-43b1-4e83-b3fb-589ff43dc84c",
        "atm-ad27afed-8fed-4132-9f69-7594cd73e166",
        "atm-6659ae64-76b2-4a0f-bf40-10417c77e32a",
        "atm-a860c4fd-19b5-47bb-a78b-3a0fa2556048",
        "atm-46bcf4b0-b6f2-4d4b-8bc5-1d2630d8bb65",
        "atm-7b237e19-f4cc-4763-b5d3-6e370388793c",
        "atm-523c38f4-7fb5-4c85-906b-2882f93225c2",
        "atm-56c6f29c-3d29-47c0-8102-7188abf5da0b",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_resume_quarantine_state(run_dir: Path, run_id: str) -> dict[str, Any] | None:
    """Load a derived seed only when its exact source checkpoint still matches."""
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None

    if run_id != AUTHORIZED_RUN_ID or run_dir.name != AUTHORIZED_RUN_ID:
        raise ValueError("resume quarantine is not authorized for this run")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("status") != "ready":
        raise ValueError("invalid resume quarantine manifest")
    if manifest.get("run_id") != run_id:
        raise ValueError("resume quarantine run identity mismatch")
    if manifest.get("source_generation") != AUTHORIZED_GENERATION:
        raise ValueError("resume quarantine generation is not authorized")
    if manifest.get("scope_id") != AUTHORIZED_SCOPE_ID:
        raise ValueError("resume quarantine scope is not authorized")
    if manifest.get("archive_run_id") != AUTHORIZED_ARCHIVE_RUN_ID:
        raise ValueError("resume quarantine archive lineage is not authorized")

    pointer = json.loads((run_dir / "snapshot-commit.json").read_text(encoding="utf-8"))
    if pointer.get("generation") != manifest.get("source_generation"):
        raise ValueError("resume quarantine source generation changed")

    state_path = run_dir / STATE_NAME
    if _sha256(state_path) != manifest.get("state_sha256"):
        raise ValueError("resume quarantine state checksum mismatch")
    with gzip.open(state_path, "rt", encoding="utf-8") as handle:
        state = json.load(handle)

    if (
        not isinstance(state, dict)
        or state.get("run_id") != run_id
        or state.get("scope_id") != manifest.get("scope_id")
        or state.get("qa_count") != manifest.get("qa_count")
        or len(state.get("items") or []) != manifest.get("item_count")
    ):
        raise ValueError("resume quarantine state does not match its source")

    expected = {str(row["qa_id"]) for row in manifest.get("items") or []}
    if expected != AUTHORIZED_QA_IDS:
        raise ValueError("resume quarantine QA set is not authorized")
    items = [item for item in state.get("items") or [] if isinstance(item, dict)]
    actual = {
        str(item.get("qa_id"))
        for item in items
        if isinstance(item.get("trace_quarantine"), dict)
        and item["trace_quarantine"].get("status") == "execution_anomaly"
    }
    if not expected or actual != expected:
        raise ValueError("resume quarantine QA set mismatch")

    for item in items:
        if str(item.get("qa_id")) not in expected:
            continue
        if _ITEM_TRACES.intersection(item):
            raise ValueError("quarantined item still contains top-level trace data")
        if any(
            isinstance(turn, dict) and _TURN_TRACES.intersection(turn)
            for turn in item.get("runtime_turns") or []
        ):
            raise ValueError("quarantined item still contains turn trace data")
    return state
