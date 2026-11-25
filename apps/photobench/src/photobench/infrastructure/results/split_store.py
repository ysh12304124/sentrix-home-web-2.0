"""Incremental checkpoints and explicit read models for results format v3."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from photobench.domain.datasets.qa_taxonomy import snapshot_metadata
from photobench.domain.error_analysis.process import process_for_qa
from photobench.domain.errors import ReadIndexRequiredError
from photobench.domain.evaluation.profile_view import (
    choice_matching_result,
    item_profile_results,
    item_scores,
    primary_profile_state,
    profile_state,
)
from photobench.domain.runs import analysis
from photobench.domain.runs.frozen_trace import ITEM_TRACE_FIELDS, TURN_TRACE_FIELDS
from photobench.infrastructure.results.split_json import (
    ObjectReader,
    ObjectWriter,
    database,
    is_reference,
    json_bytes,
    manifest,
    publish,
    thaw,
)

HEAVY = {*ITEM_TRACE_FIELDS, *TURN_TRACE_FIELDS, "debug_calls", "model_calls"}
NODE_PAGE_SIZE = 50
IO_FIELDS = {
    "messages",
    "prompt",
    "request",
    "request_body",
    "response",
    "response_body",
    "raw",
    "raw_full",
    "tools",
    "input",
    "output",
    "debug",
    "content",
    "arguments",
    "args",
    "result",
}
_WRITER_LOCKS = tuple(threading.RLock() for _ in range(64))


class ReadIndexRequired(ReadIndexRequiredError):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__("read_index_required")


def plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def light_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    trace = result.get("agent2_trace")
    if isinstance(trace, dict):
        result["agent2_trace"] = {
            k: v
            for k, v in trace.items()
            if k
            not in {
                "budget_outcomes",
                "planner_decisions",
                "task_declaration",
                "task_state",
                "evidence_ledger",
            }
        }
    return result


def metric_record(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    result = {k: row[k] for k in row if k not in IO_FIELDS}
    # Structured JSON facts and legacy action parsing have defined consumers.
    action = analysis.trace_action(row) if row.get("type") == "model" or row.get("stage") == "model" else None
    if action is not None:
        result["detail"] = action
    observations = (
        (row.get("result") or {}).get("model_call_observations")
        if isinstance(row.get("result"), dict)
        else None
    )
    if observations:
        result["result"] = {"model_call_observations": observations}
    return result


def core_item(item: dict[str, Any]) -> dict[str, Any]:
    result = {k: item[k] for k in item if k not in HEAVY}
    if "runtime_turns" in result:
        result["runtime_turns"] = [
            {k: turn[k] for k in turn if k not in HEAVY} for turn in result["runtime_turns"] or []
        ]
    if item.get("agent2_trace"):
        result["agent2_trace"] = {
            k: v
            for k, v in item["agent2_trace"].items()
            if k
            in {
                "available",
                "budget_outcome",
                "terminal_reason",
                "requirement_status_counts",
                "evidence_coverage",
            }
        }

    # Judge request bodies and historical attempts are available through raw content.
    def scores(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: scores(value[k])
                for k in value
                if k
                not in {
                    "requests",
                    "history",
                    "request",
                    "response",
                    "materials",
                    "input",
                    "output",
                    "attempts",
                    "messages",
                    "prompt",
                    "raw_full",
                    "raw",
                }
            }
        if isinstance(value, list):
            return [scores(v) for v in value]
        return value

    for key in (
        "judge",
        "evaluation_result",
        "task_judge",
        "evidence_judge",
        "scoring_results",
        "diagnostic_results",
    ):
        if key in result:
            result[key] = scores(result[key])
    content_fields = []
    for key in ("answer", "final_answer", "reference_answer", "error"):
        if isinstance(result.get(key), str) and len(result[key]) > 4096:
            content_fields.append({"path": key, "characters": len(result[key])})
            result[key] = result[key][:4096]
    for position, turn in enumerate(result.get("runtime_turns") or []):
        for key in ("answer", "message"):
            if isinstance(turn.get(key), str) and len(turn[key]) > 4096:
                content_fields.append(
                    {"path": f"runtime_turns.{position}.{key}", "characters": len(turn[key])}
                )
                turn[key] = turn[key][:4096]
    if content_fields:
        result["content_fields"] = content_fields
    return result


def answer_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": item.get("answer"),
        "turns": [
            {
                key: turn.get(key)
                for key in (
                    "answer",
                    "attachments",
                    "requirements",
                    "termination_reason",
                    "predicted_media",
                    "retrieval_observations",
                    "trace_bundle",
                    "tool_trace",
                )
            }
            for turn in item.get("runtime_turns") or [item]
        ],
        "tools": item.get("tool_trace"),
        "candidates": item.get("retrieved_candidate_media"),
        "predicted_media": item.get("predicted_media"),
    }


class SplitResultWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._objects: ObjectWriter | None = None
        self._thread: int | None = None
        self._metric_cache: dict[tuple[int, str], tuple[Any, Any]] = {}
        self._nodes_cache: dict[tuple[str, ...], Any] = {}
        self._fingerprints: dict[str, str] = {}
        self._metric_refs: dict[tuple[str, str], Any] = {}
        self._metric_values: OrderedDict[tuple[str, str], tuple[Any, int]] = OrderedDict()
        self._metric_bytes = 0

    def close(self) -> None:
        if self._objects is not None:
            self._objects.close()
            self._objects = None

    def metric_item(self, item: dict[str, Any], objects: ObjectWriter | None = None) -> dict[str, Any]:
        result = dict(item)
        for key in ITEM_TRACE_FIELDS:
            value = item.get(key)
            if value is None:
                continue
            if objects is not None:
                ref = objects.put(value)
                cache_key = (key, json.dumps(ref, sort_keys=True))
                projected = self._metric_refs.get(cache_key)
                if projected is None:
                    projected = objects.derive(
                        "metric-records-v1",
                        [key, ref],
                        lambda value=value: objects.put([metric_record(row) for row in value]),
                    )
                    self._metric_refs[cache_key] = projected
                cached_value = self._metric_values.get(cache_key)
                if cached_value is None:
                    records = plain(self._metric_reader.value(projected))
                    weight = len(json_bytes(records))
                    if weight <= 64 * 1024 * 1024:
                        self._metric_values[cache_key] = (records, weight)
                        self._metric_bytes += weight
                        while self._metric_bytes > 64 * 1024 * 1024:
                            _, (_, removed) = self._metric_values.popitem(last=False)
                            self._metric_bytes -= removed
                else:
                    records = cached_value[0]
                    self._metric_values.move_to_end(cache_key)
                result[key] = records
                continue
            identity = (id(value), key)
            cached = self._metric_cache.get(identity)
            if cached is None:
                cached = value, [metric_record(row) for row in value]
                self._metric_cache[identity] = cached
            result[key] = cached[1]
        result["runtime_turns"] = [
            {k: turn[k] for k in turn if k not in {"trace", "debug_trace", "trace_bundle"}}
            for turn in item.get("runtime_turns") or []
        ]
        return result

    def write(self, run_dir: Path, state: dict[str, Any], *, source: Any = None) -> dict[str, Any]:
        # Separate from the legacy read lock: readers select immutable objects.
        lock = _WRITER_LOCKS[hash(str(database(self.root).resolve())) % len(_WRITER_LOCKS)]
        with lock:
            # Persistence and API maintenance can use different threads.
            if self._objects is None or self._thread != threading.get_ident():
                if self._objects is not None:
                    self._objects.close()
                self._objects = ObjectWriter(self.root)
                self._thread = threading.get_ident()
            objects = self._objects
            objects.known.clear()
            objects.inserted_bytes = objects.inserted_objects = 0
            started = time.monotonic()
            try:
                root = objects.put(state)
                object_reader = ObjectReader(database(self.root), connection=objects.db)
                self._metric_reader = object_reader
                fingerprints = {}
                for item in state.get("items") or []:
                    source_ref = objects.put(answer_source(item))
                    key = json.dumps(source_ref, sort_keys=True)
                    if key not in self._fingerprints:
                        self._fingerprints[key] = objects.derive(
                            "answer-fingerprint-v1",
                            source_ref,
                            lambda source_ref=source_ref: object_reader.fingerprint(source_ref),
                        )
                    fingerprints[str(item.get("qa_id"))] = self._fingerprints[key]
                # The current transaction's objects are not visible to another connection.
                # Build node indexes from already available state, then commit together.
                analytics = {
                    **state,
                    "items": [self.metric_item(i, objects) for i in state.get("items") or []],
                }
                variants: dict[str, Any] = {}
                selections = [("", "")]
                selections.extend(
                    (str(b["profile_id"]), str(b["batch_id"])) for b in state.get("scoring_batches") or []
                )
                default_overview = None
                for profile, batch in selections:
                    selected = primary_profile_state(profile_state(analytics, profile, batch))
                    overview = {
                        k: v
                        for k, v in state.items()
                        if k not in {"items", "scoring_results", "diagnostic_results"}
                    }
                    if isinstance(overview.get("qa_snapshot"), dict):
                        overview["qa_snapshot"] = snapshot_metadata(overview["qa_snapshot"])
                    overview["scoring_batches"] = [
                        {k: v for k, v in b.items() if k not in {"materials", "targets"}}
                        for b in state.get("scoring_batches") or []
                    ]
                    overview["item_count"] = len(state.get("items") or [])
                    overview["summary"] = light_summary(analysis.effective_summary(selected))
                    if profile:
                        overview["selected_scoring_profile"] = profile
                        overview["selected_scoring_batch"] = batch
                    if default_overview is None:
                        default_overview = overview
                    diagnostic_state = {
                        **primary_profile_state(profile_state(state, profile, batch)),
                        "_answer_fingerprints": fingerprints,
                        "selected_scoring_profile": profile,
                        "selected_scoring_batch": batch,
                    }
                    summaries = []
                    cores = []
                    for index, item in enumerate(selected.get("items") or []):
                        summary = analysis.item_summary(item, index)
                        summary["profile_scores"] = item_profile_results(selected, str(item.get("qa_id")))
                        summary["_search_messages"] = " ".join(
                            str(t.get("message") or "") for t in item.get("runtime_turns") or []
                        )
                        # Table rows carry status/score, never Judge raw I/O or reasons.
                        summary.pop("evaluation_result", None)
                        for key in ("task_judge", "task_judges", "evidence_judge"):
                            summary.pop(key, None)
                        summaries.append(summary)
                        core = core_item(item)
                        qa_id = str(item.get("qa_id"))
                        core["index"] = index
                        core["profile_scores"] = summary["profile_scores"]
                        core["scoring_results"] = item_scores(selected, qa_id)
                        qa_row = next(
                            (
                                row
                                for row in (state.get("qa_snapshot") or {}).get("rows") or []
                                if str(row.get("qa_id")) == qa_id
                            ),
                            None,
                        )
                        if qa_row is not None:
                            core["_qa_snapshot_row"] = qa_row
                        core["diagnostic_results"] = [
                            value
                            for value in (state.get("diagnostic_results") or {}).values()
                            if str(value.get("qa_id")) == qa_id
                        ]
                        core["choice_matching_result"] = choice_matching_result(selected, qa_id)
                        diagnostic_key = objects.put(
                            {
                                "fingerprint": fingerprints[qa_id],
                                "qa": qa_row,
                                "item_metadata": {
                                    k: v for k, v in item.items() if k not in HEAVY and k != "runtime_turns"
                                },
                                "scores": {
                                    k: v
                                    for k, v in (state.get("scoring_results") or {}).items()
                                    if str(v.get("qa_id")) == qa_id
                                },
                                "batches": [
                                    (b["profile_id"], b["batch_id"])
                                    for b in state.get("scoring_batches") or []
                                ],
                                "profile": diagnostic_state.get("selected_scoring_profile"),
                                "batch": diagnostic_state.get("selected_scoring_batch"),
                            }
                        )
                        diagnostic_ref = objects.derive(
                            "process-diagnostics-v2",
                            diagnostic_key,
                            lambda diagnostic_state=diagnostic_state, qa_id=qa_id: objects.put(
                                process_for_qa(diagnostic_state, qa_id)
                            ),
                        )
                        core["process_diagnostics"] = object_reader.value(diagnostic_ref)
                        core = core_item(core)
                        core["trace_storage"] = {"version": 3, "item_index": index}
                        cores.append(core)
                    variants[profile + ":" + batch] = objects.put(
                        {"overview": overview, "index": summaries, "cores": cores}
                    )
                overview = default_overview or {}
                nodes = []
                for item in state.get("items") or []:
                    receipts = {
                        key: value
                        for key, value in (state.get("scoring_results") or {}).items()
                        if str(value.get("qa_id")) == str(item.get("qa_id"))
                    }
                    item_ref = objects.put(item)
                    # Trace identity is independent of score/status updates.
                    trace_key = (
                        tuple(json.dumps(objects.put(item.get(k)), sort_keys=True) for k in ITEM_TRACE_FIELDS)
                        + tuple(
                            json.dumps(objects.put(t.get(k)), sort_keys=True)
                            for t in item.get("runtime_turns") or []
                            for k in TURN_TRACE_FIELDS
                        )
                        + (
                            json.dumps([t.get("answer") for t in item.get("runtime_turns") or []]),
                            json.dumps(
                                objects.put(
                                    [
                                        {
                                            k: t.get(k)
                                            for k in (
                                                "judge",
                                                "evaluation_result",
                                                "task_judge",
                                                "evidence_judge",
                                            )
                                        }
                                        for t in item.get("runtime_turns") or []
                                    ]
                                ),
                                sort_keys=True,
                            ),
                            json.dumps(objects.put(receipts), sort_keys=True),
                            json.dumps(
                                objects.put(
                                    {
                                        k: item.get(k)
                                        for k in (
                                            "judge",
                                            "evaluation_result",
                                            "task_judge",
                                            "evidence_judge",
                                        )
                                    }
                                ),
                                sort_keys=True,
                            ),
                        )
                    )
                    node_ref = self._nodes_cache.get(trace_key)
                    if node_ref is None:
                        node_ref = objects.derive(
                            "node-directory-v4",
                            trace_key,
                            lambda item=item, receipts=receipts: objects.put(
                                build_node_index(build_nodes(item, objects, receipts), objects)
                            ),
                        )
                        self._nodes_cache[trace_key] = node_ref
                    nodes.append({"item": item_ref, "nodes": node_ref})
                views = objects.put({"variants": variants, "nodes": nodes})
                entry = analysis.run_list_entry(analytics)
                entry["summary"] = light_summary(entry.get("summary") or {})
                value = {
                    "version": 3,
                    "revision": uuid.uuid4().hex,
                    "root": root,
                    "views": views,
                    "entry": objects.put(entry),
                    "source": source,
                    "monitor": {
                        **{
                            k: plain(overview[k])
                            for k in (
                                "run_id",
                                "status",
                                "current_phase",
                                "scope_id",
                                "qa_count",
                                "item_count",
                                "reasoning",
                                "graph_enabled",
                                "model_name",
                                "model_identity",
                                "runtime_snapshot",
                                "execution_budgets",
                                "failed_phase",
                                "fatal_error",
                                "started_at",
                                "finished_at",
                                "scope_build",
                                "summary",
                            )
                            if k in overview
                        },
                        "phases": {
                            k: plain(v) for k, v in (state.get("phases") or {}).items() if k != "gpu_metrics"
                        },
                    },
                }
                objects.db.commit()
                objects.identities.clear()
                self._metric_cache.clear()
                # Keep immutable manifests so in-flight node pages/exports can pin a revision.
                history = run_dir / ".split-revisions"
                history.mkdir(parents=True, exist_ok=True)
                with (history / (value["revision"] + ".json")).open("xb") as handle:
                    handle.write(json_bytes(value))
                    handle.flush()
                    os.fsync(handle.fileno())
                publish(run_dir, value)
                return {
                    "revision": value["revision"],
                    "inserted_bytes": objects.inserted_bytes,
                    "inserted_objects": objects.inserted_objects,
                    "seconds": time.monotonic() - started,
                }
            except BaseException:
                objects.db.rollback()
                objects.identities.clear()
                raise


def build_nodes(
    item: dict[str, Any], objects: ObjectWriter, receipts: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Index saved records, retaining each original projection and its exact path."""
    nodes: list[dict[str, Any]] = []

    def add(record: Any, path: list[str | int], kind: str, turn: int) -> None:
        if not isinstance(record, dict):
            return
        metadata = {
            k: record[k]
            for k in (
                "profile_id",
                "batch_id",
                "purpose",
                "step_id",
                "call_id",
                "parent_step_id",
                "parent_call_id",
                "seq",
                "attempt",
                "call_type",
                "status",
                "duration_ms",
                "total_ms",
                "latency_s",
                "prompt_tokens",
                "completion_tokens",
                "tool",
                "tool_name",
                "kind",
                "type",
                "created_at",
                "timestamp",
                "streamed",
                "ttft_ms",
                "tokens_per_second",
                "model",
                "role",
            )
            if k in record and not isinstance(record[k], (dict, list))
        }
        metadata = {k: v[:256] if isinstance(v, str) else v for k, v in metadata.items()}
        while len(json_bytes(metadata)) > 1536:
            metadata.pop(next(reversed(metadata)))
        message_index = {}
        messages = record.get("messages")
        if isinstance(messages, list):
            message_index = {
                "message_count": len(messages),
                "message_roles": [
                    {"index": i, "role": str(message.get("role") or "unknown")[:32]}
                    for i, message in enumerate(messages[:50])
                    if isinstance(message, dict)
                ],
            }
        node_id = f"n{len(nodes)}"
        nodes.append(
            {
                "node_id": node_id,
                "kind": kind,
                "turn_index": turn,
                "metadata": metadata,
                "source_path": path,
                "record": objects.put(record),
                "fields": list(record.keys()),
                **message_index,
            }
        )

    turns = item.get("runtime_turns") or []
    for position, turn in enumerate(turns):
        turn_index = int(turn.get("index", position))
        base: list[str | int] = ["runtime_turns", position]
        bundle = turn.get("trace_bundle") or {}
        debug = bundle.get("debug_calls") or []
        for i, record in enumerate(debug):
            call_type = str(record.get("call_type") or "agent")
            kind = (
                "planner"
                if call_type == "planner"
                else "judge"
                if call_type in {"claim_verifier", "faithfulness_judge"}
                else "model"
            )
            add(record, [*base, "trace_bundle", "debug_calls", i], kind, turn_index)
        if not debug:
            for i, record in enumerate(bundle.get("model_calls") or []):
                add(record, [*base, "trace_bundle", "model_calls", i], "model", turn_index)
        for field in ("trace", "debug_trace"):
            for i, record in enumerate(turn.get(field) or []):
                add(record, [*base, field, i], "event", turn_index)
        if turn.get("agent2_trace"):
            add(turn["agent2_trace"], [*base, "agent2_trace"], "event", turn_index)
        for field in ("judge", "evaluation_result", "task_judge", "evidence_judge"):
            if isinstance(turn.get(field), dict):
                add(turn[field], [*base, field], "score", turn_index)
    for field, kind in (
        ("tool_trace", "tool"),
        ("model_call_metrics", "metrics"),
        ("execution_trace", "execution"),
    ):
        for i, record in enumerate(item.get(field) or []):
            add(record, [field, i], kind, int(record.get("conversation_turn") or 0))
    for field in ("judge", "evaluation_result", "task_judge", "evidence_judge"):
        if isinstance(item.get(field), dict):
            add(item[field], [field], "score", 0)
    for key, record in (receipts or {}).items():
        add(record, ["scoring_results", key], "score", int(record.get("turn_index") or 0))
    return nodes


