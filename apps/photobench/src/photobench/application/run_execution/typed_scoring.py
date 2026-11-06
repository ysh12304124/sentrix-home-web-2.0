"""Per-format evaluation on the existing run's Judge transport and saved traces."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from photobench.domain.evaluation.atm.scoring import deterministic_accuracy, list_jaccard_score
from photobench.domain.evaluation.contract import choice_result, result_record
from photobench.domain.evaluation.media import media_metrics_for_sets
from photobench.domain.evaluation.prompts import evaluation_prompts
from photobench.domain.runs.execution import RunCancelledError, resolve_predicted_media
from photobench.domain.runs.judge import parse_judge_json, thinking_kwargs

Request = Callable[..., tuple[dict[str, Any], int]]


def media_metrics_for_turn(
    definition: Mapping[str, Any],
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    turns: Sequence[Mapping[str, Any]],
    assets: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Answer targets and retrieval evidence have separate denominators."""
    ids = list(
        dict.fromkeys(
            str(identifier)
            for turn in turns
            for identifier in (turn.get("retrieval_observations") or {}).get("asset_ids", [])
        )
    )
    retrieved = resolve_predicted_media(ids, dict(assets))
    known_ids = {str(asset.get("id")) for group in assets.values() for asset in group}
    available = (
        bool(turns)
        and all((turn.get("retrieval_observations") or {}).get("complete") is True for turn in turns)
        and all(identifier in known_ids for identifier in ids)
    )
    return media_metrics_for_sets(definition, row, record, retrieved, retrieval_available=available)


