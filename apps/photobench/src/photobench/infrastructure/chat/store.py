"""Atomic, independent session files beside the evaluation result directory."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from photobench.infrastructure.results.split_json import manifest
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader, plain


class FileChatStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"chat-[a-f0-9]{24}", session_id):
            raise KeyError(session_id)
        return self.root / f"{session_id}.json"

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = [self.load_session(path.stem) for path in self.root.glob("chat-*.json")]
        return sorted(sessions, key=lambda s: str(s["created_at"]), reverse=True)

    def load_session(self, session_id: str) -> dict[str, Any]:
        try:
            value: dict[str, Any] = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(session_id) from exc
        return value

    def delete_session(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except FileNotFoundError as exc:
            raise KeyError(session_id) from exc
        shutil.rmtree(self.root / session_id, ignore_errors=True)

    def _turn_dir(self, session_id: str, index: int) -> Path:
        self._path(session_id)
        if index < 0:
            raise KeyError(index)
        return self.root / session_id / str(index)

    def save_turn(self, session_id: str, index: int, item: dict[str, Any]) -> None:
        directory = self._turn_dir(session_id, index)
        writer = SplitResultWriter(directory)
        try:
            writer.write(
                directory,
                {
                    "run_id": session_id,
                    "status": item.get("execution_status"),
                    "qa_count": 1,
                    "summary": {},
                    "items": [item],
                },
            )
        finally:
            writer.close()

    def load_turn(self, session_id: str, index: int) -> dict[str, Any] | None:
        directory = self._turn_dir(session_id, index)
        if manifest(directory) is None:
            return None
        reader = SplitRunReader(directory, directory)
        try:
            result = reader.core(0, preview=True)
            # A selected chat turn returns its full answer; only trace IO stays lazy.
            saved = reader.item_record(0)
            for key in ("answer", "final_answer"):
                if key in saved:
                    result[key] = plain(saved[key])
        finally:
            reader.reader.close()
        result["index"] = index
        result["trace_storage"]["base_url"] = f"/chat/sessions/{session_id}/turns/{index}"
        return result

    def read_turn_trace(
        self, session_id: str, index: int, *, revision: str = "", node_id: str = "", **options: Any
    ) -> dict[str, Any]:
        directory = self._turn_dir(session_id, index)
        reader = SplitRunReader(directory, directory, revision)
        try:
            if node_id:
                if options.pop("view", "content") == "timing":
                    return reader.timing(0, node_id)
                return reader.content(0, node_id, **options)
            if "path" in options:
                return reader.item_content(0, **options)
            return reader.nodes(0, **options)
        finally:
            reader.reader.close()

    def save_session(self, session: dict[str, Any]) -> None:
        path = self._path(str(session["session_id"]))
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump({k: v for k, v in session.items() if k != "items"}, handle, ensure_ascii=False)
                handle.flush()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
