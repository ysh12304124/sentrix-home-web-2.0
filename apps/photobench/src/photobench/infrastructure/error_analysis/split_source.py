"""Pinned format-3 analysis facts and bounded, on-demand original excerpts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from photobench.domain.error_analysis.rules import TERMINAL
from photobench.domain.evaluation.profile_view import select_batch
from photobench.infrastructure.results.split_json import StoredDict, is_reference
from photobench.infrastructure.results.split_store import IO_FIELDS, SplitRunReader, plain


def facts(value: Any, *, excluded: frozenset[str] = frozenset(IO_FIELDS)) -> Any:
    """Skip IO descriptors before dereferencing; bound long diagnostic strings."""
    if isinstance(value, dict):
        result = {}
        for key in value:
            if key in excluded:
                continue
            raw = value.reader.descriptor(value.ref, key) if isinstance(value, StoredDict) else value[key]
            if isinstance(value, StoredDict) and is_reference(raw) and raw["$photobench_object_v3"][0] == "s":
                chunks = []
                length = 0
                for ref in value.reader.metadata(raw):
                    chunk = value.reader.raw(ref).decode("utf-8")[: 8000 - length]
                    chunks.append(chunk)
                    length += len(chunk)
                    if length >= 8000:
                        break
                result[key] = "".join(chunks)
            else:
                child = value.reader.value(raw) if isinstance(value, StoredDict) else raw
                result[key] = facts(child, excluded=excluded)
        return result
    if isinstance(value, list):
        return [facts(child, excluded=excluded) for child in value]
    return value[:8000] if isinstance(value, str) else value


def trace_facts(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = facts(record)
    if isinstance(record.get("result"), dict):
        tool_result = record["result"]
        result["result"] = {
            key: facts(tool_result[key])
            for key in ("failure_reason", "degraded", "degraded_reason", "model_call_observations")
            if key in tool_result
        }
    return result


def add_traces(target: dict[str, Any], original: dict[str, Any]) -> None:
    for key in ("model_call_metrics", "tool_trace", "execution_trace", "trace"):
        if key in original:
            target[key] = [trace_facts(row) for row in original[key] or [] if isinstance(row, dict)]
    if "trace_bundle" in original:
        bundle = original["trace_bundle"] or {}
        target["trace_bundle"] = {
            key: facts(bundle[key])
            for key in ("budget", "model_calls", "debug_calls", "guard")
            if key in bundle
        }


def restore_rule_text(target: dict[str, Any], original: dict[str, Any], *, legacy_judge: bool) -> None:
    # Core previews truncate long answers and references. Rules need the saved
    # text for GT identity and their own explicit 8000-character display limit.
    for key in ("question", "message", "reference_answer", "answer", "final_answer", "error"):
        if key in original:
            target[key] = plain(original[key])
    if not legacy_judge:
        return
    for key in ("judge", "task_judge", "evidence_judge"):
        judge = original.get(key) or {}
        payload = (judge.get("attempts") or [{}])[0].get("input") or judge.get("input") or {}
        if payload:
            target.setdefault(key, {})["input"] = {
                **{
                    field: payload[field]
                    for field in ("model", "temperature", "max_tokens")
                    if field in payload
                },
                "messages": [
                    {"role": "system", "content": message.get("content")}
                    for message in payload.get("messages") or []
                    if message.get("role") == "system"
                ],
            }


def split_input(
    root: Path, run_id: str, profile: str, batch_id: str, pinned: Path | None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    view = SplitRunReader(root, root / run_id)
    try:
        original = view.reader.value(view.manifest["root"])
        if not profile and original.get("scoring_batches"):
            profile = original["scoring_batches"][0]["profile_id"]
        state = view.overview(profile, batch_id)
        state["qa_snapshot"] = plain(original.get("qa_snapshot") or {})
        selected = select_batch(original, profile, batch_id) if profile else None
        selection = {
            "scoring_profile": profile,
            "scoring_batch": selected["batch_id"] if selected else None,
            "scoring_definition": plain(
                {
                    key: selected[key]
                    for key in (
                        "profile_id",
                        "rule_version",
                        "config",
                        "judge",
                        "diagnostics",
                        "qa_definition_sha256",
                    )
                    if key in selected
                }
            )
            if selected
            else None,
            "scoring_summary": (state.get("summary") or {}).get("selected_scoring"),
            "input_projection": "rule_facts_v1; original IO read separately in bounded excerpts",
            "storage_format": 3,
        }
        if selected:
            state["judge_url"], state["judge_model"] = selected["judge"]["url"], selected["judge"]["model"]
        if pinned is not None:
            if state.get("status") not in TERMINAL:
                raise ValueError("run is not terminal")
            with pinned.open("w", encoding="utf-8") as out:
                for index in range(len(view.variant(profile, batch_id)["cores"])):
                    item = view.core(index, profile, batch_id)
                    raw = view.item_record(index)
                    restore_rule_text(item, raw, legacy_judge=not profile)
                    add_traces(item, raw)
                    for turn, saved in zip(
                        item.get("runtime_turns") or [], raw.get("runtime_turns") or [], strict=True
                    ):
                        restore_rule_text(turn, saved, legacy_judge=not profile)
                        add_traces(turn, saved)
                    item["run_id"], item["index"] = run_id, index
                    item["trace_storage"].update(revision=view.revision)
                    item["analysis_projection"] = {
                        "version": 1,
                        "kind": "rule_facts",
                        "text_limit": 8000,
                        "note": "调用计数与规则字段完整；输入输出正文按节点另读，长诊断文本最多8000字。",
                    }
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
        return state, selection, view.revision
    finally:
        view.reader.close()


def with_excerpts(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Enrich one frozen facts record; cap all raw excerpts together at 32K chars."""
    if not item.get("analysis_projection"):
        return item
    # Clone only the already bounded facts, never the saved original item.
    result: dict[str, Any] = json.loads(json.dumps(item, ensure_ascii=False))
    view = SplitRunReader(root, root / item["run_id"], item["trace_storage"]["revision"])
    try:
        records: list[tuple[list[str | int], dict[str, Any]]] = []

        def collect(value: dict[str, Any], prefix: list[str | int]) -> None:
            for key in ("tool_trace", "model_call_metrics", "execution_trace", "trace"):
                records.extend(([*prefix, key, i], row) for i, row in enumerate(value.get(key) or []))
            for key in ("debug_calls", "model_calls"):
                records.extend(
                    ([*prefix, "trace_bundle", key, i], row)
                    for i, row in enumerate((value.get("trace_bundle") or {}).get(key) or [])
                )

        collect(result, [])
        for i, turn in enumerate(result.get("runtime_turns") or []):
            collect(turn, ["runtime_turns", i])
        records.sort(key=lambda pair: pair[1].get("status") not in {"error", "failed", "partial", "denied"})
        budget = 32000
        included = 0
        for path, row in records:
            if not budget:
                break
            excerpt = view.item_content(item["index"], path, 0, min(3000, budget))
            row["saved_record_excerpt"] = excerpt
            budget -= len(excerpt["text"])
            included += 1
        result["analysis_projection"].update(
            excerpt_records=included,
            total_records=len(records),
            note="规则字段及有界原文片段；片段next_offset非空表示正文未完。完整原文按固定revision懒加载。",
        )
        return result
    finally:
        view.reader.close()
