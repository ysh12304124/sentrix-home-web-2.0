"""Watch the three authorized context retries; wake this chat for terminal review."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "20261120-doubao-reuse"))
import watch_run  # noqa: E402

original_review = watch_run.review_reason


def review(snapshot):
    for key in (
        "exact_run_matches",
        "all_source_manifests_unchanged",
        "product_build_revision_matches",
        "product_app_matches",
        "product_sources_match",
        "previous_supplement_manifests_unchanged",
        "evaluation_app_matches",
        "source_manifest_unchanged",
        "prior_source_manifest_unchanged",
        "prior_manifests_unchanged",
        "seed_manifest_unchanged",
        "writer_source_matches",
        "other_stage_mapping_unchanged",
    ):
        if not snapshot[key]:
            return "Doubao three-QA continuation invariant changed: " + key
    budgets = snapshot["execution_budgets"]
    if budgets["qa_concurrency"] != 4 or budgets["turn_timeout"] != 3660:
        return "Doubao supplement concurrency or turn timeout changed"
    if not snapshot["reasoning"] or not snapshot["graph_enabled"]:
        return "Doubao supplement reasoning or graph setting changed"
    if snapshot["status"] not in {"pending", "running", "cancelling"} and any(
        row.get("id") == snapshot["run_id"] and row.get("worker_alive") for row in snapshot["active"]
    ):
        return ""
    return original_review(snapshot)


if __name__ == "__main__":
    watch_run.review_reason = review
    sys.exit(watch_run.main())