def _node_group(kind: str) -> str:
    if kind == "event":
        return "event"
    if kind == "score":
        return "judges"
    if kind in {"metrics", "execution"}:
        return "sources"
    return "calls"


def order_call_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place tools after their uniquely identified parent attempt; preserve ambiguous records."""
    calls = [node for node in nodes if node["kind"] != "tool"]
    calls.sort(
        key=lambda node: (
            node.get("metadata", {}).get("seq")
            if isinstance(node.get("metadata", {}).get("seq"), (int, float))
            else 0
        )
    )
    by_call: dict[str, list[dict[str, Any]]] = {}
    by_step: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        metadata = call.get("metadata", {})
        if metadata.get("call_id"):
            by_call.setdefault(str(metadata["call_id"]), []).append(call)
        if metadata.get("step_id") and metadata.get("status") not in {"error", "failed", "cancelled"}:
            by_step.setdefault(str(metadata["step_id"]), []).append(call)
    children: dict[str, list[dict[str, Any]]] = {}
    unbound = []
    for node in nodes:
        if node["kind"] != "tool":
            continue
        metadata = node.get("metadata", {})
        call_id, step_id = metadata.get("parent_call_id"), metadata.get("parent_step_id")
        matches = by_call.get(str(call_id), []) if call_id else by_step.get(str(step_id), [])
        if len(matches) == 1:
            children.setdefault(str(matches[0]["node_id"]), []).append(node)
        else:
            unbound.append(node)
    return [row for call in calls for row in [call, *children.get(str(call["node_id"]), [])]] + unbound


def node_overview(nodes: Any) -> dict[str, Any]:
    """Aggregate only node metadata, never a model prompt or tool result."""
    turns: dict[int, dict[str, Any]] = {}
    for node in nodes:
        turn = turns.setdefault(int(node["turn_index"]), {"debug": [], "metrics": [], "tools": []})
        kind = node["kind"]
        if kind in {"planner", "model", "judge"}:
            turn["debug"].append(node.get("metadata", {}))
        elif kind == "metrics":
            turn["metrics"].append(node.get("metadata", {}))
        elif kind == "tool":
            turn["tools"].append(node.get("metadata", {}))
    result: dict[str, Any] = {
        "model_call_count": 0,
        "tool_call_count": 0,
        "model_ms": 0,
        "tool_ms": 0,
        "timing_complete": True,
    }
    for turn in turns.values():
        calls = turn["metrics"] or turn["debug"]
        result["model_call_count"] += len(calls)
        result["tool_call_count"] += len(turn["tools"])
        for field, records in (("model_ms", calls), ("tool_ms", turn["tools"])):
            for row in records:
                if field == "model_ms" and row.get("call_type") == "tool_internal":
                    continue
                duration = row.get("duration_ms", row.get("total_ms"))
                if duration is None and isinstance(row.get("latency_s"), (int, float)):
                    duration = row["latency_s"] * 1000
                if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
                    result[field] += duration
                else:
                    result["timing_complete"] = False
    return result


def build_node_index(nodes: list[dict[str, Any]], objects: ObjectWriter) -> dict[str, Any]:
    """Store bounded node-summary pages and a compact per-turn directory."""
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for node in nodes:
        turn = int(node["turn_index"])
        group = _node_group(str(node["kind"]))
        rows = grouped.setdefault((turn, group), [])
        rows.append({**node, "node_id": f"t{turn}_g{group}_i{len(rows)}"})

    turns: dict[str, dict[str, Any]] = {}
    for (turn, group), rows in grouped.items():
        if group == "calls":
            rows = order_call_nodes(rows)
        rows = [{**row, "node_id": f"t{turn}_g{group}_i{position}"} for position, row in enumerate(rows)]
        pages = [
            objects.put(rows[start : start + NODE_PAGE_SIZE]) for start in range(0, len(rows), NODE_PAGE_SIZE)
        ]
        turns.setdefault(str(turn), {})[group] = {"count": len(rows), "pages": pages}
    return {
        "page_size": NODE_PAGE_SIZE,
        "turns": turns,
        "order_version": 2,
        "read_summary": node_overview(nodes),
    }


class SplitRunReader:
    def __init__(self, root: Path, run_dir: Path, revision: str = "") -> None:
        self.run_dir = run_dir
        if revision:
            if len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
                raise ValueError("invalid read revision")
            self.manifest = json.loads((run_dir / ".split-revisions" / (revision + ".json")).read_text())
        else:
            self.manifest = manifest(run_dir)
        if self.manifest is None:
            raise ReadIndexRequired(run_dir.name)
        self.reader = ObjectReader(database(root))
        self.revision = self.manifest["revision"]

    def state(self) -> dict[str, Any]:
        return cast(dict[str, Any], thaw(self.reader.value(self.manifest["root"])))

    def entry(self) -> dict[str, Any]:
        return cast(dict[str, Any], plain(self.reader.value(self.manifest["entry"])))

    def variant(self, profile: str = "", batch: str = "") -> Any:
        variants = self.reader.value(self.manifest["views"])["variants"]
        if not profile and batch:
            raise ValueError("Select a scoring standard for this batch")
        if profile and not batch:
            candidates = [key for key in variants if key.startswith(profile + ":")]
            if not candidates:
                raise ValueError("Unknown scoring standard")
            key = candidates[-1]
        else:
            key = profile + ":" + batch
        if key not in variants:
            raise ValueError("Unknown scoring batch")
        return self.reader.value(variants[key])

    def overview(self, profile: str = "", batch: str = "") -> dict[str, Any]:
        return {
            **plain(self.variant(profile, batch)["overview"]),
            "read_revision": self.revision,
            "storage_format": 3,
        }

    def index(self, profile: str = "", batch: str = "") -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], plain(self.variant(profile, batch)["index"]))

    def core(
        self, index: int, profile: str = "", batch: str = "", *, preview: bool = False
    ) -> dict[str, Any]:
        cores = self.variant(profile, batch)["cores"]
        if index < 0 or index >= len(cores):
            raise KeyError(f"item index out of range: {index}")
        core = cores[index]
        result = plain({k: core[k] for k in core if k != "process_diagnostics"})
        if not preview:
            result["process_diagnostics"] = plain(core.get("process_diagnostics"))
        else:
            # QA viewing never dereferences diagnostics. Analysis/export remain explicit consumers.
            fields = [
                row for row in result.get("content_fields", []) if row.get("path") != "process_diagnostics"
            ]
            if fields:
                result["content_fields"] = fields
            else:
                result.pop("content_fields", None)
            result["trace_read_summary"] = self.trace_overview(index)
        result["trace_storage"]["revision"] = self.revision
        return cast(dict[str, Any], result)

    def trace_overview(self, index: int) -> dict[str, Any]:
        directory = self.reader.value(self.item_ref(index)["nodes"])
        if "read_summary" in directory:
            return cast(dict[str, Any], plain(directory["read_summary"]))

        # Existing immutable revisions need no rewrite or trace hydration.
        def summaries() -> Any:
            turns = self.reader.value(directory["turns"])
            for groups in turns.values():
                for name, group in groups.items():
                    if name not in {"calls", "sources"}:
                        continue
                    for page in group.get("pages", []):
                        for node in self.reader.value(page):
                            yield {
                                "turn_index": node["turn_index"],
                                "kind": node["kind"],
                                "metadata": plain(node.get("metadata", {})),
                            }

        return node_overview(summaries())

    def item_ref(self, index: int) -> Any:
        values = self.reader.value(self.manifest["views"])["nodes"]
        if index < 0 or index >= len(values):
            raise KeyError(f"item index out of range: {index}")
        return values[index]

    def node_record(self, node: Any) -> Any:
        record = self.reader.value(node["record"])
        # Older directories encoded a reused descriptor as a JSON dictionary.
        # Its field list distinguishes this internal wrapper from original JSON.
        if is_reference(record) and list(record) != list(node.get("fields", [])):
            record = self.reader.value(plain(record))
        return record

    def item_record(self, index: int) -> dict[str, Any]:
        """Return one QA as a lazy mapping; large text fields load only when accessed."""
        return cast(dict[str, Any], self.reader.value(self.item_ref(index)["item"]))

    def item_answer_fingerprint(self, index: int) -> str:
        """Match process diagnostics' fingerprint while streaming the saved trace."""
        return self.reader.fingerprint(answer_source(self.item_record(index)))

    def nodes(
        self,
        index: int,
        *,
        turn: int = 0,
        kind: str = "",
        offset: int = 0,
        limit: int = 50,
        scoring_profile: str = "",
        scoring_batch: str = "",
    ) -> dict[str, Any]:
        page_size = NODE_PAGE_SIZE
        offset, limit = max(0, offset), max(1, min(50, limit))
        group = kind if kind in {"event", "sources", "judges"} else "calls"
        node_index = self.reader.value(self.item_ref(index)["nodes"])
        turn_index = self.reader.value(node_index["turns"])
        group_index = self.reader.value(turn_index.get(str(turn), {})).get(group, {})
        total = int(group_index.get("count", 0))
        pages = group_index.get("pages", [])
        selected = []
        if group == "calls" and node_index.get("order_version") != 2:
            rows = [
                {k: plain(v) for k, v in row.items() if k != "record"}
                for page in pages
                for row in self.reader.value(page)
            ]
            selected = [
                {k: v for k, v in row.items() if k != "record"}
                for row in order_call_nodes(rows)[offset : offset + limit]
            ]
        else:
            for position in range(offset, min(offset + limit, total)):
                page_number, page_offset = divmod(position, page_size)
                rows = self.reader.value(pages[page_number])
                row = rows[page_offset]
                selected.append({k: plain(v) for k, v in row.items() if k != "record"})
        for node in selected:
            if "messages" not in node.get("fields", []) or "message_roles" in node:
                continue
            # Resolve the original node address even when the UI order was sorted.
            position = int(str(node["node_id"]).split("_i")[-1])
            record_page, record_offset = divmod(position, page_size)
            record = self.node_record(self.reader.value(pages[record_page])[record_offset])
            messages = record.get("messages")
            if isinstance(messages, list):
                node["message_count"] = len(messages)
                node["message_roles"] = [
                    {"index": i, "role": str(message.get("role") or "unknown")[:32]}
                    for i, message in enumerate(messages[:50])
                    if isinstance(message, dict)
                ]
        return {
            "nodes": selected,
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "read_revision": self.revision,
            "order_version": 2,
        }

    def _node(self, index: int, node_id: str) -> Any:
        if not node_id.startswith("t"):
            raise KeyError("invalid node id")
        try:
            turn_part, group_part, node_part = node_id.split("_")
            turn = int(turn_part[1:])
            group = group_part[1:]
            position = int(node_part[1:])
        except (ValueError, IndexError) as exc:
            raise KeyError("invalid node id") from exc
        if not node_id.startswith("t") or group not in {"calls", "event", "sources", "judges"}:
            raise KeyError("invalid node id")
        node_index = self.reader.value(self.item_ref(index)["nodes"])
        turn_index = self.reader.value(node_index["turns"])
        group_index = self.reader.value(turn_index.get(str(turn), {})).get(group, {})
        if position < 0 or position >= int(group_index.get("count", 0)):
            raise KeyError(node_id)
        page_number, page_offset = divmod(position, NODE_PAGE_SIZE)
        nodes = self.reader.value(group_index["pages"][page_number])
        return nodes[page_offset]

    def timing(self, index: int, node_id: str) -> dict[str, Any]:
        """Read one loop's timings by saved identities, without hydrating its I/O."""
        row = self._node(index, node_id)

        def facts(node: Any) -> dict[str, Any]:
            record = self.node_record(node)
            value: dict[str, Any] = plain(node.get("metadata", {}))
            for key in (
                "agent_loop_total_ms",
                "ttft_ms",
                "model_generation_ms",
                "preflight_prompt_tokens",
                "effective_max_tokens",
                "max_model_len",
            ):
                if record.get(key) is not None:
                    value[key] = record[key]
            loop = record.get("agent_loop_timing")
            if isinstance(loop, dict):
                value["agent_loop_timing"] = {
                    key: loop[key] for key in ("tool_ms", "model_generation_ms") if key in loop
                }
            timing = record.get("retrieval_timing")
            if isinstance(timing, dict):
                value["retrieval_timing"] = {
                    key: timing[key]
                    for key in ("query_build_ms", "fusion_ms", "postprocess_ms", "total_ms")
                    if key in timing
                }
                value["retrieval_timing"]["channels"] = {
                    key: {"latency_ms": channel.get("latency_ms")}
                    for key, channel in (timing.get("channels") or {}).items()
                    if isinstance(channel, dict)
                }
            if node["kind"] == "tool":
                result = record.get("result")
                if not isinstance(result, dict):
                    result = {}
                channels = result.get("retrieval_channels") or []
                if "retrieval_timing" not in value and channels:
                    value["retrieval_timing"] = {
                        "channels": {
                            str(channel.get("channel") or i): {"latency_ms": channel.get("latency_ms")}
                            for i, channel in enumerate(channels)
                            if isinstance(channel, dict)
                        }
                    }
                value["model_calls"] = [
                    {
                        key: observation[key]
                        for key in (
                            "call_id",
                            "duration_ms",
                            "total_ms",
                            "ttft_ms",
                            "status",
                            "model",
                            "prompt_tokens",
                            "completion_tokens",
                            "tokens_per_second",
                            "streamed",
                        )
                        if key in observation and not isinstance(observation[key], (dict, list))
                    }
                    for observation in result.get("model_call_observations") or []
                ]
            return value

        selected = facts(row)
        turn = int(row["turn_index"])
        calls, tools, metrics = [], [], []
        directory = self.reader.value(self.item_ref(index)["nodes"])
        groups = self.reader.value(directory["turns"]).get(str(turn), {})
        for group in ("calls", "sources"):
            for page in groups.get(group, {}).get("pages", []):
                for node in self.reader.value(page):
                    if node["kind"] == "tool":
                        tools.append(node)
                    elif node["kind"] == "metrics":
                        metrics.append(node)
                    elif node["kind"] in {"model", "planner", "judge"}:
                        calls.append(node)
        call_id, step = selected.get("call_id"), selected.get("step_id")
        matching = [m for m in metrics if call_id and m["metadata"].get("call_id") == call_id]
        if len(matching) == 1:
            selected = {**facts(matching[0]), **{k: v for k, v in selected.items() if v is not None}}
        peers = calls or metrics
        successful = [
            c
            for c in peers
            if c["metadata"].get("step_id") == step
            and c["metadata"].get("call_type") != "tool_internal"
            and c["metadata"].get("status") not in {"error", "failed", "cancelled"}
        ]
        unique_call = call_id and sum(c["metadata"].get("call_id") == call_id for c in peers) == 1
        bound = []
        tools_complete = True
        if row["kind"] != "tool" and selected.get("status") not in {"error", "failed", "cancelled"}:
            for tool in tools:
                meta = tool["metadata"]
                matches = (
                    unique_call and meta["parent_call_id"] == call_id
                    if meta.get("parent_call_id")
                    else step and meta.get("parent_step_id") == step and len(successful) == 1
                )
                if matches:
                    bound.append({**facts(tool), "node_id": tool["node_id"]})
                elif not meta.get("parent_call_id") and (
                    not meta.get("parent_step_id") or meta.get("parent_step_id") == step
                ):
                    tools_complete = False
        return {
            "node": selected,
            "tools": bound,
            "tools_complete": tools_complete,
            "read_revision": self.revision,
        }

    def content(
        self,
        index: int,
        node_id: str,
        field: str = "",
        offset: int = 0,
        limit: int = 65536,
        scoring_profile: str = "",
        scoring_batch: str = "",
    ) -> dict[str, Any]:
        row = self._node(index, node_id)
        value = self.node_record(row)
        if field:
            value = self.reader.walk(
                value, [int(part) if part.isdigit() else part for part in field.split(".")]
            )
        return {
            **self.reader.content(value, offset, limit),
            "node_id": node_id,
            "field": field,
            "read_revision": self.revision,
            "source_path": plain(row["source_path"]),
        }

    def item_content(self, index: int, path: list[str | int], offset: int, limit: int) -> dict[str, Any]:
        if path == ["process_diagnostics"]:
            core = self.variant()["cores"][index]
            value = self.reader.walk(core, path)
        else:
            value = self.reader.walk(self.item_ref(index)["item"], path)
        return {**self.reader.content(value, offset, limit), "read_revision": self.revision}
