"""Durable artifacts with a single spawn worker, cancellation and benchmark priority."""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from photobench.domain.error_analysis.diagnosis import PROMPT_VERSION, evidence_package
from photobench.domain.error_analysis.exports import export_auxiliary, export_report
from photobench.domain.error_analysis.query import filtered_view, select_case
from photobench.domain.error_analysis.rules import RULE_VERSION, matches
from photobench.infrastructure.error_analysis.worker import atomic, lines, revision, run_worker

ACTIVE = {"queued", "running", "cancelling", "paused"}


class FileAnalysisBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts = root / ".analysis"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy: Callable[[], bool] = lambda: False
        self._queue: list[str] = []
        self._latest: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._secret: dict[str, dict[str, Any]] = {}
        self._current: tuple[str, Any, Any, Any] | None = None
        self._context = multiprocessing.get_context("spawn")
        self._case_cache: dict[str, list[dict[str, Any]]] = {}

    def _directory(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise KeyError(job_id)
        return self.artifacts / job_id

    def _run(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise KeyError(run_id)
        p = self.root / run_id
        if not (p / "run.json").is_file():
            raise KeyError(run_id)
        return p

    def start(self, busy: Callable[[], bool]) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._busy = busy
            self._history.clear()
            self._latest.clear()
            self.artifacts.mkdir(parents=True, exist_ok=True)
            for path in self.artifacts.glob("*/job.json"):
                try:
                    job = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                self._history.setdefault(job["run_id"], []).append(job["job_id"])
                if job.get("kind") == "rules":
                    previous = self._latest.get(job["run_id"])
                    if previous is None or self.get(previous)["created_at"] < job["created_at"]:
                        self._latest[job["run_id"]] = job["job_id"]
                if job["status"] in ACTIVE:
                    job.update(
                        status="interrupted", error="analysis service restarted", finished_at=time.time()
                    )
                    atomic(path, job)
            self._stop.clear()
            self._thread = threading.Thread(target=self._supervise, name="analysis-supervisor", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for key in self._queue:
                job = self.get(key)
                job.update(status="cancelled")
                atomic(self._directory(key) / "job.json", job)
            self._queue.clear()
            self._secret.clear()
            current = self._current
            if current:
                current[2].set()
                current[3].clear()
        if self._thread:
            self._thread.join(timeout=65)
            if self._thread.is_alive():
                raise RuntimeError("analysis worker shutdown still pending")
        self._thread = None

    def _supervise(self) -> None:
        while not self._stop.wait(0.1) or self._current is not None:
            if self._stop.is_set():
                time.sleep(0.05)
            with self._lock:
                if self._current:
                    key, process, cancel, pause = self._current
                    if self._busy() and not self._stop.is_set():
                        pause.set()
                    else:
                        pause.clear()
                    if process.is_alive():
                        continue
                    process.join()
                    job = self.get(key)
                    if job["status"] in ACTIVE:
                        job.update(
                            status="cancelled" if cancel.is_set() else "failed",
                            error="analysis process exited without terminal commit",
                        )
                        atomic(self._directory(key) / "job.json", job)
                    process.close()
                    self._current = None
                if self._stop.is_set() or not self._queue or self._busy():
                    continue
                key = self._queue.pop(0)
                job = self.get(key)
                cancel = self._context.Event()
                pause = self._context.Event()
                secret = self._secret.pop(key, {})
                process = self._context.Process(
                    target=run_worker,
                    args=(str(self.root), str(self._directory(key)), job, cancel, pause, secret),
                    name="photobench-analysis",
                )
                try:
                    process.start()
                    self._current = (key, process, cancel, pause)
                except Exception as e:  # noqa: BLE001
                    job.update(status="failed", error=str(e))
                    atomic(self._directory(key) / "job.json", job)

    def submit(self, run_id: str, kind: str, options: Mapping[str, Any]) -> dict[str, Any]:
        self._run(run_id)
        if self._thread is None:
            raise ValueError("analysis service not started")
        with self._lock:
            if len(self._queue) >= 32:
                raise ValueError("analysis queue is full")
            values = dict(options)
            secret = values.pop("secret", {})
            if kind == "rules":
                for pending in self.work():
                    if (
                        pending["run_id"] == run_id
                        and pending["analysis_kind"] == kind
                        and pending["status"] in ACTIVE
                    ):
                        return self.get(pending["id"])
            elif kind in {"ai", "compare"}:
                keys = ["parent_job"] if kind == "ai" else ["left_job", "right_job"]
                for option in keys:
                    if not values.get(option):
                        raise ValueError(f"{option} is required")
                    previous = self.get(str(values[option]))
                    if (
                        previous["kind"] != "rules"
                        or previous["status"] != "completed"
                        or self._stale(previous)
                    ):
                        raise ValueError("a current completed rules report is required")
                    if option in {"parent_job", "left_job"} and previous["run_id"] != run_id:
                        raise ValueError("report belongs to a different run")
                    values[option.replace("_job", "_path")] = str(self._directory(previous["job_id"]))
                if kind == "ai":
                    if not 1 <= int(values.get("max_cases", 12)) <= 100:
                        raise ValueError("max_cases must be between 1 and 100")
                    all_cases = list(lines(Path(values["parent_path"]) / "cases.jsonl"))
                    scope = values.get("scope", "selection")
                    indices = values.get("indices") or []
                    if scope == "run":
                        indices = [c["index"] for c in all_cases]
                    elif scope == "filter":
                        indices = [c["index"] for c in all_cases if matches(c, values.get("filters", {}))]
                    if (
                        not indices
                        or any(type(i) is not int or i < 0 for i in indices)
                        or len(set(indices)) != len(indices)
                    ):
                        raise ValueError("select distinct case indices in a nonempty scope")
                    if not set(indices) <= {c["index"] for c in all_cases}:
                        raise ValueError("unknown case index")
                    values["indices"] = sorted(indices)
                    if values.get("resume_job"):
                        resume = self.get(str(values["resume_job"]))
                        if resume["kind"] != "ai" or resume["options"]["parent_job"] != values["parent_job"]:
                            raise ValueError("resume requires the same source report")
                        if resume["status"] in ACTIVE:
                            raise ValueError("analysis is still active")
                        values["resume_path"] = str(self._directory(resume["job_id"]))
            else:
                raise ValueError("unknown kind")
            key = uuid.uuid4().hex
            job = {
                "job_id": key,
                "run_id": run_id,
                "kind": kind,
                "status": "queued",
                "options": values,
                "created_at": time.time(),
                "processed": 0,
                "owner_pid": __import__("os").getpid(),
            }
            directory = self._directory(key)
            directory.mkdir()
            atomic(directory / "job.json", job)
            self._secret[key] = secret
            self._queue.append(key)
            self._history.setdefault(run_id, []).append(key)
            if kind == "rules":
                self._latest[run_id] = key
            return job

    def _stale(self, job: dict[str, Any]) -> bool:
        if job["status"] != "completed":
            return False
        if job["kind"] != "rules":
            keys = ["parent_job"] if job["kind"] == "ai" else ["left_job", "right_job"]
            try:
                if job["kind"] == "ai":
                    diagnosis = self._directory(job["job_id"]) / "diagnosis.json"
                    if (
                        diagnosis.is_file()
                        and json.loads(diagnosis.read_text()).get("prompt_version") != PROMPT_VERSION
                    ):
                        return True
                return any(self._stale(self.get(job["options"][key])) for key in keys)
            except (KeyError, ValueError, OSError):
                return True
        try:
            report = json.loads((self._directory(job["job_id"]) / "report.json").read_text())
            return bool(
                report.get("rule_version") != RULE_VERSION
                or report["source"]["revision"] != revision(self.root, job["run_id"])
            )
        except (OSError, ValueError, KeyError):
            return True

    def get(self, job_id: str) -> dict[str, Any]:
        path = self._directory(job_id) / "job.json"
        if not path.is_file():
            raise KeyError(job_id)
        job: dict[str, Any] = json.loads(path.read_text())
        return job

    def history(self, run_id: str) -> list[dict[str, Any]]:
        self._run(run_id)
        with self._lock:
            jobs = [self.get(key) for key in self._history.get(run_id, [])]
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

    def latest(self, run_id: str) -> dict[str, Any]:
        self._run(run_id)
        with self._lock:
            key = self._latest.get(run_id)
        if key is None:
            return {"status": "not_generated"}
        job = self.get(key)
        result = {**job, "stale": self._stale(job)}
        if job["status"] == "completed":
            result["report"] = json.loads((self._directory(job["job_id"]) / "report.json").read_text())
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._secret.pop(job_id, None)
                job.update(status="cancelled")
                atomic(self._directory(job_id) / "job.json", job)
            elif self._current and self._current[0] == job_id:
                self._current[2].set()
            return {
                **job,
                "cancel_requested": True,
                "status": "cancelling"
                if job["status"] in ACTIVE and self._current and self._current[0] == job_id
                else job["status"],
            }

    def work(self) -> list[dict[str, Any]]:
        with self._lock:
            keys = [*self._queue] + ([self._current[0]] if self._current else [])
            return [
                {
                    "id": k,
                    "kind": "analysis",
                    "analysis_kind": self.get(k)["kind"],
                    "run_id": self.get(k)["run_id"],
                    "status": self.get(k)["status"],
                    "worker_alive": bool(
                        self._current and self._current[0] == k and self._current[1].is_alive()
                    ),
                }
                for k in keys
            ]

    def cases(self, job_id: str, filters: Mapping[str, Any], page: int, page_size: int) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] != "completed" or job["kind"] != "rules":
            raise ValueError("completed rules report required")
        selected = [x for c in self._cases(job_id) if (x := select_case(c, filters)) is not None]
        return {
            "items": selected[(page - 1) * page_size : page * page_size],
            "total": len(selected),
            "page": page,
            "page_size": page_size,
        }

    def _cases(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if job_id not in self._case_cache:
                if len(self._case_cache) >= 4:
                    self._case_cache.pop(next(iter(self._case_cache)))
                self._case_cache[job_id] = list(lines(self._directory(job_id) / "cases.jsonl"))
            return self._case_cache[job_id]

    def view(self, job_id: str, filters: Mapping[str, Any], page: int, page_size: int) -> dict[str, Any]:
        job = self.get(job_id)
        if job["kind"] != "rules" or job["status"] != "completed":
            raise ValueError("completed rules report required")
        report = json.loads((self._directory(job_id) / "report.json").read_text())
        return filtered_view(self._cases(job_id), report, filters, page, page_size)

    def evidence(self, job_id: str, index: int) -> dict[str, Any]:
        job = self.get(job_id)
        if job["kind"] != "rules" or job["status"] != "completed":
            raise ValueError("completed rules report required")
        path = self._directory(job_id) / "input.jsonl"
        if not path.exists():
            raise ValueError("此历史报告未保存证据快照，请重新生成规则报告")
        for i, item in enumerate(lines(path)):
            if i == index:
                return {
                    "item": item,
                    "evidence_locations": {
                        r["id"]: r["path"]
                        for r in evidence_package(self._cases(job_id)[index], item)["records"]
                    },
                    "job_id": job_id,
                    "index": index,
                    "result_fingerprint": json.loads((path.parent / "report.json").read_text())[
                        "result_fingerprint"
                    ],
                }
        raise KeyError(index)

    def export(self, job_id: str, format: str, filters: Mapping[str, Any]) -> str:
        job = self.get(job_id)
        if job["status"] != "completed" and not (
            job["kind"] == "ai" and (self._directory(job_id) / "diagnosis.json").is_file()
        ):
            raise ValueError("analysis not completed")
        directory = self._directory(job_id)
        if job["kind"] != "rules":
            report = json.loads(
                (
                    directory / ("comparison.json" if job["kind"] == "compare" else "diagnosis.json")
                ).read_text()
            )
            report["stale"] = self._stale(job)
            return export_auxiliary(report, job["kind"], format)
        report = json.loads((directory / "report.json").read_text())
        report["stale"] = self._stale(job)
        cases = [
            selected
            for c in lines(directory / "cases.jsonl")
            if (selected := select_case(c, filters)) is not None
        ]
        view = filtered_view(cases, report, filters, 1, 100)
        report = {**report, **view["summary"], "issues": view["issues"], "all_items": report["item_count"]}
        return export_report(report, cases, format, filters)

    def result(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["kind"] == "ai" and (self._directory(job_id) / "diagnosis.json").is_file():
            job["report"] = json.loads((self._directory(job_id) / "diagnosis.json").read_text())
        if job["status"] == "completed":
            name = {"rules": "report.json", "compare": "comparison.json", "ai": "diagnosis.json"}[job["kind"]]
            job["report"] = json.loads((self._directory(job_id) / name).read_text())
            job["stale"] = self._stale(job)
        return job
