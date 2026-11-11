"""Isolated production scoring protocol; explicit connection, no Agent or production writes."""

from __future__ import annotations

import argparse
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from photobench.application.run_execution.judge import JudgeRunner
from photobench.application.run_execution.profile_scoring import ProfileScoring, prepare_batches
from photobench.domain.evaluation.profiles import ATM, profile_configs, scoring_targets
from photobench.infrastructure.http.client import HttpxJsonClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--actual-model", required=True)
    args = parser.parse_args()
    connection = json.loads(args.connection.read_text())
    fixture = json.loads(Path(__file__).with_name("cases-v2.json").read_text())
    # Refuse to overwrite or replay a prior invocation, including unknown HTTP outcomes.
    args.output.mkdir(parents=True, exist_ok=False)

    def work(job):
        case, repeat = job
        name = case["id"] + "-" + str(repeat)
        row = {
            "qa_id": case.get("qa_id", case["id"]),
            "question": case["question"],
            "answer": case["reference"],
            "expected_action": "answer",
            "evaluation": {"scorer": "atm_open", "source_qtype": "open_end", "applies_to_turn": 0},
        }
        record = {
            "qa_id": row["qa_id"],
            "message": case["question"],
            "answer": case["answer"],
            "execution_status": "completed",
            "turn_id": name,
        }
        state = {"run_id": name, "qa_count": 1, "items": [record], "qa_snapshot": {"rows": [row]}}
        batches = prepare_batches(
            state, profile_configs([ATM]), {"url": connection["url"], "model": connection["model"]}
        )
        runner = JudgeRunner(
            http=HttpxJsonClient(),
            judge_url=connection["url"],
            judge_model=connection["model"],
            judge_api_key=connection["api_key"],
        )
        intent = {"case": case, "repeat": repeat, "batch": batches[0], "expected_model": args.actual_model}
        (args.output / (name + ".intent.json")).write_text(json.dumps(intent, ensure_ascii=False, indent=2))
        service = ProfileScoring(runner, state, threading.RLock(), lambda: None, {})
        result = service.score(batches[0], scoring_targets(state, batches[0], "all")[0])
        requests = result.get("requests") or []
        models = [(r.get("raw_response") or {}).get("model") for r in requests if r.get("raw_response")]
        summary = {
            "case_id": case["id"],
            "repeat": repeat,
            "score": result.get("score"),
            "status": result["status"],
            "expected": case["expected"],
            "actual_models": models,
            "matches": result.get("score") == case["expected"] and models == [args.actual_model],
            "requests": len(requests),
        }
        (args.output / (name + ".json")).write_text(
            json.dumps({**summary, "result": copy.deepcopy(result)}, ensure_ascii=False, indent=2)
        )
        return summary

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, [(c, n) for c in fixture["cases"] for n in range(1, 4)]))
    summary = {
        "results": results,
        "judge_requests": sum(r["requests"] for r in results),
        "agent_calls": 0,
        "production_scores_written": 0,
        "all_expected": all(r["matches"] for r in results),
        "fixture_version": fixture["version"],
        "expected_status": fixture["expected_status"],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["all_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
