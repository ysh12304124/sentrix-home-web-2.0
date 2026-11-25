"""File result adapters with additive, generation-based atomic snapshot commits.

Legacy run.json/results.jsonl remain readable. New writes publish a single commit
pointer; root files are compatible exports, not the authoritative transaction.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from photobench.domain.datasets.qa_taxonomy import snapshot_metadata
from photobench.domain.runs.analysis import effective_summary, run_list_entry
from photobench.domain.runs.frozen_trace import TRACE_TYPES
from photobench.domain.runs.task_history import execution_metadata
from photobench.infrastructure.results.encoding import open_snapshot, read_snapshot, snapshot_file
from photobench.infrastructure.results.file_copy import copy_export

if TYPE_CHECKING:
    from photobench.infrastructure.results.split_store import SplitRunReader

log = logging.getLogger(__name__)
# Fixed stripes bound lock memory, including deleted and never-seen run IDs.
_LOCKS = tuple(threading.RLock() for _ in range(64))


def run_lock(run_dir: Path) -> threading.RLock:
    return _LOCKS[hash(str(run_dir.resolve())) % len(_LOCKS)]


def committed_path(run_dir: Path, name: str) -> Path:
    """Call under run_lock. One pointer selects an immutable, complete generation."""
    pointer = run_dir / "snapshot-commit.json"
    if not pointer.exists():
        return run_dir / name
    generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
    if not isinstance(generation, str) or len(generation) != 32 or not generation.isalnum():
        raise ValueError("invalid snapshot generation")
    return snapshot_file(run_dir / ".snapshots" / generation, name)


def _durable_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        # GENERIC_READ|WRITE, shared read/write/delete, OPEN_EXISTING,
        # FILE_FLAG_BACKUP_SEMANTICS: obtain a real directory handle.
        handle = kernel.CreateFileW(str(path.resolve()), 0xC0000000, 7, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.FlushFileBuffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_result_pair(
    stage: Path,
    state: dict[str, Any],
    compressed: bool = False,
    trace_cache: dict[int, tuple[Any, bytes]] | None = None,
) -> None:
    """Encode each answer once for the complete JSON and line-oriented export.

    Large traces used to be encoded twice and joined into two additional whole
    run strings. Keep the same file contract with memory bounded by one item.
    """
    suffix = ".gz" if compressed else ""
    run_path, results_path = stage / ("run.json" + suffix), stage / ("results.jsonl" + suffix)

    def encode(text: str) -> bytes:
        raw = text.encode("utf-8")
        return gzip.compress(raw, compresslevel=1, mtime=0) if compressed else raw

    used: set[int] = set()

    def field_bytes(value: Any) -> bytes:
        if isinstance(value, TRACE_TYPES) and trace_cache is not None:
            identity = id(value)
            used.add(identity)
            cached = trace_cache.get(identity)
            if cached is None:
                cached = (value, encode(json.dumps(value, ensure_ascii=False)))
                trace_cache[identity] = cached
            return cached[1]
        return encode(json.dumps(value, ensure_ascii=False))

    def fragments(row: dict[str, Any]) -> Iterator[bytes]:
        yield encode("{")
        for index, (key, child) in enumerate(row.items()):
            if index:
                yield encode(", ")
            yield encode(json.dumps(key, ensure_ascii=False) + ": ")
            if key == "runtime_turns" and isinstance(child, list):
                yield encode("[")
                for turn_index, turn in enumerate(child):
                    if turn_index:
                        yield encode(", ")
                    yield from fragments(turn)
                yield encode("]")
            else:
                yield field_bytes(child)
        yield encode("}")

    # Concatenated gzip members decode to the same JSON. Reuse each encoded
    # answer in both files, including its compression work.
    with run_path.open("wb") as run, results_path.open("wb") as results:
        results.write(encode(""))
        run.write(encode("{"))
        for index, (key, value) in enumerate(state.items()):
            if index:
                run.write(encode(", "))
            run.write(encode(json.dumps(key, ensure_ascii=False) + ": "))
            if key != "items" or value is None:
                run.write(encode(json.dumps(value, ensure_ascii=False)))
                continue
            run.write(encode("["))
            for row_index, row in enumerate(value or []):
                if row_index:
                    run.write(encode(", "))
                chunks = (
                    fragments(row)
                    if trace_cache is not None
                    else (encode(json.dumps(row, ensure_ascii=False)),)
                )
                for encoded in chunks:
                    run.write(encoded)
                    results.write(encoded)
                results.write(encode("\n"))
            run.write(encode("]"))
        run.write(encode("}"))
    if trace_cache is not None:
        for identity in trace_cache.keys() - used:
            del trace_cache[identity]
    # Close the compressors first so the gzip footer is durable as well.
    for path in (run_path, results_path):
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())


def _overview(state: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in state.items()
        if key not in {"items", "scoring_results", "diagnostic_results"}
    }
    if isinstance(result.get("qa_snapshot"), dict):
        result["qa_snapshot"] = snapshot_metadata(result["qa_snapshot"])
    result["scoring_batches"] = [
        {k: v for k, v in b.items() if k not in {"materials", "targets"}}
        for b in state.get("scoring_batches") or []
    ]
    result["item_count"] = len(state.get("items") or [])
    result["summary"] = effective_summary(state)
    return result


def read_entry(run_dir: Path) -> dict[str, Any]:
    """Cheap committed metadata; legacy derived index is invalidated by file identity."""
    from photobench.infrastructure.results.split_json import MANIFEST
    from photobench.infrastructure.results.split_store import SplitRunReader

    if (run_dir / MANIFEST).is_file():
        return SplitRunReader(run_dir.parent, run_dir).entry()
    with run_lock(run_dir):
        metadata = committed_path(run_dir, "metadata.json")
        if metadata.is_file():
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            entry = payload["entry"]
            if not isinstance(entry, dict):
                raise ValueError("invalid committed entry")
            # 已提交元数据中的旧列表投影可能缺少题型汇总；从同一快照概览补齐。
            evaluation = (payload.get("overview", {}).get("summary") or {}).get("evaluation_summary")
            if evaluation is not None:
                entry.setdefault("summary", {})["evaluation_summary"] = evaluation
            scoring = (entry.get("summary") or {}).get("scoring") or {}
            if os.environ.get("PHOTOBENCH_SPLIT_RESULTS") != "1" and any(
                batch.get("profile_id") == "atm_qs_v1" and "tool_list_qs" not in batch
                for batch in (scoring.get("batches") or {}).values()
            ):
                from photobench.domain.evaluation.profiles import scoring_summary

                state = read_snapshot(committed_path(run_dir, "run.json"))
                entry.setdefault("summary", {})["scoring"] = scoring_summary(state)
            entry.update(execution_metadata(payload.get("overview") or {}))
            return entry
        path = run_dir / "run.json"
        stat = path.stat()
        signature = [4, stat.st_ino, stat.st_size, stat.st_mtime_ns]
        index = run_dir / ".photobench-read-index.json"
        try:
            cached = json.loads(index.read_text(encoding="utf-8"))
            if cached.get("signature") == signature and isinstance(cached.get("entry"), dict):
                entry = cached["entry"]
                assert isinstance(entry, dict)
                return entry
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if os.environ.get("PHOTOBENCH_SPLIT_RESULTS") == "1":
            from photobench.infrastructure.results.split_store import ReadIndexRequired

            raise ReadIndexRequired(run_dir.name)
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not state.get("run_id"):
            raise ValueError("missing run_id")
        entry = run_list_entry(state)
        # Derived only. Read-only historical directories remain readable.
        temporary = index.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"signature": signature, "entry": entry}, ensure_ascii=False), encoding="utf-8"
            )
            temporary.replace(index)
        except OSError:
            log.warning("Could not cache historical run entry: %s", run_dir, exc_info=True)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        return entry


def write_committed_snapshot(
    run_dir: Path,
    state: dict[str, Any],
    *,
    compressed: bool = False,
    trace_cache: dict[int, tuple[Any, bytes]] | None = None,
) -> None:
    """Stage/fsync the complete pair, then atomically publish one commit pointer.

    Root files are legacy exports. Their repair never changes the committed
    revision. Readers and recovery use the pointer, including after a crash.
    """
    with run_lock(run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        stages = run_dir / ".snapshots"
        stage = stages / generation
        stage.mkdir(parents=True)
        pointer = run_dir / "snapshot-commit.json"
        previous = json.loads(pointer.read_text())["generation"] if pointer.exists() else None
        temporary = run_dir / "snapshot-commit.tmp"
        try:
            _write_result_pair(stage, state, compressed, trace_cache)
            _durable_text(
                stage / "metadata.json",
                json.dumps(
                    {"entry": run_list_entry(state), "overview": _overview(state)}, ensure_ascii=False
                ),
            )
            _sync_directory(stage)
            _sync_directory(stages)
            _durable_text(
                temporary, json.dumps({"version": 2 if compressed else 1, "generation": generation})
            )
            temporary.replace(pointer)
        except BaseException:
            temporary.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)
            raise
        # From here the commit is published; export/cleanup errors must not
        # pretend that the authoritative write was rolled back.
        # Failure to sync the published pointer is a durability failure: retain
        # the selected generation, but let the caller report uncertain persistence.
        _sync_directory(run_dir)
        try:
            for name in ("run.json", "results.jsonl"):
                source = snapshot_file(stage, name)
                export = run_dir / (source.name + ".tmp")
                copy_export(source, export)
                export.replace(run_dir / source.name)
        except OSError:
            log.exception(
                "Snapshot %s committed; legacy export/cleanup needs repair: %s", generation, run_dir
            )
        finally:
            # Export failure must not bypass rolling-checkpoint cleanup forever.
            try:
                obsolete = list(stages.iterdir())
            except OSError:
                obsolete = []
                log.warning("Could not list obsolete checkpoints: %s", stages, exc_info=True)
            for old in obsolete:
                if old.is_dir() and old.name not in {generation, previous}:
                    try:
                        shutil.rmtree(old)
                    except OSError:
                        log.warning("Could not clean obsolete checkpoint: %s", old, exc_info=True)
            for name in ("run.json.tmp", "results.jsonl.tmp", "run.json.gz.tmp", "results.jsonl.gz.tmp"):
                with suppress(OSError):
                    (run_dir / name).unlink(missing_ok=True)


class FileRunStore:
    def __init__(self, results_root: Path) -> None:
        self._root = results_root

    def _run_dir(self, run_id: str) -> Path:
        """run_id 直接来自 URL，所以先挡住路径穿越。

        1.0 是 ``self.results_root / run_id``（``:4974``），``run_id`` 里带 ``..``
        就能读到别处的 JSON。这里把非法形状当成「这个 run 不存在」，与 1.0 对一个
        普通未知 run_id 的结局（404）一致。
        """
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise KeyError(run_id)
        return self._root / run_id

    def save_process_diagnostics(self, run_id: str, diagnostics: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        path = self._root / ".process-diagnostics"
        path.mkdir(exist_ok=True)
        # One immutable derived input per selection/version; switching batches cannot overwrite it.
        from photobench.domain.error_analysis.process import fingerprint

        identity = fingerprint(
            [
                diagnostics.get("rule_version"),
                diagnostics.get("scoring_context"),
                diagnostics.get("source_fingerprint"),
            ]
        )
        target = path / f"{run_id}.{identity}.json"
        with run_lock(run_dir):
            pending = target.with_suffix(".tmp")
            _durable_text(pending, json.dumps(diagnostics, ensure_ascii=False))
            os.replace(pending, target)

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        if not self._root.exists():
            return
        for run_dir in sorted(self._root.iterdir()):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            try:
                yield read_entry(run_dir)
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("Could not index run: %s", run_dir, exc_info=True)

    def load_entry(self, run_id: str) -> dict[str, Any]:
        directory = self._run_dir(run_id)
        if not directory.is_dir():
            raise KeyError(run_id)
        return read_entry(directory)

    def load_state(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        from photobench.infrastructure.results.resume_quarantine import load_resume_quarantine_state

        reduced = load_resume_quarantine_state(run_dir, run_id)
        if reduced is not None:
            return reduced
        from photobench.infrastructure.results.split_json import MANIFEST
        from photobench.infrastructure.results.split_store import SplitRunReader

        if (run_dir / MANIFEST).is_file():
            return SplitRunReader(self._root, run_dir).state()
        if os.environ.get("PHOTOBENCH_SPLIT_RESULTS") == "1":
            from photobench.infrastructure.results.split_store import ReadIndexRequired

            raise ReadIndexRequired(run_id)
        with run_lock(run_dir):
            path = committed_path(run_dir, "run.json")
            if not path.is_file():
                raise KeyError(run_id)
            # Detach the immutable generation bytes before releasing the lock.
            # Parsing a large trace must not block the next durable commit.
            with open_snapshot(path) as handle:
                text = handle.read()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise KeyError(run_id)
        return value

    def read_video_comparison(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / "video-objective-comparison.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid video comparison document")
        return value

    def load_overview(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        from photobench.infrastructure.results.split_json import MANIFEST
        from photobench.infrastructure.results.split_store import SplitRunReader

        if (run_dir / MANIFEST).is_file():
            overview = SplitRunReader(self._root, run_dir).overview()
            return self._attach_live(run_dir, overview)
        with run_lock(run_dir):
            path = committed_path(run_dir, "metadata.json")
            if path.is_file():
                overview = json.loads(path.read_text(encoding="utf-8"))["overview"]
                if not isinstance(overview, dict):
                    raise ValueError("invalid committed overview")
                if "scoring" not in (overview.get("summary") or {}):
                    overview = _overview(self.load_state(run_id))
            else:
                overview = _overview(self.load_state(run_id))
            live_path = run_dir / "telemetry-live.json"
            if not overview.get("finished_at") and overview.get("status") in {
                "pending",
                "running",
                "cancelling",
            }:
                try:
                    view = json.loads(live_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    view = None
                if (
                    isinstance(view, dict)
                    and view.get("run_id") == run_id
                    and view.get("started_at")
                    and view["started_at"] == overview.get("started_at")
                ):
                    overview["telemetry_live"] = view["telemetry_live"]
                    phases = overview.setdefault("phases", {})
                    if (phases.get("gpu_metrics") or {}).get("status") not in {"done", "skipped"}:
                        phases["gpu_metrics"] = view["gpu_metrics"]
                    if view.get("llama_server_args"):
                        overview["llama_server_args"] = view["llama_server_args"]
            return {**overview, "read_revision": self.revision(run_id)}

    def _attach_live(self, run_dir: Path, overview: dict[str, Any]) -> dict[str, Any]:
        if not overview.get("finished_at") and overview.get("status") in {"pending", "running", "cancelling"}:
            try:
                live = json.loads((run_dir / "telemetry-live.json").read_text())
            except FileNotFoundError:
                live = None
            if (
                isinstance(live, dict)
                and live.get("started_at") == overview.get("started_at")
                and live.get("run_id") == run_dir.name
            ):
                overview["telemetry_live"] = live["telemetry_live"]
                if (overview.get("phases", {}).get("gpu_metrics") or {}).get("status") not in {
                    "done",
                    "skipped",
                }:
                    overview.setdefault("phases", {})["gpu_metrics"] = live["gpu_metrics"]
        return overview

    def use_read_index(self, run_id: str) -> bool:
        from photobench.infrastructure.results.split_json import MANIFEST

        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        return (run_dir / MANIFEST).is_file()

    def _split_reader(self, run_id: str, revision: str = "") -> SplitRunReader:
        from photobench.infrastructure.results.split_store import SplitRunReader

        return SplitRunReader(self._root, self._run_dir(run_id), revision)

    def load_selected_overview(
        self, run_id: str, profile: str, batch: str, *, revision: str = ""
    ) -> dict[str, Any]:
        return self._attach_live(
            self._run_dir(run_id), self._split_reader(run_id, revision).overview(profile, batch)
        )

    def load_item_index(
        self, run_id: str, profile: str, batch: str, *, revision: str = ""
    ) -> list[dict[str, Any]]:
        return self._split_reader(run_id, revision).index(profile, batch)

    def load_indexed_state(self, run_id: str, *, revision: str = "") -> dict[str, Any]:
        return self._split_reader(run_id, revision).state()

    def load_item_core(
        self, run_id: str, index: int, profile: str, batch: str, *, revision: str = "", preview: bool = False
    ) -> dict[str, Any]:
        return self._split_reader(run_id, revision).core(index, profile, batch, preview=preview)

    def load_item_record(self, run_id: str, index: int, *, revision: str = "") -> dict[str, Any]:
        return self._split_reader(run_id, revision).item_record(index)

    def load_item_answer_fingerprint(self, run_id: str, index: int, *, revision: str = "") -> str:
        return self._split_reader(run_id, revision).item_answer_fingerprint(index)

    def load_trace_nodes(self, run_id: str, index: int, **options: Any) -> dict[str, Any]:
        return self._split_reader(run_id, options.pop("revision", "")).nodes(index, **options)

    def load_trace_content(self, run_id: str, index: int, node_id: str, **options: Any) -> dict[str, Any]:
        reader = self._split_reader(run_id, options.pop("revision", ""))
        if options.pop("view", "content") == "timing":
            return reader.timing(index, node_id)
        return reader.content(index, node_id, **options)

    def load_item_content(self, run_id: str, index: int, **options: Any) -> dict[str, Any]:
        return self._split_reader(run_id, options.pop("revision", "")).item_content(index, **options)

    def revision(self, run_id: str) -> str:
        run_dir = self._run_dir(run_id)
        from photobench.infrastructure.results.split_json import manifest

        selected = manifest(run_dir)
        if selected is not None:
            return str(selected["revision"])
        with run_lock(run_dir):
            path = committed_path(run_dir, "run.json")
            try:
                stat = path.stat()
            except FileNotFoundError as exc:
                raise KeyError(run_id) from exc
            return f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"

    def telemetry_revision(self, run_id: str) -> str:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        signatures = []
        for name in ("gpu_samples.jsonl", "telemetry-live.json"):
            try:
                stat = (run_dir / name).stat()
            except FileNotFoundError:
                signatures.append("absent")
            else:
                signatures.append(f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(signatures)

    def read_reviews(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        path = run_dir / "reviews.json"
        if not path.is_file():
            return {"reviews": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return {
                "reviews": value.get("reviews") or {},
                "updated_at": value.get("updated_at"),
                **({"scoring_reviews": value["scoring_reviews"]} if "scoring_reviews" in value else {}),
            }
        except (OSError, json.JSONDecodeError, AttributeError):
            return {"reviews": {}}

    def read_gpu_sample_lines(self, run_id: str) -> Iterator[str]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        path = run_dir / "gpu_samples.jsonl"
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as handle:
            yield from handle


class FileResultWriter:
    """Write complete checkpoints and append raw telemetry.

    A checkpoint still contains all items. Trace/event storage separation is a
    later change; raw history is never silently migrated or shortened here.
    """

    def __init__(self, results_root: Path) -> None:
        self._root = results_root
        self._split_writer: Any = None
        self._compressed = os.environ.get("PHOTOBENCH_COMPRESS_SNAPSHOTS") == "1"
        self._trace_caches: dict[str, dict[int, tuple[Any, bytes]]] | None = (
            {} if os.environ.get("PHOTOBENCH_REUSE_TRACE_CHECKPOINTS") == "1" else None
        )

    def _run_dir(self, run_id: str) -> Path:
        """与 ``FileRunStore._run_dir`` 同一条路径穿越拦截。写入侧更需要它：
        读到一个越界的路径最多是读错文件，写进去是**覆盖别人的文件**。"""
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise KeyError(run_id)
        return self._root / run_id

    def write_snapshot(self, run_id: str, state: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        from photobench.infrastructure.results.split_json import MANIFEST
        from photobench.infrastructure.results.split_store import SplitResultWriter

        if os.environ.get("PHOTOBENCH_SPLIT_RESULTS") == "1" or (run_dir / MANIFEST).is_file():
            if self._split_writer is None:
                self._split_writer = SplitResultWriter(self._root)
            self._split_writer.write(run_dir, state)
            return
        cache = self._trace_caches.setdefault(run_id, {}) if self._trace_caches is not None else None
        write_committed_snapshot(run_dir, state, compressed=self._compressed, trace_cache=cache)
        if self._trace_caches is not None and state.get("status") in {
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
            "interrupted",
        }:
            self._trace_caches.pop(run_id, None)

    def compact_committed_traces(self, run_id: str, current: dict[str, Any], stored: dict[str, Any]) -> None:
        from photobench.domain.runs.frozen_trace import ITEM_TRACE_FIELDS, TURN_TRACE_FIELDS
        from photobench.infrastructure.results.split_json import MANIFEST
        from photobench.infrastructure.results.split_store import SplitRunReader

        directory = self._run_dir(run_id)
        if not (directory / MANIFEST).is_file():
            return
        reader = SplitRunReader(self._root, directory)
        live = {str(item.get("qa_id")): item for item in current.get("items") or []}
        for index, saved in enumerate(stored.get("items") or []):
            item = live.get(str(saved.get("qa_id")))
            if item is None:
                continue
            lazy = reader.item_record(index)
            for key in ITEM_TRACE_FIELDS:
                if key in saved and item.get(key) is saved[key]:
                    item[key] = saved[key] = lazy[key]
            for position, turn in enumerate(saved.get("runtime_turns") or []):
                active_turns = item.get("runtime_turns") or []
                if position >= len(active_turns):
                    continue
                active = active_turns[position]
                lazy_turn = lazy["runtime_turns"][position]
                for key in TURN_TRACE_FIELDS:
                    if key in turn and active.get(key) is turn[key]:
                        active[key] = turn[key] = lazy_turn[key]

    def write_telemetry_view(self, run_id: str, view: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        with run_lock(run_dir):
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "telemetry-live.json"
            temporary = run_dir / "telemetry-live.tmp"
            try:
                temporary.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

    def reset_gpu_samples(self, run_id: str) -> None:
        path = self._run_dir(run_id) / "gpu_samples.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        (path.parent / "telemetry-live.json").unlink(missing_ok=True)

    def append_gpu_sample(self, run_id: str, sample: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "gpu_samples.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

    def save_reviews(self, run_id: str, stored: dict[str, Any]) -> None:
        """原子写 ``reviews.json``（1.0 ``save_reviews`` 的 ``atomic_json``）。"""
        run_dir = self._run_dir(run_id)
        with run_lock(run_dir):
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "reviews.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)

    def delete_run(self, run_id: str) -> None:
        """删掉整个 run 目录（1.0 ``:7010``）。

        ``_run_dir`` 已经挡住 ``..`` 与分隔符；这里再加一道「解析后的路径必须真的在
        ``results_root`` 之下」——符号链接也能把 ``results/x`` 指到别处，而 1.0 的
        ``self.results_root in run_dir.parents`` 正是这道校验。
        """
        run_dir = self._run_dir(run_id).resolve()
        root = self._root.resolve()
        if root not in run_dir.parents:
            raise KeyError(run_id)
        with run_lock(run_dir):
            if run_dir.exists():
                shutil.rmtree(run_dir)
