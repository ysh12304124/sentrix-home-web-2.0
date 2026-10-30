"""遥测归因的“当前 run 用了哪些本机服务”配置。

整机/整卡始终全量采样；这份配置只决定 product / auxiliary / main 的归属。
唯一来源是 ``runtime_connection.json``；不再有第二份遥测配置文件。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

RUNTIME_CONNECTION_DEFAULT = "apps/photobench/config/runtime_connection.json"


def _local_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1"}
    with contextlib.suppress(OSError):
        hosts.update(socket.gethostbyname_ex(socket.gethostname())[2])
    with contextlib.suppress(OSError):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            hosts.add(probe.getsockname()[0])
        finally:
            probe.close()
    return hosts


def _url_port(value: Any) -> int | None:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _from_runtime_connection(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """runtime_connection 是新版的唯一来源；把扁平连接字段映射成监控清单。"""
    model_url = str(raw.get("model_base_url") or "").strip()
    embedding = raw.get("embedding")
    if not model_url and not isinstance(embedding, Mapping):
        return None
    host = (urlparse(model_url).hostname or "").lower() if model_url else ""
    port = _url_port(model_url)
    local = bool(host) and host in _local_hosts()
    sentrix_url = str(raw.get("sentrix_url") or "").strip()
    sentrix_port = _url_port(sentrix_url)
    embedding_scope = dict(embedding or {})
    mode = str(embedding_scope.get("mode") or "")
    profiles = dict(embedding_scope.get("profiles") or {})
    profile = dict(profiles.get(mode) or {})
    # 端口从实际 URL 推导，避免 profiles.ports 与 text_url/image_url 各写一份后漂移。
    if profile.get("local") is not False and mode == "unified_multimodal":
        wemm_port = _url_port(embedding_scope.get("wemm_url"))
        if wemm_port:
            profile["ports"] = [wemm_port]
    elif profile.get("local") is not False and mode == "split_text_image":
        ports = [
            port
            for port in (
                _url_port(embedding_scope.get("text_url")),
                _url_port(embedding_scope.get("image_url")),
            )
            if port
        ]
        if ports:
            profile["ports"] = ports
    urls = (
        [embedding_scope.get("wemm_url")]
        if mode == "unified_multimodal"
        else [embedding_scope.get("text_url"), embedding_scope.get("image_url")]
    )
    configured_hosts = [(urlparse(str(url or "")).hostname or "").lower() for url in urls if url]
    if configured_hosts and not all(host in _local_hosts() for host in configured_hosts):
        profile["local"] = False
        profile["ports"] = []
    if mode:
        profiles[mode] = profile
        embedding_scope["profiles"] = profiles
    return {
        "main_model": {
            "base_url": model_url,
            "model": str(raw.get("endpoint_model") or ""),
            "local": local,
            "ports": [port] if local and port else [],
            # 端口已足够；"llama-server" 同时是 8101 bge 的命令，不能再当主模型。
            "keywords": [],
        },
        "embedding": embedding_scope,
        "sentrix": {
            "url": sentrix_url,
            "ports": [sentrix_port]
            if sentrix_port and urlparse(sentrix_url).hostname in _local_hosts()
            else [],
            "local": urlparse(sentrix_url).hostname in _local_hosts(),
        },
        "product": [
            {
                **item,
                "ports": [sentrix_port]
                if sentrix_port and urlparse(sentrix_url).hostname in _local_hosts()
                else [],
                "local": urlparse(sentrix_url).hostname in _local_hosts(),
            }
            if isinstance(item, dict) and item.get("id") == "sentrix_api"
            else item
            for item in raw.get("product") or []
            if not (isinstance(item, dict) and item.get("id") == "photobench")
        ],
    }


def services_from_connection(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a frozen connection once when constructing a run's adapters."""
    return _from_runtime_connection(raw) or {}


def load_telemetry_services() -> dict[str, Any] | None:
    # 唯一来源：runtime_connection.json。没有第二份遥测配置文件。
    path = (
        Path(os.environ.get("PHOTOBENCH_CONFIG_ROOT") or Path(RUNTIME_CONNECTION_DEFAULT).parent)
        / "runtime_connection.json"
    )
    legacy = os.environ.get("PHOTOBENCH_RUNTIME_CONNECTION") or os.environ.get("PHOTOBENCH_RUNTIME_SERVICES")
    if legacy and not os.environ.get("PHOTOBENCH_CONFIG_ROOT"):
        path = Path(legacy)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _from_runtime_connection(raw)


def matches_service(row: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    if spec.get("local") is False:
        return False
    pids = {str(pid) for pid in spec.get("pids") or []}
    if pids:
        return str(row.get("pid")) in pids
    ports = {str(port) for port in spec.get("ports") or []}
    row_ports = {str(port) for port in row.get("listening_ports") or []}
    if ports:
        return bool(ports & row_ports)
    identity = str(row.get("identity") or "").lower()
    return any(keyword and str(keyword).lower() in identity for keyword in spec.get("keywords") or [])


def service_rows(rows: Sequence[Mapping[str, Any]], spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = {int(row["pid"]) for row in rows if matches_service(row, spec)}
    while True:
        children = {int(row["pid"]) for row in rows if row.get("ppid") in selected}
        expanded = selected | children
        if expanded == selected:
            break
        selected = expanded
    return [dict(row) for row in rows if int(row["pid"]) in selected]


def dedupe_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        pid = int(row["pid"])
        if pid in seen:
            continue
        seen.add(pid)
        out.append(dict(row))
    return out


__all__ = [
    "RUNTIME_CONNECTION_DEFAULT",
    "dedupe_rows",
    "load_telemetry_services",
    "matches_service",
    "service_rows",
]


def service_resolved(rows: list[dict[str, Any]], spec: Mapping[str, Any]) -> bool:
    """All configured listeners must be observed before a role total is complete."""
    if spec.get("local") is False or spec.get("required") is False:
        return True
    expected = {str(port) for port in spec.get("ports") or []}
    if expected:
        observed = {str(port) for row in rows for port in row.get("listening_ports") or []}
        return expected <= observed
    if spec.get("pids") or spec.get("keywords"):
        return bool(service_rows(rows, spec))
    return True