def score_typed(
    *,
    definition: Mapping[str, Any],
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    turns: Sequence[Mapping[str, Any]],
    assets: Mapping[str, list[dict[str, Any]]],
    request: Request,
    model: str,
    url: str,
    options: Mapping[str, Any],
    prompts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    frozen_prompts = dict(prompts) if prompts is not None else evaluation_prompts()
    calls: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    question, answer = (
        str(record.get("message") or row.get("question") or ""),
        str(record.get("answer") or ""),
    )
    reference = str(record.get("reference_answer") or row.get("answer") or "")
    context = [{"question": t.get("message"), "answer": t.get("answer")} for t in turns]
    effective = dict(definition)
    if effective["scorer"] == "open_judge" and options["open_scale"] == "binary":
        effective["max_score"] = 1

    def ask(
        prompt: str, data: Mapping[str, Any], *, correction: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 2048,
            **thinking_kwargs(url),
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
        }
        if correction is not None:
            payload["messages"].extend(
                [
                    {"role": "assistant", "content": correction["response"]},
                    {
                        "role": "user",
                        "content": "上次返回未通过格式与一致性校验："
                        + str(correction["error"])
                        + "。请重新比较并输出完整JSON；回答未说明区分选项所需的事实时归other。"
                        "仅选项自身语义重叠才用ambiguous_options。不得猜测正确选项。",
                    },
                ]
            )
        attempt: dict[str, Any] = {"input": payload}
        attempts.append(attempt)
        try:
            raw, count = request(payload, observations=calls)
        except Exception as exc:
            attempt["error"] = str(exc)
            raise
        text = str((raw.get("choices") or [{}])[0].get("message", {}).get("content") or "")
        attempt.update(raw_response=raw, raw_text=text, request_attempts=count)
        parsed = parse_judge_json(text)
        if not parsed:
            raise ValueError("Invalid evaluator JSON")
        return parsed

    def binary(parsed: Mapping[str, Any]) -> int:
        score = parsed.get("score")
        if type(score) is not int or score not in {0, 1}:
            raise ValueError("Binary Judge must return integer 0 or 1")
        return score

    try:
        scorer = effective["scorer"]
        if scorer == "choice_judge":
            data = {
                "question": question,
                "answer": answer,
                "conversation": context,
                "options": effective["options"],
            }
            correction = None
            for index in range(2):
                try:
                    parsed = ask(frozen_prompts["choice"], data, correction=correction)
                    result = choice_result(effective, parsed)
                    break
                except ValueError as exc:
                    if "raw_text" not in attempts[-1]:
                        raise
                    attempts[-1]["validation_error"] = str(exc)
                    if index == 1:
                        raise
                    correction = {"response": attempts[-1]["raw_text"], "error": str(exc)}
            if options["choice_semantic_aux"]:
                try:
                    auxiliary = ask(
                        frozen_prompts["binary"],
                        {
                            "question": question,
                            "answer": answer,
                            "reference": reference,
                            "expected_action": record.get("expected_action"),
                        },
                    )
                    result["semantic_aux"] = {
                        "score": binary(auxiliary),
                        "reason": auxiliary.get("reason"),
                        "max_score": 1,
                        "judge_status": "completed",
                    }
                except RunCancelledError:
                    raise
                except Exception as exc:
                    result["semantic_aux"] = {"score": None, "judge_status": "failed", "reason": str(exc)}
        elif scorer in {"media_set", "atm_list"}:
            media_metrics = media_metrics_for_turn(effective, row, record, turns, assets)
            delivered = media_metrics["delivery"]
            prediction = record.get("predicted_media")
            score = delivered["jaccard"]
            serialized = ", ".join(
                PurePosixPath(str(r.get("media_id") or r.get("file_name") or "")).stem
                for r in prediction or []
            )
            if score is not None:
                if scorer == "atm_list":
                    score = list_jaccard_score(str(effective["official_answer"]), serialized)
                elif effective["retrieval_requirement"] == "any":
                    score = int(delivered["matched"] > 0)
            result = result_record(
                effective,
                score,
                reason="media_set" if score is not None else "delivery_unavailable",
                media_metrics=media_metrics,
                official_prediction=serialized if scorer == "atm_list" else None,
            )
        elif scorer == "atm_number":
            score = int(deterministic_accuracy(reference, answer, question))
            result = result_record(effective, score, reason="ATM-Bench ef4e5dff deterministic_accuracy")
            if options["number_semantic_aux"]:
                try:
                    normalized = ask(
                        frozen_prompts["number_normalize"], {"question": question, "answer": answer}
                    )
                    if (
                        not isinstance(normalized.get("normalized_answer"), str)
                        or not normalized["normalized_answer"].strip()
                    ):
                        raise ValueError("Empty numeric normalization")
                    matched = ask(
                        frozen_prompts["number_match"],
                        {
                            "question": question,
                            "normalized_answer": normalized["normalized_answer"],
                            "reference": reference,
                        },
                    )
                    aux = binary(matched)
                    result.update(
                        number_aux={
                            "score": aux,
                            "max_score": 1,
                            "normalization": normalized,
                            "reason": matched.get("reason"),
                            "judge_status": "completed",
                        },
                        number_aux_disagrees=aux != score,
                    )
                except RunCancelledError:
                    raise
                except Exception as exc:
                    result["number_aux"] = {"score": None, "judge_status": "failed", "reason": str(exc)}
        elif scorer == "atm_open":
            prompt = (
                frozen_prompts["atm_open"]
                .replace("{{question}}", question)
                .replace("{{answer}}", reference)
                .replace("{{prediction}}", answer)
            )
            parsed = ask(prompt, {})
            accuracy = parsed.get("accuracy")
            if type(accuracy) is not bool and accuracy not in ("true", "false"):
                raise ValueError("ATM Judge must return true or false accuracy")
            result = result_record(
                effective,
                int(str(accuracy).lower() == "true"),
                reason=str(parsed.get("explanation") or ""),
            )
        else:
            prompt = frozen_prompts["binary"]
            if effective.get("answer_quality_rubric"):
                prompt += (
                    "\n\n本题专用评分说明（评测端提供）：\n"
                    + str(effective["answer_quality_rubric"])
                    + "\n本次采用二元评分：上述量表2分映射为1，0分或1分映射为0；只输出0或1。"
                )
            parsed = ask(
                prompt,
                {
                    "question": question,
                    "answer": answer,
                    "reference": reference,
                    "conversation": context,
                    "expected_action": record.get("expected_action"),
                },
            )
            result = result_record(effective, binary(parsed), reason=str(parsed.get("reason") or ""))
    except RunCancelledError:
        raise
    except Exception as exc:
        result = result_record(effective, None, reason=str(exc))
    if "media_metrics" not in result:
        result["media_metrics"] = media_metrics_for_turn(effective, row, record, turns, assets)
    return {
        **result,
        "call_observations": calls,
        "attempts": attempts,
        "judge_model": model,
        "evaluation_options": dict(options),
    }
