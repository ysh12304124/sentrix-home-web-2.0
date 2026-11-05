"""One predicate for filtered aggregates, pagination, export and AI scope."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.error_analysis.taxonomy import category_value

EVENT_FIELDS = {"code", "recovery", "tool_name", "issue_kind", "failure_reason", "component"}
TURN_FIELDS = {
    "ability_taxonomy_version",
    "ability_family",
    "ability_type",
    "benchmark_group",
    "content_type",
    "content_subtype",
    "difficulty",
    "answerability",
    "score",
    "expected_action",
    "actual_action",
    "behavior_result",
    "test_scenario",
}
BEHAVIOR_FIELDS = {"expected_action", "actual_action", "behavior_result", "test_scenario"}


def behavior(turn: Mapping[str, Any]) -> str:
    value = turn.get("behavior_correct")
    return "correct" if value is True else "incorrect" if value is False else "missing"


def score_matches(value: Any, wanted: Any) -> bool:
    return value in (0, 1) if wanted == "low" else str(value) == str(wanted)


def turn_matches(turn: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for key, value in filters.items():
        if key == "score":
            if not score_matches(turn.get("score"), value):
                return False
        elif key in {"expected_action", "actual_action"}:
            if str(turn.get("expected" if key == "expected_action" else "actual") or "unknown") != value:
                return False
        elif key == "behavior_result":
            if behavior(turn) != value:
                return False
        elif value not in turn.get("test_scenarios" if key == "test_scenario" else key, []):
            return False
    return True


def select_case(case: Mapping[str, Any], filters: Mapping[str, Any]) -> dict[str, Any] | None:
    f = {k: v for k, v in filters.items() if v is not None and v != ""}
    query = str(f.pop("q", "")).casefold()
    if (
        query
        and query not in " ".join(str(case.get(k) or "") for k in ["qa_id", "question", "answer"]).casefold()
    ):
        return None
    index = f.pop("index", None)
    if index is not None and str(case["index"]) != str(index):
        return None
    unit = f.pop("content_unit", "qa")
    turn_number = f.pop("turn", None)
    tf = {k: v for k, v in f.items() if k in (TURN_FIELDS if unit == "turn" else BEHAVIOR_FIELDS)}
    turns = [
        t
        for t in case["turns"]
        if turn_matches(t, tf) and (turn_number is None or str(t["turn"]) == str(turn_number))
    ]
    if (tf or unit == "turn" or turn_number is not None) and not turns:
        return None
    event_filters = {k: v for k, v in f.items() if k in EVENT_FIELDS}
    turn_ids = {t["turn"] for t in turns}
    constrain_turn = bool(tf) or unit == "turn" or turn_number is not None
    events = [
        e
        for e in case["events"]
        if (not constrain_turn or e.get("turn", 0) in turn_ids)
        and all(str(e.get(k) or "unknown") == str(v) for k, v in event_filters.items())
    ]
    tool_only = set(event_filters) == {"tool_name"}
    matching_tools = [
        t
        for t in case.get("tool_inventory", [])
        if str(t["tool_name"]) == str(event_filters.get("tool_name"))
        and (not constrain_turn or t["turn"] in turn_ids)
    ]
    if event_filters and not events and not (tool_only and matching_tools):
        return None
    if "anomaly" in f and bool(events) != (f["anomaly"] == "yes"):
        return None
    if event_filters:
        turn_ids &= {e.get("turn", 0) for e in events} | (
            {t["turn"] for t in matching_tools} if tool_only else set()
        )
    for key, value in f.items():
        if key in event_filters or key in tf or key == "anomaly":
            continue
        if key == "score" and not score_matches(case.get("score"), value):
            return None
        if key == "execution" and case.get(key) != value:
            return None
        if key == "signal" and value not in case.get("signals", []):
            return None
        if key in case.get("dimension_labels", {}) and category_value(key, str(value)) not in {
            category_value(key, str(v)) for v in case["dimension_labels"][key]
        }:
            return None
    return {
        **case,
        "matched_event_ids": [e.get("event_id", "") for e in events],
        "matched_turns": sorted(turn_ids),
    }


def issue_groups(cases: Sequence[Mapping[str, Any]], matched: bool = False) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for case in cases:
        for event in case["events"]:
            if matched and event["event_id"] not in case.get("matched_event_ids", []):
                continue
            key = tuple(
                str(event.get(k) or "unknown") for k in ("tool_name", "code", "failure_reason", "component")
            )
            row = groups.setdefault(
                key,
                {
                    "tool_name": key[0],
                    "code": key[1],
                    "failure_reason": key[2],
                    "component": key[3],
                    "label": event["label"],
                    "indices": set(),
                    "calls": set(),
                    "low": set(),
                    "events": [],
                    "recovery": Counter(),
                    "issue_kind": event.get("issue_kind", "unknown"),
                },
            )
            row["indices"].add(case["index"])
            if case["score"] in (0, 1):
                row["low"].add(case["index"])
            if event.get("call_key"):
                row["calls"].add(event["call_key"])
            row["events"].append(event["event_id"])
            row["recovery"][event["recovery"]] += 1
    return [
        dict(
            group_id=f"g{i}",
            tool_name=r["tool_name"],
            code=r["code"],
            label=r["label"],
            failure_reason=r["failure_reason"],
            component=r["component"],
            issue_kind=r["issue_kind"],
            affected_items=len(r["indices"]),
            affected_calls=len(r["calls"]),
            low_score_items=len(r["low"]),
            indices=sorted(r["indices"]),
            event_ids=r["events"],
            events=len(r["events"]),
            recovery=dict(r["recovery"]),
        )
        for i, r in enumerate(
            sorted(groups.values(), key=lambda r: (-len(r["indices"]), r["tool_name"], r["code"]))
        )
    ]


def filtered_view(
    cases: Sequence[dict[str, Any]],
    report: dict[str, Any],
    filters: Mapping[str, Any],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    from photobench.domain.error_analysis.rules import Aggregator

    selected = [c for case in cases if (c := select_case(case, filters)) is not None]
    aggregate = Aggregator()
    for case in selected:
        projected = {
            **case,
            "turns": [t for t in case["turns"] if t["turn"] in case["matched_turns"]],
            "events": [e for e in case["events"] if e.get("event_id", "") in case["matched_event_ids"]],
        }
        aggregate.add(projected)
    summary = aggregate.report(report["source"])
    summary.update(
        generated_at=report.get("generated_at"),
        result_fingerprint=report.get("result_fingerprint"),
        call_count_scope="matching_cases_all_turns",
        matched_event_scope="matching_events",
    )
    facets: dict[str, Counter[str]] = {}
    for case in cases:
        for key, values in case.get("dimension_labels", {}).items():
            facets.setdefault(key, Counter()).update(set(values))
        facets.setdefault("tool_name", Counter()).update(
            {t["tool_name"] for t in case.get("tool_inventory", [])}
            | {str(e.get("tool_name") or "unknown") for e in case["events"]}
        )
        for key in EVENT_FIELDS - {"tool_name"}:
            facets.setdefault(key, Counter()).update({str(e.get(key) or "unknown") for e in case["events"]})
    tools: dict[str, dict[str, Any]] = {}
    for case in selected:
        for call in case.get("tool_inventory", []):
            if call["turn"] not in case["matched_turns"]:
                continue
            name = call["tool_name"]
            if filters.get("tool_name") and filters["tool_name"] != name:
                continue
            row = tools.setdefault(
                name,
                {"tool_name": name, "calls": 0, "items": set(), "affected": set(), "affected_items": set()},
            )
            row["calls"] += 1
            row["items"].add(case["index"])
        for e in case["events"]:
            if (
                e.get("event_id", "") in case["matched_event_ids"]
                and e.get("tool_name") in tools
                and e.get("call_key")
            ):
                tools[e["tool_name"]]["affected"].add(e["call_key"])
                tools[e["tool_name"]]["affected_items"].add(case["index"])
    return {
        "items": selected[(page - 1) * page_size : page * page_size],
        "total": len(selected),
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "issues": issue_groups(selected, matched=True),
        "tools": [
            dict(
                tool_name=v["tool_name"],
                recorded_calls=v["calls"],
                using_items=len(v["items"]),
                affected_items=len(v["affected_items"]),
                affected_calls=len(v["affected"]),
            )
            for v in tools.values()
        ],
        "facets": {
            k: [{"value": v, "count": n} for v, n in sorted(values.items())] for k, values in facets.items()
        },
        "ability_tree": {
            family: sorted(
                {
                    t
                    for c in cases
                    for turn in c["turns"]
                    if family in turn.get("ability_family", [])
                    for t in turn.get("ability_type", [])
                    if t not in {"unknown", "not_applicable"} and t.startswith(family)
                }
            )
            for family in facets.get("ability_family", {})
        },
    }
