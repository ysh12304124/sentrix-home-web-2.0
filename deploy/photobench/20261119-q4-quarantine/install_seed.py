"""Publish the locally verified Q4 seed once; the original checkpoint remains unchanged."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from publish import HERE, RESULTS, idle, save, sha


def main() -> None:
    source = idle("q4km")
    verification = json.loads((HERE / "real101-preservation.json").read_text())
    if (
        verification.get("status") != "verified"
        or verification.get("all_non_trace_leaves_equal") is not True
        or verification.get("items") != 101
        or verification.get("quarantined") != 18
        or verification.get("qa_count") != 1013
        or verification["canonical_non_trace_stream"]["source"]
        != verification["canonical_non_trace_stream"]["seed"]
    ):
        raise RuntimeError("non-trace preservation verification is incomplete")
    staged = HERE / "verified-seed"
    manifest = json.loads((staged / "resume-quarantine.json").read_text())
    if source["run_id"] != manifest["run_id"] or source["generation"] != manifest["source_generation"]:
        raise RuntimeError("source run or generation changed")
    run_dir = RESULTS["q4km"] / source["run_id"]
    original = run_dir / ".snapshots" / source["generation"] / "run.json.gz"
    if sha(original) != "4f57fb6d56e9c2bb433ade75c97fa0e4eab7ae911670dfd9ff041cd3e1679efe":
        raise RuntimeError("original checkpoint checksum changed")
    if sha(staged / "resume-quarantine-state.json.gz") != manifest["state_sha256"]:
        raise RuntimeError("transferred seed checksum changed")
    for name in ["resume-quarantine.json", "resume-quarantine-state.json.gz"]:
        if (run_dir / name).exists():
            raise RuntimeError("quarantine seed already exists; inspect its receipt")

    sys.path.insert(0, str(HERE / "evaluation/apps/photobench/src"))
    from photobench.infrastructure.results.resume_quarantine import load_resume_quarantine_state

    with tempfile.TemporaryDirectory(prefix=".verify-quarantine-", dir=run_dir.parent) as temporary:
        candidate = Path(temporary) / source["run_id"]
        candidate.mkdir()
        shutil.copyfile(run_dir / "snapshot-commit.json", candidate / "snapshot-commit.json")
        for name in ["resume-quarantine.json", "resume-quarantine-state.json.gz"]:
            os.link(staged / name, candidate / name)
        state = load_resume_quarantine_state(candidate, source["run_id"])
        if state is None:
            raise RuntimeError("production loader did not select the reduced seed")
        save(
            "install-seed-intent.json",
            {"source": source, "manifest": manifest, "preservation": verification},
        )
        # Same filesystem: the verified compressed file is shared without another large copy.
        os.link(staged / "resume-quarantine-state.json.gz", run_dir / "resume-quarantine-state.json.gz")
        temporary_manifest = run_dir / ".resume-quarantine.install.tmp"
        with temporary_manifest.open("x") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_manifest.replace(run_dir / "resume-quarantine.json")
    if idle("q4km") != source:
        raise RuntimeError("source checkpoint changed while publishing seed")
    save("install-seed-response.json", {
        "status": "verified", "run_id": source["run_id"], "items": len(state["items"]),
        "quarantined": 18, "seed_sha256": manifest["state_sha256"], "original_checkpoint_unchanged": True,
    })
    print((HERE / "install-seed-response.json").read_text())


if __name__ == "__main__":
    main()
