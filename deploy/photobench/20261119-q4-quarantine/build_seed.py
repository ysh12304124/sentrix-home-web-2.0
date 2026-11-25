#!/usr/bin/env python3
"""Build the one authorized Q4 recovery seed without parsing its long traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

JQ_FILTER = r'''
def filtered($indexes; $heavy; $turn_heavy):
  foreach inputs as $e
    ({};
     .;
     if (($e[0][0] // "") == "items"
         and ($indexes | index($e[0][1])) != null
         and (((($e[0][2] // "") as $key | $heavy | index($key)) != null)
              or (($e[0][2] // "") == "runtime_turns"
                  and (($e[0][4] // "") as $key | $turn_heavy | index($key)) != null)))
     then empty else $e end);
fromstream(1 | filtered($indexes; $heavy; $turn_heavy))
| ($q | map(.qa_id)) as $ids
| ($q | map({key: .qa_id, value: {
      status: "execution_anomaly",
      reason: .reason,
      archive_run_id: $archive_run_id,
      archive_source_row_sha256: .raw_sha256,
      source_generation: $source_generation
  }}) | from_entries) as $markers
| ($ids | sort) as $expected
| ([.items[] | .qa_id as $id | select($expected | index($id)) | $id] | sort) as $found
| if $found != $expected then error("quarantine QA IDs missing or duplicated") else . end
| .items |= map(. as $item
                | if ($ids | index($item.qa_id)) != null
                  then $item + {trace_quarantine: $markers[$item.qa_id]}
                  else $item end)
'''


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def find_item_indexes(source: Path, rows: list[dict[str, Any]]) -> list[int]:
    """Locate exact QA IDs before filtering, independent of object key order."""
    qids = sorted(row["qa_id"] for row in rows)
    selector = r'''
inputs
| . as $event
| select(($event | length) == 2
         and ($event[0] | length) == 3
         and $event[0][0] == "items"
         and $event[0][2] == "qa_id"
         and ($qids | index($event[1])) != null)
| [$event[0][1], $event[1]]
| @json
'''
    decompressor = subprocess.Popen(["gzip", "-dc", str(source)], stdout=subprocess.PIPE)
    assert decompressor.stdout is not None
    jq = subprocess.Popen(
        ["jq", "--stream", "-r", "-n", "--argjson", "qids", json.dumps(qids), selector],
        stdin=decompressor.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    decompressor.stdout.close()
    output, errors = jq.communicate()
    source_status = decompressor.wait()
    if source_status != 0 or jq.returncode != 0:
        raise RuntimeError(
            f"could not scan source item IDs: gzip={source_status} jq={jq.returncode}: {errors}"
        )
    matches = [json.loads(line) for line in output.splitlines()]
    matched_qids = [str(pair[1]) for pair in matches]
    if sorted(matched_qids) != qids:
        raise RuntimeError("quarantine QA IDs are missing or duplicated in the source checkpoint")
    indexes = [int(pair[0]) for pair in matches]
    if len(indexes) != len(set(indexes)):
        raise RuntimeError("multiple quarantined QA IDs resolve to the same source item")
    return indexes


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--activation", type=Path, default=here / "activation.json")
    args = parser.parse_args()
    activation: dict[str, Any] = json.loads(args.activation.read_text(encoding="utf-8"))
    run_id = activation["run_id"]
    run_dir = Path(activation["run_dir"]).resolve()
    if run_dir.name != run_id:
        raise SystemExit("run directory does not match the authorized run")

    manifest_path = run_dir / "resume-quarantine.json"
    final_state = run_dir / "resume-quarantine-state.json.gz"
    if manifest_path.exists() or final_state.exists():
        raise SystemExit("a quarantine artifact already exists; refusing to replace it")

    pointer = json.loads((run_dir / "snapshot-commit.json").read_text(encoding="utf-8"))
    generation = str(pointer.get("generation") or "")
    if generation != activation["source_generation"]:
        raise SystemExit("checkpoint generation changed; refusing to build a seed")
    if not generation.isalnum() or len(generation) != 32:
        raise SystemExit("invalid source generation")

    source = run_dir / ".snapshots" / generation / "run.json.gz"
    if not source.is_file():
        raise SystemExit("expected compressed source checkpoint is missing")
    if digest(source) != activation["source_sha256"]:
        raise SystemExit("source checkpoint checksum does not match the inspected immutable snapshot")
    rows = activation["items"]
    if len({row["qa_id"] for row in rows}) != len(rows):
        raise SystemExit("duplicate QA IDs in quarantine list")
    item_indexes = find_item_indexes(source, rows)

    state_path = run_dir / ("." + uuid.uuid4().hex + ".resume-quarantine.tmp.gz")
    filter_path: Path | None = None
    temp_manifest: Path | None = None
    verification_root: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jq", delete=False) as handle:
            handle.write(JQ_FILTER)
            filter_path = Path(handle.name)
        with state_path.open("xb") as output:
            decompressor = subprocess.Popen(["gzip", "-dc", str(source)], stdout=subprocess.PIPE)
            assert decompressor.stdout is not None
            jq = subprocess.Popen(
                [
                    "jq",
                    "--stream",
                    "-n",
                    "--argjson",
                    "q",
                    json.dumps(rows, separators=(",", ":")),
                    "--argjson",
                    "indexes",
                    json.dumps(item_indexes),
                    "--argjson",
                    "heavy",
                    json.dumps(["tool_trace", "execution_trace", "model_call_metrics"]),
                    "--argjson",
                    "turn_heavy",
                    json.dumps(["trace", "debug_trace", "trace_bundle", "agent2_trace"]),
                    "--arg",
                    "archive_run_id",
                    activation["archive_run_id"],
                    "--arg",
                    "source_generation",
                    generation,
                    "-f",
                    str(filter_path),
                ],
                stdin=decompressor.stdout,
                stdout=subprocess.PIPE,
            )
            decompressor.stdout.close()
            assert jq.stdout is not None
            compressor = subprocess.Popen(["gzip", "-c"], stdin=jq.stdout, stdout=output)
            jq.stdout.close()
            jq_status = jq.wait()
            gzip_status = compressor.wait()
            source_status = decompressor.wait()
            if (source_status, jq_status, gzip_status) != (0, 0, 0):
                raise RuntimeError(
                    f"stream transform failed: gzip={source_status} jq={jq_status} output-gzip={gzip_status}"
                )
            output.flush()
            os.fsync(output.fileno())

        record = {
            "version": 1,
            "status": "ready",
            "run_id": run_id,
            "scope_id": activation["scope_id"],
            "source_generation": generation,
            "qa_count": activation["qa_count"],
            "item_count": activation["item_count"],
            "archive_run_id": activation["archive_run_id"],
            "items": rows,
            "state_file": final_state.name,
            "state_sha256": digest(state_path),
        }

        # Verify with the production loader before the live run can see a manifest.
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps/photobench/src"))
        from photobench.infrastructure.results.resume_quarantine import load_resume_quarantine_state

        verification_root = Path(tempfile.mkdtemp(prefix=".resume-quarantine-verify-", dir=run_dir.parent))
        candidate_dir = verification_root / run_id
        candidate_dir.mkdir()
        shutil.copyfile(run_dir / "snapshot-commit.json", candidate_dir / "snapshot-commit.json")
        os.link(state_path, candidate_dir / final_state.name)
        (candidate_dir / manifest_path.name).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        state = load_resume_quarantine_state(candidate_dir, run_id)
        if state is None:
            raise RuntimeError("resume seed was not activated")
        if json.loads((run_dir / "snapshot-commit.json").read_text(encoding="utf-8")) != pointer:
            raise RuntimeError("source checkpoint changed during seed generation")

        os.replace(state_path, final_state)
        temp_manifest = run_dir / ("." + uuid.uuid4().hex + ".resume-quarantine.json.tmp")
        temp_manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        with temp_manifest.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_manifest, manifest_path)
        descriptor = os.open(run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "source_generation": generation,
                    "items": len(state["items"]),
                    "quarantined": len(rows),
                    "state_bytes": final_state.stat().st_size,
                    "state_sha256": record["state_sha256"],
                    "status": "verified",
                }
            )
        )
    except BaseException:
        # Leave any completed candidate file for inspection, but without a valid manifest
        # it cannot be selected by resume_run.
        raise
    finally:
        if filter_path is not None:
            filter_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        if temp_manifest is not None:
            temp_manifest.unlink(missing_ok=True)
        if verification_root is not None:
            shutil.rmtree(verification_root, ignore_errors=True)


if __name__ == "__main__":
    main()
