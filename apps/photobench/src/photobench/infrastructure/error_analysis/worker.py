"""One process per analysis task; streams committed JSONL, never changes raw runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from photobench.domain.datasets.qa_taxonomy import snapshot_metadata
from photobench.domain.error_analysis.rules import (
    Aggregator,
    case_from_item,
    compare_reports,
    fingerprint,
    snapshot_annotations,
)
from photobench.infrastructure.error_analysis.ai_worker import run_ai
from photobench.infrastructure.results.store import committed_path, run_lock


def source(root: Path, run_id: str) -> dict[str, Any]:
    directory = root / run_id
    with run_lock(directory):
        rows = committed_path(directory, "results.jsonl")
        # Resolve the atomic commit pointer once. Rejudge may publish a newer
        # generation while this worker is reading; all source facts must remain
        # tied to the generation selected by the result path.
        selected = rows.parent
        meta = selected / "metadata.json"
        if meta.is_file():
            state = json.loads(meta.read_text())["overview"]
        else:
            # Legacy state may be large; this runs only in the isolated worker.
            state = json.loads((selected / "run.json").read_text())
            state.pop("items", None)
        identity = _selected_identity(selected)
        runtime = state.get("runtime_snapshot") or {}
        return {
            "run_id": run_id,
            "qa_snapshot": snapshot_metadata(state.get("qa_snapshot") or {}),
            "_qa_rows": (state.get("qa_snapshot") or {}).get("rows") or [],
            "path": str(rows if rows.is_file() else selected / "run.json"),
            "format": "jsonl" if rows.is_file() else "legacy_run_json",
            "revision": fingerprint(identity),
            "status": state.get("status"),
            "mode": state.get("mode"),
            "build_revision": state.get("build_revision") or runtime.get("required_build_revision"),
            "judge": {k: state[k] for k in ("judge_url", "judge_model", "judge_provider_id") if state.get(k)},
            "model": state.get("model_name") or state.get("endpoint_model") or state.get("model"),
            "budgets": {
                "execution": state.get("execution_budgets") or runtime.get("execution_budgets"),
                "product": runtime.get("product_settings"),
            },
            "device": {
                "framework": runtime.get("runtime_framework") or state.get("runtime_framework"),
                "telemetry_source": (state.get("phases", {}).get("gpu_metrics") or {}).get("source"),
            },
            "telemetry": {
                k: v
                for k, v in (state.get("phases", {}).get("gpu_metrics") or {}).items()
                if k
                in {
                    "samples_count",
                    "partial",
                    "memory_complete",
                    "component_validity",
                    "sampling_diagnostics",
                }
            },
            "phase_errors": {
                k: {f: v[f] for f in ("status", "error", "error_details") if f in v}
                for k, v in (state.get("phases") or {}).items()
                if isinstance(v, dict) and (v.get("error") or v.get("status") in {"failed", "partial"})
            },
            "sampling_integrity": ((state.get("phases") or {}).get("gpu_metrics") or {}).get(
                "sampling_integrity"
            ),
            "persistence": state.get("persistence"),
            "cleanup": state.get("cleanup"),
            "expected_items": state.get("qa_count") or state.get("item_count"),
        }


def _identity(directory: Path) -> list[Any]:
    return _selected_identity(committed_path(directory, "results.jsonl").parent)


def _selected_identity(selected: Path) -> list[Any]:
    facts: list[Any] = []
    for name in ("results.jsonl", "metadata.json", "run.json"):
        path = selected / name
        if path.is_file():
            stat = path.stat()
            facts.append([str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns])
    return facts


def revision(root: Path, run_id: str) -> str:
    directory = root / run_id
    with run_lock(directory):
        return fingerprint(_identity(directory))


def lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


def run_worker(
    root: str, directory: str, job: dict[str, Any], cancel: Any, pause: Any, secret: dict[str, Any]
) -> None:
    target = Path(directory)

    def check() -> None:
        if job.get("owner_pid") and os.getppid() != job["owner_pid"]:
            raise InterruptedError("analysis owner exited")
        while pause.is_set() and not cancel.is_set():
            if job.get("owner_pid") and os.getppid() != job["owner_pid"]:
                raise InterruptedError("analysis owner exited")
            time.sleep(0.05)
        if cancel.is_set():
            raise InterruptedError("analysis cancelled")

    def progress(**values: Any) -> None:
        job.update(values)
        atomic(target / "job.json", job)

    try:
        progress(status="running", worker_pid=__import__("os").getpid())
        if job["kind"] == "rules":
            src = source(Path(root), job["run_id"])
            if (src.get("cleanup") or {}).get("status") == "stop_pending":
                raise ValueError("run cleanup has not finished")
            if src["status"] not in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
                "interrupted",
            }:
                raise ValueError("run is not terminal")
            # Pin a private input copy: source snapshot generations may be retired by rejudge.
            pinned = target / "input.jsonl"
            if src["format"] == "jsonl":
                shutil.copyfile(src["path"], pinned)
            else:
                old = json.loads(Path(src["path"]).read_text())
                with pinned.open("w", encoding="utf-8") as out:
                    for item in old.get("items") or []:
                        check()
                        out.write(json.dumps(item, ensure_ascii=False) + "\n")
                del old
            result_hash = hashlib.sha256()
            with pinned.open("rb") as incoming:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    check()
                    result_hash.update(chunk)
            telemetry = Path(root) / job["run_id"] / "gpu_samples.jsonl"
            coverage: dict[str, Any] = {"recorded": 0, "memory_complete": 0, "component_valid": {}}
            if telemetry.is_file():
                for sample in lines(telemetry):
                    check()
                    coverage["recorded"] += 1
                    coverage["memory_complete"] += sample.get("memory_complete") is True
                    for key, value in (sample.get("component_validity") or {}).items():
                        if value == "valid":
                            coverage["component_valid"][key] = coverage["component_valid"].get(key, 0) + 1
            src["telemetry_coverage"] = coverage
            saved_rows = src.pop("_qa_rows", [])
            saved_by_id = {r["qa_id"]: r for r in saved_rows if r.get("qa_id")}
            if len(saved_by_id) != len(saved_rows):
                saved_by_id = {}  # Ambiguous snapshots cannot supply missing labels.
            src["ability_annotation_source"] = "run_item_or_frozen_qa_snapshot"
            aggregate = Aggregator()
            with (target / "cases.jsonl").open("w", encoding="utf-8") as out:
                for i, item in enumerate(lines(pinned)):
                    check()
                    case = case_from_item(snapshot_annotations(item, saved_by_id.get(item.get("qa_id"))), i)
                    aggregate.add(case)
                    out.write(json.dumps(case, ensure_ascii=False) + "\n")
                    if i % 10 == 0:
                        progress(processed=i + 1)
            check()
            if revision(Path(root), job["run_id"]) != src["revision"]:
                raise ValueError("source changed while analysing; retry")
            report = aggregate.report(src)
            report.update(generated_at=time.time(), result_fingerprint=result_hash.hexdigest())
            atomic(target / "report.json", report)
            progress(processed=aggregate.total)
        elif job["kind"] == "compare":
            left = Path(job["options"]["left_path"])
            right = Path(job["options"]["right_path"])
            check()
            report = compare_reports(
                json.loads((left / "report.json").read_text()),
                json.loads((right / "report.json").read_text()),
                list(lines(left / "cases.jsonl")),
                list(lines(right / "cases.jsonl")),
            )
            atomic(target / "comparison.json", report)
        elif job["kind"] == "ai":
            run_ai(target, job["options"], secret, check, progress, lines, atomic)
        else:
            raise ValueError("unknown analysis kind")
        check()
        progress(status="completed", finished_at=time.time())
        # Keep the immutable snapshot for evidence navigation and explicit AI analysis.
    except InterruptedError:
        progress(status="cancelled", finished_at=time.time())
    except Exception as error:  # noqa: BLE001 — task failures never change a run
        progress(status="failed", error=str(error)[:1000], finished_at=time.time())
