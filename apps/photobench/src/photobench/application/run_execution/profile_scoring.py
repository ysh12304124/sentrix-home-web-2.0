"""Shared scoring execution for new answers, regrading and crash recovery."""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Mapping
from typing import Any

from photobench.application.run_execution.judge import JudgeRunner
from photobench.application.run_execution.typed_scoring import media_metrics_for_turn, score_typed
from photobench.domain.evaluation.gt_context import (
    LEGACY_INSTRUCTIONS,
    capture_context,
    pair_image_context,
)
from photobench.domain.evaluation.media import delivered_media_aliases, saved_media_metrics
from photobench.domain.evaluation.profiles import (
    ATM,
    OPEN,
    answer_fingerprint,
    diagnostic_key,
    digest,
    evaluation_turn,
    list_score,
    make_batch,
    result_key,
    summarize_batches,
    uses_readable_metadata,
    valid_result,
)
from photobench.domain.runs.analysis import now_iso
from photobench.domain.runs.execution import RunCancelledError, resolve_gt_media
from photobench.domain.runs.judge import JUDGE_PROMPT_KINDS, parse_judge_json, thinking_kwargs
from photobench.domain.runs.judge_recovery import gt_image_refs


def compact_verdict(result: Mapping[str, Any]) -> dict[str, Any]:
    # Requests are the authoritative audit trail; do not duplicate image payloads.
    return {
        k: v for k, v in result.items() if k not in {"input", "attempts", "raw_response", "call_observations"}
    }


