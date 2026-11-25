"""Persistent chat provenance and serial product turns, sharing model preparation with retests."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from photobench.application.run_execution.service import RunExecution, RunRequest
from photobench.application.runs.suites import RunSuites
from photobench.application.spaces.service import MemorySpaces
from photobench.domain.chat.ports import ChatStore
from photobench.domain.chat.projection import turn_item
from photobench.domain.datasets.ports import AlbumSource
from photobench.domain.datasets.qa_taxonomy import qa_fingerprint
from photobench.domain.errors import ChatBusyError, InvalidRequestError
from photobench.domain.runs.analysis import now_iso
from photobench.domain.runs.execution import (
    RunCancelledError,
    index_assets_by_name,
    retrieved_candidate_asset_ids,
    selected_asset_ids,
)
from photobench.domain.runtime.ports import RunRuntimeSnapshot
from photobench.domain.sentrix.ports import InteractiveSentrixClient

log = logging.getLogger(__name__)
ACTIVE = {"preparing", "running", "cancelling"}
TURN_ACTIVE = {"pending", "running"}


class ChatService:
    def __init__(
        self,
        *,
        store: ChatStore,
        suites: RunSuites,
        albums: AlbumSource,
        spaces: MemorySpaces,
        client_for: Callable[[str], InteractiveSentrixClient],
        execution_for: Callable[[RunRequest], RunExecution],
    ) -> None:
        self.store, self.suites, self.albums, self.spaces = store, suites, albums, spaces
        self.client_for, self.execution_for = client_for, execution_for
        self.lock = threading.RLock()
        self.workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.turn_reads = tuple(threading.Lock() for _ in range(64))

    def list_chats(self) -> dict[str, Any]:
        with self.lock:
            sessions = self.store.list_sessions()
        return {
            "items": [
                {
                    k: s.get(k)
                    for k in (
                        "session_id",
                        "created_at",
                        "title",
                        "scope_id",
                        "model_name",
                        "status",
                        "error",
                        "album_id",
                    )
                }
                for s in sessions
            ]
        }

    def create_chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = str(payload.get("scope_id") or "")
        spaces = self.spaces.list_spaces()
        if not any(s.get("id") == scope for s in spaces["spaces"]):
            raise InvalidRequestError("所选相册不存在")
        base: dict[str, Any] = next((s for s in spaces["reuse_bases"] if s.get("scope_id") == scope), {})
        if base and not base.get("ready"):
            raise InvalidRequestError("所选相册尚未完成构建")
        request = self.suites.interactive_models.resolve(
            payload, album_id=str(base.get("album_id") or ""), qa_set="", scope_id=scope, mode="reuse"
        )
        snapshot = request.runtime_snapshot
        assert snapshot is not None
        embedding = (base.get("scope_build") or {}).get("embedding")
        if embedding is not None and embedding != snapshot.connection().get("embedding"):
            raise InvalidRequestError("嵌入配置与所选相册不一致")
        session: dict[str, Any] = {
            "session_id": "chat-" + uuid.uuid4().hex[:24],
            "created_at": now_iso(),
            "title": "新对话",
            "scope_id": scope,
            "album_id": base.get("album_id") or "",
            "status": "ready",
            "conversation_id": None,
            "turns": [],
            "source": None,
            "model_profile": request.model_profile,
            "model_base_url": request.model_base_url,
            "manager_url": request.manager_url,
            "endpoint_model": request.endpoint_model,
            "runtime_snapshot": snapshot.connection(),
            "build_revision": snapshot.build_revision,
            "turn_timeout": request.turn_timeout,
            "reasoning": request.reasoning,
        }
        if payload.get("qa_id"):
            album_id, qa_set = str(payload.get("album_id") or ""), str(payload.get("qa_set") or "")
            if not session["album_id"] or session["album_id"] != album_id:
                raise InvalidRequestError("题集与所选相册不对应")
            try:
                row = self.albums.qa_question(album_id, qa_set, str(payload["qa_id"]))
            except KeyError as exc:
                raise InvalidRequestError("题目不存在") from exc
            session["source"] = {
                "album_id": album_id,
                "qa_set": qa_set,
                "row": copy.deepcopy(row),
                "definition_sha256": qa_fingerprint([row]),
            }
            messages = [str(t.get("message") or "").strip() for t in row.get("conversation") or []] or [
                str(row["question"])
            ]
        else:
            message = str(payload.get("message") or "").strip()
            messages = [message] if message else []
        self._validate_messages(messages)
        with self.lock:
            if messages:
                self._launch(session, messages)
            else:
                self.store.save_session(session)
        return session

    @staticmethod
    def _validate_messages(messages: list[str]) -> None:
        if any(not m or len(m) > 2000 for m in messages):
            raise InvalidRequestError("每条消息需为1至2000字")

    def send_chat(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        self._validate_messages([message])
        with self.lock:
            session = self.store.load_session(session_id)
            if session["status"] in ACTIVE:
                raise InvalidRequestError("请等待当前回答结束")
            if session.get("status") == "outcome_unknown":
                raise InvalidRequestError("上次请求的执行状态尚未确认，请先刷新记录")
            reasoning = payload.get("reasoning")
            if reasoning is None:
                reasoning = session.get("reasoning") is True
            if not isinstance(reasoning, bool):
                raise InvalidRequestError("reasoning must be a boolean")
            if reasoning != (session.get("reasoning") is True):
                session["turn_timeout"] = self.suites.interactive_models.turn_timeout(reasoning)
            session["reasoning"] = reasoning
            session["runtime_snapshot"].setdefault("execution_budgets", {}).update(
                reasoning=reasoning, turn_timeout=session["turn_timeout"]
            )
            self._launch(session, [message])
        return session

    def _request(self, session: dict[str, Any]) -> RunRequest:
        return RunRequest(
            album_id=session["album_id"],
            qa_set="",
            scope_id=session["scope_id"],
            mode="reuse",
            model_profile=session["model_profile"],
            manager_url=session["manager_url"],
            model_base_url=session["model_base_url"],
            endpoint_model=session["endpoint_model"],
            sentrix_url=session["runtime_snapshot"]["sentrix_url"],
            switch_model=True,
            reasoning=session.get("reasoning") is True,
            turn_timeout=int(session.get("turn_timeout") or 900),
            runtime_snapshot=RunRuntimeSnapshot(
                json.dumps(session["runtime_snapshot"]), session["build_revision"]
            ),
        )

    def _launch(self, session: dict[str, Any], messages: list[str], *, recovery: bool = False) -> None:
        key = str(session["session_id"])
        cancel = threading.Event()
        self.suites.reserve_interactive(key, cancel, recovery=recovery)
        if not recovery:
            session.update(status="preparing", error=None, pending_messages=messages)
        try:
            self.store.save_session(session)
            thread = threading.Thread(
                target=self._worker, args=(key, cancel, recovery), name=key, daemon=True
            )
            self.workers[key] = (thread, cancel)
            thread.start()
        except Exception:
            self.workers.pop(key, None)
            self.suites.release_interactive(key)
            raise

    def _update(self, session_id: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.lock:
            session = self.store.load_session(session_id)
            mutate(session)
            self.store.save_session(session)
            return session

    def _patch(self, session_id: str, **changes: Any) -> dict[str, Any]:
        return self._update(session_id, lambda s: s.update(changes))

    def _worker(self, session_id: str, cancel: threading.Event, recovery: bool) -> None:
        session = self.store.load_session(session_id)
        client = self.client_for(session["runtime_snapshot"]["sentrix_url"])
        try:
            if not recovery:
                # Explicit new messages follow product releases; saved turns keep their provenance.
                connection = copy.deepcopy(session["runtime_snapshot"])
                revision = client.diagnostics().get("build_revision")
                if revision:
                    connection.update(product_build_revision=revision, required_build_revision=revision)
                snapshot = RunRuntimeSnapshot(json.dumps(connection), self.suites.interactive_models.revision)
                request = replace(self._request(session), runtime_snapshot=snapshot)
                prepared = self.execution_for(request).prepare_interactive_model(request, cancel)
                self._update(
                    session_id,
                    lambda s: s.update(
                        model_name=prepared.get("model_name"),
                        model_identity=prepared.get("model_identity"),
                        model_preparation=prepared.get("phases"),
                        turn_runtime_snapshot=connection,
                        turn_build_revision=snapshot.build_revision,
                    ),
                )
            if not session.get("conversation_id"):
                if recovery:
                    raise RuntimeError("服务重启时建会话尚未返回，未自动重发请求")
                cid = client.start_conversation(scope_id=session["scope_id"], title=session["title"])["id"]
                session = self._update(session_id, lambda s: s.update(conversation_id=cid))
            # A saved turn is polled after restart; generation POSTs are never replayed.
            if recovery and session.get("turns") and session["turns"][-1]["status"] in TURN_ACTIVE:
                self._await(session_id, session["turns"][-1], client, cancel)
            elif recovery and session.get("dispatching_message"):
                detail = client.get_conversation(conversation_id=session["conversation_id"])
                known = {t.get("turn_id") for t in session["turns"]}
                found = [
                    t
                    for t in detail.get("turns") or []
                    if t["id"] not in known and t.get("question") == session["dispatching_message"]
                ]
                if len(found) != 1:
                    raise RuntimeError("未能确定上次发送的回合，未自动重发")
                record = self._register_turn(session_id, session["dispatching_message"], found[0]["id"])
                self._await(session_id, record, client, cancel)
            session = self.store.load_session(session_id)
            while session.get("pending_messages"):
                if cancel.is_set():
                    raise RunCancelledError("已取消")
                message = session["pending_messages"][0]
                self._patch(session_id, dispatching_message=message, status="running")
                started = client.start_turn(
                    conversation_id=session["conversation_id"],
                    message=message,
                    include_debug=True,
                    reasoning=session.get("reasoning") is True,
                )
                record = self._register_turn(session_id, message, str(started["turn_id"]))
                self._await(session_id, record, client, cancel)
                session = self.store.load_session(session_id)
            self._patch(session_id, status="ready", error=None, poll_error=None)
        except RunCancelledError:
            self._update(session_id, lambda s: s.update(status="cancelled", pending_messages=[]))
        except Exception as exc:
            log.warning("Chat execution failed: %s (%s)", session_id, type(exc).__name__)
            current = self.store.load_session(session_id)
            unknown = bool(current.get("dispatching_message")) or any(
                t["status"] in TURN_ACTIVE for t in current["turns"]
            )
            self._patch(session_id, status="outcome_unknown" if unknown else "failed", error=str(exc))
        finally:
            with self.lock:
                if self.store.load_session(session_id)["status"] != "outcome_unknown":
                    self.suites.release_interactive(session_id)
                self.workers.pop(session_id, None)

    def _register_turn(self, session_id: str, message: str, turn_id: str) -> dict[str, Any]:
        def register(s: dict[str, Any]) -> None:
            s["turns"].append(
                {
                    "index": len(s["turns"]),
                    "turn_id": turn_id,
                    "message": message,
                    "status": "pending",
                    "runtime_snapshot": copy.deepcopy(s.get("turn_runtime_snapshot", s["runtime_snapshot"])),
                    "build_revision": s.get("turn_build_revision", s["build_revision"]),
                    "reasoning": s.get("reasoning") is True,
                    "started_at": now_iso(),
                }
            )
            s.update(
                dispatching_message=None,
                pending_messages=s["pending_messages"][1:],
                title=s["title"] if s["title"] != "新对话" else message[:50],
            )

        return dict(self._update(session_id, register)["turns"][-1])

    def _await(
        self,
        session_id: str,
        record: dict[str, Any],
        client: InteractiveSentrixClient,
        cancel: threading.Event,
    ) -> None:
        started = time.monotonic()
        cancellation_sent = False
        timeout = int(self.store.load_session(session_id).get("turn_timeout") or 900)
        while True:
            if time.monotonic() - started >= timeout:
                cancel.set()
            if cancel.is_set() and not cancellation_sent:
                client.cancel_turn(turn_id=record["turn_id"])
                cancellation_sent = True
            try:
                turn = client.get_turn(turn_id=record["turn_id"])
            except Exception as exc:
                self._patch(session_id, poll_error=str(exc))
                time.sleep(1)
                continue
            if turn["status"] not in TURN_ACTIVE:

                def finish(s: dict[str, Any], terminal: dict[str, Any] = turn) -> None:
                    current = s["turns"][record["index"]]
                    current.update(
                        status=terminal["status"],
                        finished_at=now_iso(),
                        wall_clock_ms=round((time.monotonic() - started) * 1000),
                        answer=(terminal.get("result") or {}).get("answer") or "",
                        error=terminal.get("error"),
                    )

                self._update(session_id, finish)
                # Persist one terminal trace once; session polls read only the small message file.
                try:
                    self._cache_turn(session_id, record["index"], client, turn)
                except Exception:
                    log.exception("Chat trace cache failed: %s turn %s", session_id, record["index"])
                if cancellation_sent or cancel.is_set():
                    raise RunCancelledError("已取消")
                if turn["status"] not in {"complete", "partial"}:
                    raise RuntimeError(str(turn.get("error") or turn["status"]))
                return
            cancel.wait(0.5) if not cancel.is_set() else time.sleep(0.5)

    def get_chat(self, session_id: str, *, include_items: bool = True) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        session.pop("items", None)
        if include_items:
            session["items"] = [self.get_chat_turn(session_id, i) for i in range(len(session["turns"]))]
        return session

    def _cache_turn(
        self,
        session_id: str,
        index: int,
        client: InteractiveSentrixClient,
        turn: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.turn_reads[hash((session_id, index)) % len(self.turn_reads)]:
            session = self.store.load_session(session_id)
            if index < 0 or index >= len(session["turns"]):
                raise KeyError(index)
            record = session["turns"][index]
            cached = self.store.load_turn(session_id, index)
            if cached and cached.get("execution_status") not in TURN_ACTIVE:
                return cached
            if turn is None:
                turn = client.get_turn(turn_id=record["turn_id"])
            result = turn.get("result") or {}
            identifiers = list(
                dict.fromkeys(
                    [
                        *selected_asset_ids(result),
                        *retrieved_candidate_asset_ids(result),
                        *[str(r["asset_id"]) for r in (result.get("trace") or {}).get("source_reads", [])],
                    ]
                )
            )
            with ThreadPoolExecutor(max_workers=4) as pool:
                assets = [a for a in pool.map(lambda key: client.get_asset(asset_id=key), identifiers) if a]
                parents = list(
                    dict.fromkeys(
                        str(a["parent_asset_id"])
                        for a in assets
                        if a.get("parent_asset_id") and a["parent_asset_id"] not in identifiers
                    )
                )
                assets.extend(a for a in pool.map(lambda key: client.get_asset(asset_id=key), parents) if a)
            item = turn_item(record, turn, index_assets_by_name(assets))
            self.store.save_turn(session_id, index, item)
            saved = self.store.load_turn(session_id, index)
            assert saved is not None
            return saved

    def get_chat_turn(self, session_id: str, index: int) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if index < 0 or index >= len(session["turns"]):
            raise KeyError(index)
        client = self.client_for(session["runtime_snapshot"]["sentrix_url"])
        return self._cache_turn(session_id, index, client)

    def get_chat_trace(self, session_id: str, index: int, **options: Any) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if index < 0 or index >= len(session["turns"]):
            raise KeyError(index)
        return self.store.read_turn_trace(session_id, index, **options)

    def cancel_chat(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.store.load_session(session_id)
            worker = self.workers.get(session_id)
            if worker:
                worker[1].set()
                self._update(session_id, lambda s: s.update(status="cancelling"))
            elif session["status"] == "outcome_unknown":
                self._launch(session, [], recovery=True)
                self.workers[session_id][1].set()
        return {
            "session_id": session_id,
            "cancel_requested": bool(worker) or session["status"] == "outcome_unknown",
        }

    def delete_chat(self, session_id: str) -> None:
        with self.lock:
            session = self.store.load_session(session_id)
            if session_id in self.workers or session["status"] in ACTIVE | {"outcome_unknown"}:
                raise ChatBusyError("请先停止回答，等待回合结束后再删除对话")
            conversation_id = session.get("conversation_id")
            if conversation_id:
                self.client_for(session["runtime_snapshot"]["sentrix_url"]).delete_conversation(
                    conversation_id=conversation_id, timeout=5
                )
            self.store.delete_session(session_id)

    def recover_chats(self) -> None:
        for session in self.store.list_sessions():
            if session["status"] in ACTIVE or session["status"] == "outcome_unknown":
                with self.lock:
                    self._launch(session, [], recovery=True)
