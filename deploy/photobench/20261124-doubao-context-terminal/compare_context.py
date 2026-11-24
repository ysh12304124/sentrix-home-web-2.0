"""Recalculate aligned ATM statistics after the new Doubao exports are accepted."""

from __future__ import annotations

import csv
import json
import math
import sys

from accept_context import BASE, HERE, PRIOR, ROOT, RUN, fingerprint, manifest_sha

sys.path.insert(0, str(BASE / "three-model-comparison-20261122-v2"))
import compare_runs  # noqa: E402


def run():
    accepted = json.loads((HERE / "terminal-acceptance.json").read_text())
    exported = json.loads((HERE / "exports-acceptance.json").read_text())
    assert accepted["accepted"] and accepted["run_id"] == exported["run_id"] == RUN
    assert accepted["read_revision"] == exported["read_revision"]
    assert exported["answers_attempts_and_all_score_receipts_unchanged"]
    assert exported["source_manifests_and_product_runtime_unchanged"]
    assert all(exported["exports"][kind]["verified"] for kind in ("trace", "sft"))
    assert accepted["manifest_sha256"] == manifest_sha(RUN)
    compare_runs.RUNS["doubao"] = {
        "model": "doubao-seed-2.1-lite",
        "root": ROOT,
        "run_id": RUN,
        "revision": accepted["read_revision"],
        "valid_count": accepted["valid_atm_scores"],
    }
    output = HERE / "comparison"
    compare_runs.main(output)
    comparison = json.loads((output / "comparison.json").read_text())
    rows = list(csv.DictReader((output / "qa-scores.csv").open()))
    targets = set(json.loads((PRIOR / "plan.json").read_text())["agent_qa_ids"])
    assert len(rows) == 1013 and len(targets) == 3
    new_scores = [float(r["doubao_score"]) for r in rows if r["qa_id"] in targets and r["doubao_score"]]
    assert len(new_scores) == accepted["new_valid_scores"]
    all_qa = {}
    for name, summary in comparison["runs"].items():
        scores = [float(r[name + "_score"]) for r in rows if r[name + "_score"]]
        assert len(scores) == summary["valid_count"]
        assert math.isclose(math.fsum(scores) / len(scores), summary["valid_mean"], abs_tol=1e-12)
        all_qa[name] = {
            "total": 1013,
            "valid_scores": len(scores),
            "coverage": len(scores) / 1013,
            "valid_mean": math.fsum(scores) / len(scores),
            "all_1013_missing_as_zero_mean": math.fsum(scores) / 1013,
            "missing_scores": 1013 - len(scores),
            "real_zero_scores": sum(v == 0 for v in scores),
        }
    assert math.isclose(all_qa["doubao"]["valid_mean"], accepted["valid_mean"], abs_tol=1e-12)
    baseline_file = BASE / "doubao-supplement49-terminal-20261124-v1/comparison/comparison.json"
    baseline = json.loads(baseline_file.read_text())
    result = {
        "run_id": RUN,
        "read_revision": accepted["read_revision"],
        "label": "历史豆包媒体记忆复用+新版邮件增量；161/49题补做；并行工具压缩局部修复后仅补3道上下文题",
        "all_models": all_qa,
        "common_valid_count": comparison["common_valid_count"],
        "common_valid_means": comparison["common_valid_means"],
        "new_three": {
            "submitted_turns": 3,
            "new_valid_scores": len(new_scores),
            "valid_mean": math.fsum(new_scores) / len(new_scores) if new_scores else None,
            "remaining_execution_failures": len(accepted["execution_failures"]),
            "execution_counts": accepted["new_three_execution_counts"],
            "product_status_counts": accepted["new_three_product_status_counts"],
            "product_termination_counts": accepted["new_three_product_termination_counts"],
            "product_attempt_no_counts": accepted["product_attempt_no_counts"],
            "internal_claim_note": (
                "One submitted turn per QA; internal claim generations "
                "recorded separately in terminal acceptance."
            ),
        },
        "before_supplement": {
            "run_id": baseline["runs"]["doubao"]["run_id"],
            "valid_count": baseline["runs"]["doubao"]["valid_count"],
            "valid_mean": baseline["runs"]["doubao"]["valid_mean"],
            "coverage": baseline["runs"]["doubao"]["coverage"],
            "common_valid_count": baseline["common_valid_count"],
        },
        "null_scores_preserved": True,
        "zero_imputation_is_report_only": True,
        "source_baseline_fingerprint": fingerprint(baseline),
        "agent_calls": 0,
        "judge_calls": 0,
    }
    with (output / "supplement-statistics.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run()