def prepare_batches(
    state: dict[str, Any],
    configs: Mapping[str, Any],
    judge: Mapping[str, Any],
    scope: str = "missing",
    nonce: str = "",
    *,
    metadata_snapshot: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    batches = state.setdefault("scoring_batches", [])
    state.setdefault("scoring_results", {})
    selected = []
    frozen_judge = copy.deepcopy(dict(judge))
    diagnostics = frozen_judge.setdefault("diagnostics", {})
    for purpose in ("task_decision", "evidence"):
        diagnostics[purpose] = diagnostics.get(purpose) or JUDGE_PROMPT_KINDS[purpose]
    diagnostics["evidence_enabled"] = bool(diagnostics.get("evidence_enabled"))
    for profile, config in configs.items():
        candidate = make_batch(
            profile,
            config,
            frozen_judge,
            state["qa_snapshot"],
            scope=scope,
            created_at=now_iso(),
            nonce=nonce,
            metadata_snapshot=metadata_snapshot
            if metadata_snapshot is not None
            else state.get("gt_metadata_snapshot"),
        )
        batch = next((b for b in batches if b["batch_id"] == candidate["batch_id"]), None)
        if batch is None:
            batch = candidate
            prior = next((b for b in reversed(batches) if b["profile_id"] == profile), None)
            if prior and scope in {"failed", "needs_review"}:
                batch["selection_source_batch_id"] = prior["batch_id"]
            batches.append(batch)
        selected.append(batch)
    return selected


class ProfileScoring:
    """Persist each request boundary and completed item under the caller's state lock."""

    def __init__(
        self,
        runner: JudgeRunner,
        state: dict[str, Any],
        lock: Any,
        checkpoint: Callable[[], None],
        assets: Mapping[str, list[dict[str, Any]]],
    ) -> None:
        self.runner, self.state, self.lock, self.checkpoint, self.assets = (
            runner,
            state,
            lock,
            checkpoint,
            assets,
        )
        self.rows = {str(r["qa_id"]): r for r in state["qa_snapshot"]["rows"]}
        self.universe = [
            ref
            for row in self.rows.values()
            for ref in [
                *(row.get("retrieval_media_refs") or []),
                *((row.get("evaluation") or {}).get("target_media_refs") or []),
                *(row.get("answer_evidence_media_refs") or []),
            ]
        ]
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_keys: dict[str, Any] = {}
        self.runner._rate_lock = self.runner._rate_lock or threading.Lock()

    def _legacy_metadata(self, refs: Any, source: str) -> tuple[dict[str, Any], dict[str, Any]]:
        resolved = resolve_gt_media(refs, dict(self.assets), [])
        metadata = []
        by_id = {str(a.get("id")): a for group in self.assets.values() for a in group}
        for ref in resolved:
            asset = by_id.get(str(ref.get("asset_id")))
            if ref.get("mapping_status") != "ok" or asset is None:
                raise ValueError("GT metadata unavailable: " + str(ref.get("media_id")))
            fields = {
                k: asset[k]
                for k in ("captured_at", "gps", "source_timestamp_sec")
                if asset.get(k) is not None
            }
            if not fields:
                raise ValueError("GT metadata not recorded: " + str(ref.get("media_id")))
            metadata.append({"reference": ref, "metadata": fields, "source": "product.assets"})
        return (
            {"source": source, "materials": metadata, "sha256": digest(metadata)},
            {"type": "text", "text": "GT元数据：" + json.dumps(metadata, ensure_ascii=False)},
        )

    def save(self, key: str, result: dict[str, Any]) -> None:
        with self.lock:
            self.state.setdefault("scoring_results", {})[key] = copy.deepcopy(result)
            self.state["scoring_summaries"] = summarize_batches(self.state)
        self.checkpoint()

    def diagnostics(
        self, batch: Mapping[str, Any], target: Mapping[str, Any], item: Mapping[str, Any]
    ) -> str:
        config = batch.get("diagnostics") or {}
        key = diagnostic_key(batch, target)
        with self._diagnostic_lock:
            mutex = self._diagnostic_keys.setdefault(key, threading.Lock())
        with mutex:
            with self.lock:
                prior = copy.deepcopy(self.state.setdefault("diagnostic_results", {}).get(key) or {})
            if prior.get("status") == "completed":
                return key
            row = self.rows[str(target["qa_id"])]
            index = target["turn_index"]
            turns = item.get("runtime_turns") or [item]
            record = turns[index]
            definition = (row.get("conversation") or [row])[index]
            result: dict[str, Any] = {
                "key": key,
                "qa_id": target["qa_id"],
                "turn_index": index,
                "answer_fingerprint": target["answer_fingerprint"],
                "config": config,
                "judge": batch["judge"],
                "status": "running",
                "requests": [],
                "history": [
                    *(prior.get("history") or []),
                    *([{k: v for k, v in prior.items() if k != "history"}] if prior else []),
                ],
            }

            def save() -> None:
                with self.lock:
                    self.state["diagnostic_results"][key] = copy.deepcopy(result)
                self.checkpoint()

            def observe(event: dict[str, Any]) -> None:
                event = {
                    **event,
                    "standard": "shared_diagnostics",
                    "qa_id": target["qa_id"],
                    "turn_index": index,
                    "diagnostic_key": key,
                }
                existing = next((r for r in result["requests"] if r["call_id"] == event["call_id"]), None)
                if existing is None:
                    result["requests"].append(copy.deepcopy(event))
                else:
                    existing.update(copy.deepcopy(event))
                save()

            runner = copy.copy(self.runner)
            runner.task_judge_prompt = config.get("task_decision") or runner.task_judge_prompt
            runner.evidence_judge_prompt = config.get("evidence") or runner.evidence_judge_prompt
            runner.request_observer = observe
            runner.request_transform = None
            # The request observer commits the running state with its exact input
            # before HTTP starts; the final save also covers pre-request failures.
            try:
                if runner.cancel and runner.cancel.is_set():
                    raise RunCancelledError("scoring cancelled")
                result["task_judge"] = runner.judge_task_action(
                    str(record.get("message") or row.get("question") or ""),
                    str(record.get("answer") or ""),
                    definition.get(
                        "expected_action", record.get("expected_action", row.get("expected_action"))
                    ),
                    record.get("agent_status"),
                    str(record.get("termination_reason") or ""),
                    task_type=definition.get("task_type", row.get("task_type")),
                    question_type=definition.get("question_type", row.get("question_type")),
                    answerability=definition.get("answerability", row.get("answerability")),
                    reference=definition.get("reference_answer") or row.get("answer"),
                    conversation=turns[: index + 1],
                )
                if (
                    config.get("evidence_enabled")
                    and record.get("predicted_images")
                    and (result["task_judge"] or {}).get("actual_action") != "clarify"
                ):
                    result["evidence_judge"] = runner.judge_evidence(
                        str(record.get("message") or ""),
                        str(record.get("answer") or ""),
                        record["predicted_images"],
                        self.assets,
                        conversation=turns[: index + 1],
                    )
                result["status"] = (
                    "failed"
                    if any(
                        result.get(field, {}).get("judge_status") == "failed"
                        for field in ("task_judge", "evidence_judge")
                    )
                    else "completed"
                )
            except RunCancelledError:
                result.update(status="interrupted", reason="scoring cancelled")
            except Exception as exc:
                result.update(status="failed", reason=str(exc))
            for field in ("task_judge", "evidence_judge"):
                if field in result:
                    result[field] = compact_verdict(result[field])
            result["finished_at"] = now_iso()
            save()
            return key

    def score(
        self, batch: Mapping[str, Any], target: Mapping[str, Any], item: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        key = str(target["key"])
        with self.lock:
            previous = copy.deepcopy(self.state["scoring_results"].get(key) or {})
        item = item if item is not None else self.state["items"][target["item_index"]]
        if previous and previous.get("answer_fingerprint") != target["answer_fingerprint"]:
            raise ValueError("Saved answer changed; create a new scoring batch")
        if valid_result(previous):
            previous["diagnostic_key"] = self.diagnostics(batch, target, item)
            self.save(key, previous)
            return previous
        row = self.rows[str(target["qa_id"])]
        index = int(target["turn_index"])
        turns = item.get("runtime_turns") or [item]
        record = turns[index]
        definitions = row.get("conversation") or []
        turn = definitions[index] if definitions else row
        profile, config = batch["profile_id"], batch["config"]
        kind = (
            (row.get("evaluation") or {}).get("scorer", "open_judge")
            if index == evaluation_turn(row)
            else "open_judge"
        )
        purpose = (
            "answer_quality"
            if profile == OPEN
            else {
                "choice_judge": "choice",
                "atm_number": "number",
                "media_set": "list",
                "atm_list": "list",
            }.get(kind, "open")
        )
        result: dict[str, Any] = {
            **dict(target),
            "batch_id": batch["batch_id"],
            "profile_id": profile,
            "rule_version": batch["rule_version"],
            "purpose": purpose,
            "status": "running",
            "score": None,
            "max_score": 2 if profile == OPEN else 1,
            "started_at": now_iso(),
            "requests": [],
            "history": [
                *(previous.get("history") or []),
                *([{k: v for k, v in previous.items() if k != "history"}] if previous else []),
            ],
        }
        result["media_metrics"] = (
            media_metrics_for_turn(row.get("evaluation") or {}, row, record, turns[: index + 1], self.assets)
            if self.assets
            else saved_media_metrics(row, item, index)
        )
        result["diagnostic_key"] = self.diagnostics(batch, target, item)
        runner = copy.copy(self.runner)
        runner.request_transform = None
        runner.judge_use_gt_images = bool(config["use_gt_images"])
        runner.answer_quality_prompt = config["prompts"].get("answer_quality", runner.answer_quality_prompt)
        runner.answer_quality_input_version = config.get("quality_input_version", "legacy_v1")

        def observe(event: dict[str, Any]) -> None:
            event = {
                **event,
                "profile_id": profile,
                "purpose": purpose,
                "batch_id": batch["batch_id"],
                "qa_id": target["qa_id"],
                "turn_index": index,
                "retry_reason": event.get("retry_reason") or (previous.get("reason") if previous else None),
            }
            existing = next((r for r in result["requests"] if r["call_id"] == event["call_id"]), None)
            if existing is None:
                result["requests"].append(copy.deepcopy(event))
            else:
                existing.update(copy.deepcopy(event))
            self.save(key, result)

        runner.request_observer = observe
        question = str(record.get("message") or row.get("question") or "")
        answer = str(record.get("answer") or "")
        expected = turn.get(
            "expected_action", record.get("expected_action", row.get("expected_action", "answer"))
        )
        reference = str(
            turn.get("reference_answer") or row.get("answer") or item.get("reference_answer") or ""
        )
        if expected == "clarify" and not turn.get("reference_answer"):
            reference = "应要求用户提供至少一个具体、可用于后续检索的锚点；询问形式不限。"
        try:
            if runner.cancel and runner.cancel.is_set():
                raise RunCancelledError("scoring cancelled")
            material_key = str(target["qa_id"]) + ":" + str(index)
            materials = batch["materials"]
            cached = materials.get(material_key)
            if cached is None:
                cached = {"attachments": [], "images": None, "metadata": None}
                if purpose not in {"choice", "list"}:
                    if config["use_gt_metadata"]:
                        refs, source = gt_image_refs(row, turn)
                        if uses_readable_metadata(config):
                            if config.get("gt_metadata_mode") == "image_metadata_v1":
                                refs = [ref for ref in refs if ref["media_type"] == "image"]
                            cached["metadata"], attachment = capture_context(
                                refs,
                                batch["gt_metadata_snapshot"],
                                source,
                                instructions=batch.get("gt_metadata_instructions", LEGACY_INSTRUCTIONS),
                            )
                            if config.get("gt_metadata_mode") == "image_metadata_v1":
                                cached["metadata"]["mode"] = "image_metadata_v1"
                            elif refs:
                                cached["attachments"].append(attachment)
                        else:
                            cached["metadata"], attachment = self._legacy_metadata(refs, source)
                            cached["attachments"].append(attachment)
                    if config["use_gt_images"]:
                        images, audit = runner._gt_images(row, turn, self.assets)
                        result["gt_images"] = audit
                        if audit["status"] in {"mapping_error", "attachment_error"}:
                            raise ValueError(
                                "GT image attachment failed: " + json.dumps(audit, ensure_ascii=False)
                            )
                        if config.get("gt_metadata_mode") == "image_metadata_v1" and images:
                            images = pair_image_context(
                                images, audit, cached["metadata"], batch["gt_metadata_instructions"]
                            )
                        cached["images"] = [images, audit]
                with self.lock:
                    materials[material_key] = copy.deepcopy(cached)
                # Commit materials at the first request boundary or the final no-Judge result.
            attachments: list[dict[str, Any]] = copy.deepcopy(cached["attachments"])
            if cached["images"] is not None:
                runner.gt_image_materials = (cached["images"][0], cached["images"][1])
                result["gt_images"] = cached["images"][1]
                if profile == ATM:
                    attachments.extend(cached["images"][0])
            result["gt_metadata"] = cached["metadata"]
            if attachments:

                def transform(payload: dict[str, Any]) -> dict[str, Any]:
                    payload = copy.deepcopy(payload)
                    user = next(m for m in payload["messages"] if m["role"] == "user")
                    content = user["content"]
                    user["content"] = (
                        [{"type": "text", "text": content}] if isinstance(content, str) else content
                    ) + attachments
                    return payload

                runner.request_transform = transform
            if profile == OPEN:
                output = runner._legacy_judge(
                    question,
                    reference,
                    answer,
                    conversation=turns[: index + 1],
                    expected_action=expected,
                    task_type=turn.get("task_type", row.get("task_type")),
                    question_type=turn.get("question_type", row.get("question_type")),
                    answerability=turn.get("answerability", row.get("answerability")),
                    gt_row=row,
                    gt_turn=turn,
                    assets_by_name=self.assets,
                    media_aliases=delivered_media_aliases(record),
                )
            elif purpose == "list":
                output = {
                    **list_score(row["evaluation"]["target_media_refs"], record, self.universe),
                    "reason": "Jaccard",
                    "call_observations": [],
                }
            elif purpose == "choice":
                output = score_typed(
                    definition=row["evaluation"],
                    row=row,
                    record=record,
                    turns=turns[: index + 1],
                    assets=self.assets,
                    request=runner._request,
                    model=runner.judge_model,
                    url=runner.judge_url,
                    options={
                        "open_scale": "binary",
                        "choice_semantic_aux": False,
                        "number_semantic_aux": False,
                    },
                    prompts={"choice": config["prompts"]["choice"]},
                )
            else:
                prompt = config["prompts"][purpose]
                if purpose == "open":
                    prompt = (
                        prompt.replace("{{question}}", question)
                        .replace("{{answer}}", reference)
                        .replace("{{prediction}}", answer)
                    )
                    evaluation = row.get("evaluation") or {}
                    if (
                        evaluation.get("scorer") == "open_judge"
                        and evaluation.get("applies_to_turn", 0) == index
                        and evaluation.get("answer_quality_rubric")
                    ):
                        prompt += (
                            "\n\n本题专用评分说明（评测端提供）：\n"
                            + evaluation["answer_quality_rubric"]
                            + "\n本次采用二元评分：上述量表2分对应accuracy=true，0分或1分对应accuracy=false。"
                        )
                payload = {
                    "model": runner.judge_model,
                    "temperature": 0,
                    "max_tokens": 2048,
                    **thinking_kwargs(runner.judge_url),
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "question": question,
                                    "answer": answer,
                                    "reference": reference,
                                    "expected_action": expected,
                                    "conversation": [
                                        {"question": t.get("message"), "answer": t.get("answer")}
                                        for t in turns[: index + 1]
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                }
                observations: list[dict[str, Any]] = []
                raw, _ = runner._request(payload, observations=observations)
                parsed = parse_judge_json(
                    str((raw.get("choices") or [{}])[0].get("message", {}).get("content") or "")
                )
                value = parsed.get("accuracy") if purpose == "open" else parsed.get("score")
                if purpose == "open":
                    if type(value) is not bool and value not in ("true", "false"):
                        raise ValueError("ATM open Judge must return boolean accuracy")
                    score = int(str(value).lower() == "true")
                else:
                    if type(value) is not int or value not in {0, 1}:
                        raise ValueError("Numeric Judge must return integer 0 or 1")
                    score = value
                output = {
                    "score": score,
                    "reason": parsed.get("reason", parsed.get("explanation", "")),
                    "call_observations": observations,
                }
            result.update(compact_verdict(output))
            result["max_score"] = 2 if profile == OPEN else 1
            result["status"] = "completed"
            result["status"] = (
                "completed"
                if valid_result(result) and output.get("judge_status", "completed") == "completed"
                else "needs_review"
                if output.get("judge_status") == "needs_review"
                else "failed"
            )
            result["normalized_score"] = (
                result["score"] / result["max_score"] if result["status"] == "completed" else None
            )
        except RunCancelledError:
            result.update(status="interrupted", reason="scoring cancelled")
        except Exception as exc:
            result.update(status="failed", reason=str(exc), score=None)
        result["finished_at"] = now_iso()
        self.save(key, result)
        return result

    def score_item(
        self, batches: list[dict[str, Any]], item: dict[str, Any], row: Mapping[str, Any]
    ) -> dict[str, Any]:

        all_results = []
        for batch in batches:
            for index, record in enumerate(item.get("runtime_turns") or [item]):
                target = {
                    "key": result_key(batch["batch_id"], str(row["qa_id"]), index),
                    "qa_id": str(row["qa_id"]),
                    "turn_index": index,
                    "answer_fingerprint": answer_fingerprint(record),
                    "is_evaluation_turn": index == evaluation_turn(row),
                }
                with self.lock:
                    if target["key"] not in batch["targets"]:
                        batch["targets"].append(target["key"])
                all_results.append(self.score(batch, target, item))
        item["judge_status"] = "completed" if all(valid_result(r) for r in all_results) else "failed"
        return item
