"""Independently recalculate the saved CSV; never access or mutate a live run."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, getcontext
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text())


def read_rows(path):
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len({row["qa_id"] for row in rows}) == 1013
    return {row["qa_id"]: row for row in rows}


def close(value, reference):
    assert abs(value - Decimal(str(reference))) < Decimal("1e-12"), (value, reference)


def main(audit_root):
    getcontext().prec = 50
    current = audit_root / "20261124-doubao-context-comparison"
    previous = audit_root / "20261124-doubao-supplement49-comparison"
    terminal = audit_root / "20261124-doubao-context-terminal"
    comparison = read_json(current / "comparison.json")
    statistics = read_json(current / "supplement-statistics.json")
    accepted = read_json(terminal / "terminal-acceptance.json")
    exported = read_json(terminal / "exports-acceptance.json")
    rows = read_rows(current / "qa-scores.csv")
    old = read_rows(previous / "qa-scores.csv")
    assert rows.keys() == old.keys()
    models = ("qwen37", "q4km", "doubao")
    valid_ids, summaries = {}, {}
    for model in models:
        values = {
            qa: Decimal(row[model + "_score"]) for qa, row in rows.items() if row[model + "_score"] != ""
        }
        assert all(0 <= value <= 1 for value in values.values())
        valid_ids[model] = set(values)
        total, count = sum(values.values(), Decimal(0)), len(values)
        remote = statistics["all_models"][model]
        assert count == comparison["runs"][model]["valid_count"] == remote["valid_scores"]
        close(Decimal(count) / 1013, remote["coverage"])
        close(total / count, comparison["runs"][model]["valid_mean"])
        close(total / 1013, remote["all_1013_missing_as_zero_mean"])
        assert set(comparison["runs"][model]["missing_score_ids"]) == rows.keys() - values.keys()
        summaries[model] = {
            "valid": count,
            "missing": 1013 - count,
            "real_zero_scores": sum(value == 0 for value in values.values()),
            "score_sum": str(total),
            "coverage": str(Decimal(count) / 1013),
            "valid_mean": str(total / count),
            "full_1013_missing_as_error_mean": str(total / 1013),
        }
    common = set.intersection(*valid_ids.values())
    assert common == set(comparison["common_valid_qa_ids"])
    assert len(common) == comparison["common_valid_count"] == 992
    assert common == {qa for qa, row in rows.items() if row["common_valid"] == "True"}
    means = {
        model: sum((Decimal(rows[qa][model + "_score"]) for qa in common), Decimal(0)) / len(common)
        for model in models
    }
    for model in models:
        close(means[model], comparison["common_valid_means"][model]["valid_mean"])
    for pair in comparison["pairwise"]:
        a, b = pair["a"], pair["b"]
        shared = valid_ids[a] & valid_ids[b]
        assert len(shared) == pair["common_valid_count"]
        deltas = [Decimal(rows[qa][a + "_score"]) - Decimal(rows[qa][b + "_score"]) for qa in shared]
        close(sum(deltas, Decimal(0)) / len(shared), pair["a_minus_b"])
        assert sum(value > 0 for value in deltas) == pair["a_higher"]
        assert sum(value < 0 for value in deltas) == pair["b_higher"]
        assert sum(value == 0 for value in deltas) == pair["ties"]
    assert rows == old, "The final CSV must match every field of the saved 994-score baseline."
    missing = rows.keys() - valid_ids["doubao"]
    assert missing == set(accepted["execution_failures"]) == set(exported["retained_execution_failures"])
    assert len(missing) == 19
    execution_counts = Counter(row["doubao_execution_status"] for row in rows.values())
    assert execution_counts == Counter(accepted["execution_counts"])
    for qa in missing:
        failure = accepted["execution_failures"][qa]
        assert rows[qa]["doubao_execution_status"] == failure["execution_status"]
        assert rows[qa]["doubao_termination_reason"] == (failure["termination_reason"] or "")
    assert Decimal(rows["atm-c2d9d893-99d1-4482-99dc-085b074ea099"]["doubao_score"]) == 0
    assert comparison["source_manifests_and_active_mapping_unchanged"]
    assert comparison["agent_calls"] == comparison["judge_calls"] == 0
    assert (
        comparison["runs"]["doubao"]["read_revision"]
        == accepted["read_revision"]
        == exported["read_revision"]
    )
    inputs = [
        current / "comparison.json",
        current / "qa-scores.csv",
        current / "supplement-statistics.json",
        previous / "qa-scores.csv",
        terminal / "exports-acceptance.json",
        terminal / "terminal-acceptance.json",
    ]
    result = {
        "at": datetime.now().astimezone().isoformat(),
        "independent_decimal_recalculation": True,
        "decimal_precision": 50,
        "total": 1013,
        "all_models": summaries,
        "common_valid_count": len(common),
        "common_valid_means": {model: str(value) for model, value in means.items()},
        "pairwise_recalculation_matches": True,
        "all_three_models_all_1013_scores_unchanged_from_994_baseline": True,
        "all_original_994_scored_doubao_csv_rows_unchanged": True,
        "qwen_and_q4_all_csv_fields_unchanged": True,
        "all_csv_fields_unchanged": True,
        "common_valid_set_unchanged": True,
        "changed_csv_rows": [],
        "doubao_execution_counts": dict(execution_counts),
        "raw_nulls_preserved": True,
        "zero_imputation_report_only": True,
        "strawberry_frozen_zero_preserved": True,
        "representation_note": (
            "CSV writes numeric zero as 0 and JSON null termination_reason as an empty field; "
            "validation compares numbers and normalizes only the null representation."
        ),
        "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
    }
    (current / "independent-verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "input_sha256"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
