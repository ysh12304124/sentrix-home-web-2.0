"""Bounded model IO for durable, resumable diagnostic artifacts."""

from __future__ import annotations

import base64
import hashlib
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
    scope_facts,
    task_contract,
    terminal_facts,
    validate_case_diagnosis,
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
    gt_inputs = {i: json.loads((target / "gt" / f"input-{i}.json").read_text()) for i in sorted(indices)}
    signature = fingerprint(
        {
            "source": report["result_fingerprint"],
            "rule_version": report["rule_version"],
            "prompt": PROMPT_VERSION,
            "evidence": EVIDENCE_VERSION,
            "gt_inputs": gt_inputs,
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
    by_index = {c["index"]: c for c in cases}
    if set(by_index) != indices:
        raise ValueError("Selected cases are missing from the frozen report")
    needed = set() if options.get("summary_only") else indices - reusable.keys()
    answers = [
        dict(c, reused=c.get("valid") is True) for c in previous.get("cases", []) if c["index"] in indices
    ]
    requests: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": 3,
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
        "requested": len(cases),
        "covered": len(answers),
        "cases": answers,
        "issues": issue_groups(cases),
        "facts": scope_facts(cases),
        "requests": requests,
        "stage": "cases",
        "summary_status": "not_requested",
        "note": "逐题分析覆盖所选范围；失败与未完成题目单独记录。引用校验仅确认来源，结论仍需复核。",
    }

    def persist() -> None:
        result["coverage"] = coverage(answers, len(cases))
        result["covered"] = result["coverage"]["reference_valid"]
        save(target / "diagnosis.json", result)
        progress(processed=len(answers), stage=result["stage"])

    persist()

    def request(
        client: httpx.Client,
        prompt: str,
        payload: Any,
        stage: str,
        index: int | None = None,
        image_parts: list[dict[str, Any]] | None = None,
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
                "gt_image_count": sum(p["type"] == "image_url" for p in image_parts or []),
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
                            {
                                "role": "user",
                                "content": (
                                    [
                                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                                        *image_parts,
                                    ]
                                    if image_parts
                                    else json.dumps(payload, ensure_ascii=False)
                                ),
                            },
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
        remaining = set(needed)
        for index, source in enumerate(read(parent / "input.jsonl") if needed else ()):
            if index not in needed:
                continue
            check()
            case = by_index[index]
            package = evidence_package(case, source)
            remaining.remove(index)
            gt = gt_inputs[index]
            package["gt_images"] = gt["gt_images"]
            package["gt_missing_evidence"] = gt["missing_evidence"]
            for image in gt["gt_images"]:
                package["records"].append(
                    {
                        "id": image["id"],
                        "path": f"gt/input-{index}.json",
                        "excerpt": json.dumps(image, ensure_ascii=False),
                        "truncated": False,
                    }
                )
            save(target / f"evidence-{index}.json", package)
            raw: str | None
            error: str | None
            image_parts: list[dict[str, Any]] = []
            try:
                for image in gt["gt_images"]:
                    if image["status"] != "attached":
                        raise ValueError("GT image unavailable")
                    data = (target / "gt" / image["file"]).read_bytes()
                    if hashlib.sha256(data).hexdigest() != image["sha256"]:
                        raise ValueError("GT image checksum mismatch")
                    image_parts.extend(
                        [
                            {"type": "text", "text": image["id"]},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{image['mime']};base64," + base64.b64encode(data).decode()
                                },
                            },
                        ]
                    )
            except (OSError, ValueError):
                raw, error = None, "gt_images_unavailable_or_changed"
            else:
                raw, error = request(client, CASE_PROMPT, package, "cases", index, image_parts)
            value, invalid = (
                validate_case_diagnosis(raw, {r["id"] for r in package["records"]})
                if raw is not None
                else ({}, None)
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
                    "gt_images": gt["gt_images"],
                    "gt_missing_evidence": gt["missing_evidence"],
                }
            )
            persist()
            if not remaining:
                break
        if remaining:
            raise ValueError(f"Frozen evidence missing for selected indices: {sorted(remaining)}")
        if not any(answer.get("valid") for answer in answers):
            result["stage"] = "completed"
            if options.get("mode") == "overview":
                result.update(summary_status="failed", summary_error="没有有效逐题诊断，未生成汇总")
            persist()
            raise ValueError("所有题目分析失败或无效；请查看逐题失败原因")
        if options.get("mode") == "overview":
            check()
            result.update(stage="overview", summary_status="running")
            persist()
            # Include every diagnosis with its authoritative contract and outcome.
            # Split requests by input size; no selected case is sampled out.
            base = {
                "scope_count": len(cases),
                "facts": result["facts"],
                "coverage": result["coverage"],
            }
            entries = [{"groups": [g]} for g in result["issues"]]
            for answer in answers:
                case = by_index[answer["index"]]
                entry = {
                    "task_contracts": [task_contract(case)],
                    "terminal_outcomes": [terminal_facts(case)],
                    "diagnoses": [],
                }
                if answer["valid"]:
                    entry["diagnoses"].append(
                        {
                            "id": f"i{answer['index']}:diagnosis",
                            "index": answer["index"],
                            "result": {
                                "claims": [
                                    {k: c[k] for k in ("kind", "text", "limitations")}
                                    for c in answer["result"]["claims"]
                                ],
                                "missing_evidence": answer["result"]["missing_evidence"],
                                "diagnosis": answer["result"].get("diagnosis"),
                            },
                        }
                    )
                entries.append(entry)

            def empty_payload() -> dict[str, Any]:
                return {**base, "groups": [], "task_contracts": [], "terminal_outcomes": [], "diagnoses": []}

            payloads = []
            payload = empty_payload()
            for entry in entries:
                merged = {**payload, **{key: payload[key] + rows for key, rows in entry.items()}}
                if len(json.dumps(merged, ensure_ascii=False)) > 30000:
                    if payload != empty_payload():
                        payloads.append(payload)
                    payload = empty_payload()
                    merged = {**payload, **entry}
                    if len(json.dumps(merged, ensure_ascii=False)) > 30000:
                        raise ValueError("One summary evidence unit exceeds the input budget")
                payload = merged
            if payload != empty_payload() or not payloads:
                payloads.append(payload)

            result["summary_parts"] = []
            level = 0
            summary_error = None
            while payloads:
                summaries = []
                for part, payload in enumerate(payloads):
                    allowed = {"scope:statistics"}
                    for key, id_key in (
                        ("groups", "group_id"),
                        ("task_contracts", "id"),
                        ("terminal_outcomes", "id"),
                        ("diagnoses", "id"),
                    ):
                        allowed.update(row[id_key] for row in payload.get(key, []))
                    for summary in payload.get("summaries", []):
                        allowed.update(ref for claim in summary["claims"] for ref in claim["evidence_refs"])
                    payload.update(
                        allowed_evidence_refs=sorted(allowed),
                        omitted_groups=0,
                        omitted_diagnoses=0,
                        omitted_task_contracts=0,
                        omitted_terminal_outcomes=0,
                    )
                    name = f"summary-{level}-{part}.json"
                    raw, error = request(client, OVERVIEW_PROMPT, payload, "overview")
                    value, invalid = validate_claims(raw, allowed) if raw is not None else ({}, None)
                    save(
                        target / name,
                        {"input": payload, "raw": raw, "result": value, "error": error or invalid},
                    )
                    result["summary_parts"].append(name)
                    result.update(summary_input=payload, summary_raw=(raw or "")[:20000])
                    summary_error = error or invalid
                    persist()
                    if summary_error:
                        break
                    summaries.append(value)
                if summary_error or len(summaries) == 1:
                    result.update(
                        overview=summaries[0] if not summary_error else None,
                        summary_status="failed" if summary_error else "completed",
                        summary_error=summary_error,
                        stage="completed",
                    )
                    break
                # Every valid part participates in the next level; retain source citations.
                payloads = [{**base, "summaries": summaries[i : i + 2]} for i in range(0, len(summaries), 2)]
                level += 1
        else:
            result["stage"] = "completed"
        persist()
