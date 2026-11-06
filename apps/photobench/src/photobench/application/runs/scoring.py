"""Background scoring of saved answers; no Agent or product mutations."""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from photobench.application.run_execution.judge import JudgeRunner
from photobench.application.run_execution.profile_scoring import ProfileScoring
from photobench.domain.evaluation.profiles import scoring_targets, summarize_batches
from photobench.domain.runs.analysis import now_iso


def preview(
    state: Mapping[str, Any], batches: list[dict[str, Any]], scope: str, qa_ids: Sequence[str] | None = None
) -> dict[str, Any]:
    selected = [(b, t) for b in batches for t in scoring_targets(state, b, scope, qa_ids)]
    return {
        "qa_count": len({t["qa_id"] for _, t in selected}),
        "turn_count": len({(t["qa_id"], t["turn_index"]) for _, t in selected}),
        "subscore_count": len(selected),
        "diagnostic_only_count": sum(bool(t.get("diagnostics_only")) for _, t in selected),
        "profiles": {b["profile_id"]: len(scoring_targets(state, b, scope, qa_ids)) for b in batches},
    }


def run_scoring(
    state: dict[str, Any],
    batches: list[dict[str, Any]],
    scope: str,
    runner: JudgeRunner,
    assets: Mapping[str, list[dict[str, Any]]],
    checkpoint: Callable[[], None],
    cancel: threading.Event,
    *,
    concurrency: int = 4,
    qa_ids: Sequence[str] | None = None,
    lock: Any = None,
) -> None:
    lock = lock or threading.RLock()
    service = ProfileScoring(runner, state, lock, checkpoint, assets)
    selected = [(b, t) for b in batches for t in scoring_targets(state, b, scope, qa_ids)]
    for batch in batches:
        batch.setdefault("submissions", []).append(
            {
                "started_at": now_iso(),
                "scope": scope,
                "qa_ids": list(qa_ids) if qa_ids is not None else None,
                "targets": [t["key"] for b, t in selected if b["batch_id"] == batch["batch_id"]],
            }
        )
    for batch, target in selected:
        if target["key"] not in batch["targets"]:
            batch["targets"].append(target["key"])
        batch["status"] = "running"
    checkpoint()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        queue = iter(selected)
        futures: dict[concurrent.futures.Future[dict[str, Any]], dict[str, Any]] = {}

        def fill() -> None:
            while len(futures) < concurrency and not cancel.is_set():
                pair = next(queue, None)
                if pair is None:
                    break
                batch, target = pair
                futures[pool.submit(service.score, batch, target)] = target

        fill()
        while futures:
            done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                target = futures.pop(future)
                result = future.result()
                with lock:
                    diagnostic = state.get("diagnostic_results", {}).get(result.get("diagnostic_key"), {})
                    failed = result["status"] != "completed" or diagnostic.get("status") != "completed"
                    receipt = {
                        "sample_id": target["qa_id"],
                        "turn_index": target["turn_index"],
                        "profile_id": result["profile_id"],
                        "batch_id": result["batch_id"],
                        "result_key": target["key"],
                        "status": "failed" if failed else "completed",
                        "score": result.get("score"),
                        "max_score": result.get("max_score"),
                        "reason": result.get("reason"),
                        "started_at": result.get("started_at"),
                        "finished_at": result.get("finished_at"),
                        "diagnostics_only": bool(target.get("diagnostics_only")),
                    }
                    state["rejudge"].setdefault("results", []).append(receipt)
                    if failed:
                        state["rejudge"].setdefault("error_details", []).append(
                            {
                                **receipt,
                                "error_type": "judge_error",
                                "reason": result.get("reason")
                                if result["status"] != "completed"
                                else diagnostic.get("reason") or "shared diagnostics incomplete",
                            }
                        )
                    state["rejudge"]["completed"] += 1
                    state["rejudge"]["failed"] += int(failed)
                checkpoint()
            fill()
    with lock:
        state["scoring_summaries"] = summarize_batches(state)
        for batch in batches:
            batch["status"] = (
                "interrupted" if cancel.is_set() else state["scoring_summaries"][batch["batch_id"]]["status"]
            )
        state["rejudge"].update(
            status="cancelled"
            if cancel.is_set()
            else "completed_with_errors"
            if state["rejudge"]["failed"]
            else "completed",
            finished_at=now_iso(),
        )
    checkpoint()
