"""Read-only task families, connected exclusively by saved resume provenance."""

from __future__ import annotations

from typing import Any

TASK_FIELDS = (
    "rejudge_id",
    "task_type",
    "source_run_id",
    "status",
    "started_at",
    "finished_at",
    "completed",
    "total",
    "failed",
    "unit",
    "qa_count",
    "turn_count",
    "subscore_count",
    "scope",
    "batch_ids",
)


def execution_metadata(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("resume_context") or {}
    inherited = context.get("inherited_qa_ids")
    resume = {
        k: context[k] for k in ("resumed_from", "source_status", "source_build_revision") if k in context
    }
    if isinstance(inherited, list):
        resume["inherited_count"] = len(set(inherited))
    tasks = [*(state.get("rejudge_history") or []), *([state["rejudge"]] if state.get("rejudge") else [])]
    return {
        **({"source_run_id": state["source_run_id"]} if state.get("source_run_id") else {}),
        "resume_context": resume,
        "scoring_executions": [{k: t[k] for k in TASK_FIELDS if k in t} for t in tasks],
    }


def task_families(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row["run_id"]): row for row in rows}

    def root(run_id: str) -> str:
        seen: list[str] = []
        while run_id in by_id:
            if run_id in seen:
                return min(seen[seen.index(run_id) :])
            seen.append(run_id)
            parent = str((by_id[run_id].get("resume_context") or {}).get("resumed_from") or "")
            if not parent:
                return run_id
            run_id = parent
        return run_id

    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        families.setdefault(root(str(row["run_id"])), []).append(row)
    result = []
    for task_id, members in families.items():
        # Input is newest first; each answer execution is an immutable saved run.
        latest = members[0]
        versions = []
        known: set[str] = set()
        for member in reversed(members):
            run_id = member["run_id"]
            versions.append(
                {
                    "execution_id": run_id,
                    "run_id": run_id,
                    "kind": "answers",
                    "status": member.get("status"),
                    "started_at": member.get("started_at"),
                    "finished_at": member.get("finished_at"),
                    "build_revision": member.get("build_revision"),
                    "source_run_id": (member.get("resume_context") or {}).get("resumed_from"),
                    "inherited_count": (member.get("resume_context") or {}).get("inherited_count"),
                    "answer_count": member.get("item_count"),
                    "qa_count": member.get("qa_count"),
                }
            )
            for task in member.get("scoring_executions") or []:
                task_id_value = task.get("rejudge_id")
                if not task_id_value or task_id_value in known:
                    continue
                known.add(task_id_value)
                versions.append({**task, "execution_id": task_id_value, "run_id": run_id, "kind": "scoring"})
        result.append(
            {
                **latest,
                "task_id": task_id,
                "task_run_ids": [m["run_id"] for m in members],
                "execution_versions": versions,
            }
        )
    return result
