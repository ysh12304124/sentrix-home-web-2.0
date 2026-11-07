"""检查已审核的最简回答用例是否仍对应当前题干、参考和选项。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def contract_sha(row: Mapping[str, Any]) -> str:
    payload = {k: row.get(k) for k in ("question", "answer", "conversation", "evaluation")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def check(rows: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {row["qa_id"]: row for row in rows}
    errors: list[str] = []
    reviewed: set[str] = set()
    seen: set[str] = set()
    for case in cases:
        qa_id = str(case.get("qa_id", ""))
        if qa_id in seen:
            errors.append(qa_id + ": duplicate case")
        seen.add(qa_id)
        row = by_id.get(qa_id)
        if row is None:
            errors.append(qa_id + ": missing question")
            continue
        if case.get("contract_sha256") != contract_sha(row):
            errors.append(qa_id + ": changed question/reference/options; review again")
        if not case.get("minimal_answer") or not case.get("required_facts") or not case.get("review_reason"):
            errors.append(qa_id + ": incomplete editorial review")
        definition = row.get("evaluation", {})
        if definition.get("scorer") == "choice_judge":
            if case.get("expected_option") != definition.get("correct_option"):
                errors.append(qa_id + ": minimal answer must target correct option")
        elif case.get("expected_option") is not None:
            errors.append(qa_id + ": option supplied for non-choice question")
        reviewed.add(qa_id)
    choice_ids = {row["qa_id"] for row in rows if row.get("evaluation", {}).get("scorer") == "choice_judge"}
    return {
        "passed": not errors,
        "errors": errors,
        "reviewed_cases": len(reviewed),
        "choice_count": len(choice_ids),
        "reviewed_choice_count": len(reviewed & choice_ids),
        "unreviewed_choice_ids": sorted(choice_ids - reviewed),
        "scope": "Editorial regression fixtures; semantic correctness requires saved Judge/human review.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.qa.read_text().splitlines() if line.strip()]
    result = check(rows, json.loads(args.cases.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not result["passed"] or (args.require_all and bool(result["unreviewed_choice_ids"])))


if __name__ == "__main__":
    raise SystemExit(main())
