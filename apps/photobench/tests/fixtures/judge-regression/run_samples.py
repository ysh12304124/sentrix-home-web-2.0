"""Isolated Judge samples. No Agent, production batches, or automatic retries.

Run with the PhotoBench package on PYTHONPATH. Credentials stay in --connection;
requests and responses (without authorization headers) go to a fresh --output.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from photobench.application.run_execution.judge import JudgeRunner
from photobench.domain.evaluation.atm.scoring import deterministic_accuracy
from photobench.domain.runs.judge import (
    judge_chat_url,
    normalize_judge_score,
    parse_judge_json,
    thinking_kwargs,
)
from photobench.infrastructure.http.client import HttpxJsonClient


def parse_score(case, response):
    parsed = parse_judge_json(response["choices"][0]["message"]["content"])
    if case["profile_id"] == "photobench_open_v1":
        score = normalize_judge_score(parsed.get("score"))["score"]
        if score is None:
            raise ValueError("Invalid 0/1/2 score")
    elif case["qtype"] == "open_end":
        value = parsed.get("accuracy")
        if type(value) is not bool and value not in ("true", "false"):
            raise ValueError("Invalid boolean accuracy")
        score = int(str(value).lower() == "true")
    else:
        score = parsed.get("score")
        if type(score) is not int or score not in (0, 1):
            raise ValueError("Invalid numeric score")
    return score, parsed


class Capture(JudgeRunner):
    """Build the production task_v4 request without dispatching it."""

    def _request(self, payload, timeout=180, observations=None):
        self.payload = payload
        return {"choices": [{"message": {"content": '{"score":2,"reason":""}'}}]}, 1


def build_request(case, variant, prompts, connection):
    profile = case["profile_id"]
    purpose = (
        "answer_quality"
        if profile == "photobench_open_v1"
        else ("number" if case["qtype"] == "number" else "open")
    )
    if profile == "photobench_open_v1":
        if variant != "current":
            raise ValueError("No official 0/1/2 protocol")
        runner = Capture(
            http=HttpxJsonClient(),
            judge_url=connection["url"],
            judge_model=connection["model"],
            answer_quality_prompt=prompts["current"][profile][purpose],
        )
        runner.answer_quality_input_version = "task_v4"
        runner._legacy_judge(
            case["question"],
            case["reference"],
            case["answer"],
            conversation=[{"message": case["question"], "answer": case["answer"]}],
            expected_action="answer",
            task_type=None,
            question_type=case["qtype"],
            answerability=None,
            media_aliases=[],
            tool_aliases=[],
        )
        return runner.payload
    if variant == "official_open":
        if case["qtype"] != "open_end":
            raise ValueError("Official numeric evaluator is code, not an LLM prompt")
        prompt = prompts["official_open"]
        for field, value in (
            ("question", case["question"]),
            ("answer", case["reference"]),
            ("prediction", case["answer"]),
        ):
            prompt = prompt.replace("{{" + field + "}}", value)
        messages = [{"role": "user", "content": prompt}]
    else:
        data = {
            "question": case["question"],
            "answer": case["answer"],
            "reference": case["reference"],
            "expected_action": "answer",
            "conversation": [{"question": case["question"], "answer": case["answer"]}],
        }
        messages = [
            {"role": "system", "content": prompts["current"][profile][purpose]},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
    return {
        "model": connection["model"],
        "temperature": 0,
        "max_tokens": 2048,
        **thinking_kwargs(connection["url"]),
        "messages": messages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--actual-model", required=True)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("At least three independent samples are required")
    root = Path(__file__).parent
    fixtures = json.loads((root / "cases-v1.json").read_text())
    prompts = json.loads((root / "prompts-v1.json").read_text())
    connection = json.loads(args.connection.read_text())
    cases = [c for c in fixtures["cases"] if not args.case or c["id"] in args.case]
    if not cases or set(args.case) - {c["id"] for c in cases}:
        parser.error("Unknown sample")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        variants = (
            ["current", "official_open"]
            if case["qtype"] == "open_end" and case["profile_id"] == "atm_qs_v1"
            else ["current"]
        )
        for variant in variants:
            for repeat in range(1, args.repeats + 1):
                name = f"{case['id']}--{variant}--{repeat}"
                path = args.output / (name + ".json")
                intent = args.output / (name + ".intent.json")
                if path.exists():
                    results.append(json.loads(path.read_text()))
                    continue
                if intent.exists():
                    raise RuntimeError(f"Outcome unknown: {name}; inspect before any new request")
                payload = build_request(case, variant, prompts, connection)
                result = {
                    "case_id": case["id"],
                    "variant": variant,
                    "repeat": repeat,
                    "request": payload,
                    "expected": case["expected"],
                    "review_status": case["review_status"],
                    "gt": fixtures["gt_config"],
                    "protocol": "task_v4" if case["profile_id"] == "photobench_open_v1" else variant,
                    "retries": 0,
                }
                with intent.open("x") as out:
                    json.dump({**result, "case": case, "at": time.time()}, out, ensure_ascii=False, indent=2)
                start = time.monotonic()
                try:
                    request = urllib.request.Request(
                        judge_chat_url(connection["url"]),
                        data=json.dumps(payload).encode(),
                        headers={
                            "Authorization": "Bearer " + connection["api_key"],
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(request, timeout=120) as response:
                        body = json.load(response)
                    result.update(response=body, actual_model=body.get("model"))
                    if body.get("model") != args.actual_model:
                        raise ValueError("Actual model drift")
                    score, parsed = parse_score(case, body)
                    result.update(
                        status="scored",
                        score=score,
                        parsed=parsed,
                        expectation_matches=None if case["expected"] is None else score == case["expected"],
                    )
                except Exception as error:
                    result.update(
                        status="unscored", score=None, error=type(error).__name__ + ": " + str(error)[:300]
                    )
                result["elapsed_seconds"] = time.monotonic() - start
                with path.open("x") as out:
                    json.dump(result, out, ensure_ascii=False, indent=2)
                results.append(result)
                print(name, result["status"], result["score"], flush=True)
    summary = {
        "version": fixtures["version"],
        "requests": len(results),
        "scored": sum(r["status"] == "scored" for r in results),
        "mismatches": [r["case_id"] for r in results if r.get("expectation_matches") is False],
        "official_numeric_code": [
            {
                "case_id": c["id"],
                "score": int(deterministic_accuracy(c["reference"], c["answer"], c["question"])),
            }
            for c in fixtures["cases"]
            if c["qtype"] == "number"
        ],
        "agent_calls": 0,
        "production_rejudged": 0,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
