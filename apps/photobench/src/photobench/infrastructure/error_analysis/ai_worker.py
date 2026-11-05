"""Bounded model IO for durable, resumable diagnostic artifacts."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from photobench.domain.error_analysis.diagnosis import (
    CASE_PROMPT,
    EVIDENCE_VERSION,
    OVERVIEW_PROMPT,
    PROMPT_VERSION,
    coverage,
    evidence_package,
    representative,
    scope_facts,
    task_contract,
    terminal_facts,
    validate_claims,
)
from photobench.domain.error_analysis.query import issue_groups
from photobench.domain.error_analysis.rules import fingerprint
from photobench.domain.runs.judge import judge_chat_url, thinking_kwargs


def run_ai(
    target: Path,
    options: dict[str, Any],
    secret: dict[str, Any],
    check: Callable[[], None],
    progress: Callable[..., None],
    read: Callable[[Path], Iterator[dict[str, Any]]],
    save: Callable[[Path, Any], None],
) -> None:
    parent = Path(options["parent_path"])
    if not (parent / "input.jsonl").is_file():
        raise ValueError("Historical report has no evidence snapshot; regenerate rules first")
    report = json.loads((parent / "report.json").read_text())
    indices = set(options["indices"])
    cases = [c for c in read(parent / "cases.jsonl") if c["index"] in indices]
    signature = fingerprint(
        {
            "source": report["result_fingerprint"],
            "rule_version": report["rule_version"],
            "prompt": PROMPT_VERSION,
            "evidence": EVIDENCE_VERSION,
            "model": secret["model"],
            "url": secret["url"],
            "provider": secret["provider_id"],
            "temperature": 0,
            "max_tokens": 2048,
            "thinking": thinking_kwargs(secret["url"]),
        }
    )
    previous: dict[str, Any] = {}
    if options.get("resume_path"):
        path = Path(options["resume_path"]) / "diagnosis.json"
        if not path.is_file():
            raise ValueError("No saved diagnosis to resume")
        previous = json.loads(path.read_text())
        if previous.get("configuration_fingerprint") != signature:
            raise ValueError("Source, prompt, evidence or model changed; create a fresh analysis")
    reusable = {c["index"]: c for c in previous.get("cases", []) if c.get("valid") and c["index"] in indices}
    limit = int(options.get("max_cases", 12))
    if not 1 <= limit <= 100:
        raise ValueError("max_cases must be between 1 and 100")
    sample = representative([c for c in cases if c["index"] not in reusable], limit)
    if options.get("summary_only"):
        sample = []
    needed = set(sample)
    # Retain bounded packages, not entire raw run in memory.
    packages = {
        i: evidence_package(next(c for c in cases if c["index"] == i), raw)
        for i, raw in enumerate(read(parent / "input.jsonl"))
        if i in needed
    }
    answers = [
        dict(c, reused=c.get("valid") is True) for c in previous.get("cases", []) if c["index"] in indices
    ]
    requests: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": 2,
        "parent_job": options["parent_job"],
        "model": secret["model"],
        "provider_id": secret["provider_id"],
        "prompt_version": PROMPT_VERSION,
        "configuration_fingerprint": signature,
        "scope": {
            "kind": options.get("scope", "selection"),
            "indices": sorted(indices),
            "filters": options.get("filters", {}),
            "result_fingerprint": report["result_fingerprint"],
        },
        "sample_indices": sorted(set(sample) | set(reusable)),
        "requested": len(cases),
        "covered": len(answers),
        "cases": answers,
        "issues": issue_groups(cases),
        "facts": scope_facts(cases),
        "requests": requests,
        "stage": "cases",
        "summary_status": "not_requested",
        "note": "AI结论待核实；引用校验仅确认来源。统计覆盖本范围，语义分析仅覆盖已深查案例。",
    }

    def persist() -> None:
        result["coverage"] = coverage(answers, len(cases))
        result["covered"] = result["coverage"]["reference_valid"]
        save(target / "diagnosis.json", result)
        progress(processed=len(answers), stage=result["stage"])

    persist()

    def request(
        client: httpx.Client, prompt: str, payload: Any, stage: str, index: int | None = None
    ) -> tuple[str | None, str | None]:
        for attempt in range(2):
            check()
            start = time.monotonic()
            entry: dict[str, Any] = {
                "stage": stage,
                "index": index,
                "attempt": attempt + 1,
                "usage": None,
                "status": "failed",
                "input_chars": len(json.dumps(payload, ensure_ascii=False)),
            }
            try:
                response = client.post(
                    judge_chat_url(secret["url"]),
                    headers={"Authorization": "Bearer " + secret["key"]} if secret.get("key") else {},
                    json={
                        "model": secret["model"],
                        "temperature": 0,
                        "max_tokens": 2048,
                        **thinking_kwargs(secret["url"]),
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                    },
                )
                entry["http_status"] = response.status_code
                response.raise_for_status()
                data = response.json()
                entry["usage"] = data.get("usage")
                entry["finish_reason"] = data["choices"][0].get("finish_reason")
                raw = data["choices"][0]["message"]["content"]
                if not isinstance(raw, str):
                    raise ValueError("model content is not text")
                entry["status"] = "completed"
                return raw, None
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                entry["error"] = f"HTTP {code}"
                if 400 <= code < 500:
                    # Configuration/authentication errors stop the whole job, without exposing a URL/key.
                    if code in {400, 401, 403, 404, 422}:
                        raise ValueError(
                            f"Analysis model configuration/authentication error: HTTP {code}"
                        ) from None
                    return None, f"HTTP {code}"
                if attempt == 1:
                    return None, f"HTTP {code}"
            except httpx.RequestError as error:
                entry["error"] = type(error).__name__
                if attempt == 1:
                    return None, type(error).__name__
            except (ValueError, KeyError, IndexError, TypeError):
                entry["error"] = "invalid_response"
                return None, "invalid_response"
            finally:
                entry["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
                requests.append(entry)
                persist()
        return None, "request_failed"

    with httpx.Client(timeout=httpx.Timeout(45, connect=10), trust_env=False) as client:
        for index in sample:
            check()
            case = next(c for c in cases if c["index"] == index)
            package = packages[index]
            save(target / f"evidence-{index}.json", package)
            raw, error = request(client, CASE_PROMPT, package, "cases", index)
            value, invalid = (
                validate_claims(raw, {r["id"] for r in package["records"]}) if raw is not None else ({}, None)
            )
            answers[:] = [a for a in answers if a["index"] != index]
            answers.append(
                {
                    "index": index,
                    "qa_id": case["qa_id"],
                    "valid": not error and not invalid,
                    "status": "failed" if error else "invalid" if invalid else "completed",
                    "result": value,
                    "raw": (raw or "")[:20000],
                    "error": error or invalid,
                    "evidence_fingerprint": fingerprint(package),
                    "omissions": package["omissions"],
                    "reused": False,
                }
            )
            persist()
        if options.get("mode") == "overview":
            check()
            result.update(stage="overview", summary_status="running")
            persist()
            # Full statistics remain in the report. Bound the AI view and disclose omissions.
            groups = [
                {
                    k: g[k]
                    for k in (
                        "group_id",
                        "tool_name",
                        "code",
                        "failure_reason",
                        "component",
                        "affected_items",
                        "affected_calls",
                        "low_score_items",
                    )
                }
                for g in result["issues"][:40]
            ]
            diagnoses = [
                {
                    "id": f"i{a['index']}:diagnosis",
                    "index": a["index"],
                    "result": {
                        "claims": [
                            {k: claim[k] for k in ("kind", "text", "limitations")}
                            for claim in a["result"]["claims"]
                        ],
                        "missing_evidence": a["result"]["missing_evidence"],
                    },
                }
                for a in answers
                if a["valid"]
            ]
            payload: dict[str, Any] = {
                "scope_count": len(cases),
                "facts": result["facts"],
                "coverage": result["coverage"],
                "groups": groups,
                "terminal_outcomes": [],
                "task_contracts": [],
                "diagnoses": [],
            }
            # Recorded expectations and outcomes take priority over potentially conflicting diagnoses.
            contracts = [task_contract(c) for c in cases if c["index"] in {a["index"] for a in answers}]
            for contract in contracts:
                if (
                    len(json.dumps(payload, ensure_ascii=False))
                    + len(json.dumps(contract, ensure_ascii=False))
                    < 42000
                ):
                    payload["task_contracts"].append(contract)
            outcomes = [terminal_facts(c) for c in cases if c["index"] in {a["index"] for a in answers}]
            for outcome in outcomes:
                if (
                    len(json.dumps(payload, ensure_ascii=False))
                    + len(json.dumps(outcome, ensure_ascii=False))
                    < 42000
                ):
                    payload["terminal_outcomes"].append(outcome)
            for diagnosis in diagnoses:
                if (
                    len(json.dumps(payload, ensure_ascii=False))
                    + len(json.dumps(diagnosis, ensure_ascii=False))
                    < 42000
                ):
                    payload["diagnoses"].append(diagnosis)
            payload["omitted_groups"] = len(result["issues"]) - len(groups)
            payload["omitted_diagnoses"] = len(diagnoses) - len(payload["diagnoses"])
            payload["omitted_terminal_outcomes"] = len(outcomes) - len(payload["terminal_outcomes"])
            payload["omitted_task_contracts"] = len(contracts) - len(payload["task_contracts"])
            allowed = (
                {"scope:statistics"}
                | {g["group_id"] for g in groups}
                | {d["id"] for d in payload["diagnoses"]}
                | {o["id"] for o in payload["terminal_outcomes"]}
                | {c["id"] for c in payload["task_contracts"]}
            )
            payload["allowed_evidence_refs"] = sorted(allowed)
            raw, error = request(client, OVERVIEW_PROMPT, payload, "overview")
            value, invalid = validate_claims(raw, allowed) if raw is not None else ({}, None)
            result.update(
                overview=value if not error and not invalid else None,
                summary_status="failed" if error or invalid else "completed",
                summary_error=error or invalid,
                summary_raw=(raw or "")[:20000],
                summary_input=payload,
                stage="completed",
            )
        else:
            result["stage"] = "completed"
        persist()
