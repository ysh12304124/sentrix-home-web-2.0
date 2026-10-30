#!/usr/bin/env python3
"""Strict T006 preflight and independent acceptance, separate from production run_valid."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypeGuard
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.deploy.photobench_release import (  # noqa: E402
    digest,
    listener,
    local_url,
    process_environment,
    read_json,
    verify,
    write_json,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Local dependencies must not redirect")


def http_value(url: str, body: dict[str, Any] | None = None, timeout: int = 180) -> Any:
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)


def http(url: str, body: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    value = http_value(url, body, timeout)
    require(isinstance(value, dict), "Expected a JSON object")
    result: dict[str, Any] = value
    return result


def probe_text_embedding(url: str, unified: bool) -> tuple[dict[str, Any], str]:
    try:
        return http(url + ("/embed_text" if unified else "/embed"), {"text": "测试图像"}), "sidecar"
    except HTTPError as error:
        if unified or error.code != 404:
            raise
    try:
        result = http(url + "/v1/embeddings", {"input": "测试图像"})
    except HTTPError as error:
        if error.code != 400:
            raise
        detail = json.load(error)
        require(
            (detail.get("error") or {}).get("message")
            == "Pooling type 'none' is not OAI compatible. Please use a different pooling type",
            "Unrecognized text embedding protocol error",
        )
        rows = http_value(url + "/embedding", {"content": "测试图像"})
        require(
            isinstance(rows, list) and len(rows) == 1 and rows[0].get("index") == 0,
            "Native embedding must return one input",
        )
        tokens = rows[0].get("embedding") or []
        require(
            tokens and isinstance(tokens[0], list) and len(tokens[0]) == 1024,
            "Native BGE-M3 CLS must have 1024 dimensions",
        )
        cls = tokens[0]
        require(all(finite(v) for v in cls), "Non-finite native BGE-M3 CLS")
        norm = math.sqrt(sum(v * v for v in cls))
        require(finite(norm) and norm > 0, "Invalid native BGE-M3 CLS norm")
        return {"vector": [v / norm for v in cls]}, "llama_cpp_bge_m3_cls_l2"
    rows = result.get("data") or []
    require(len(rows) == 1, "Text embedding must return one vector")
    return {"vector": rows[0].get("embedding")}, "openai_compatible"


def finite(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(value: Any, reason: str) -> None:
    if not value:
        raise RuntimeError(reason)


def expected_total(parts: list[Any], actual: Any, tolerance: float = 0.1) -> bool:
    expected = sum(parts) if parts and all(finite(v) and v >= 0 for v in parts) else None
    return actual is None if expected is None else finite(actual) and abs(actual - expected) <= tolerance


def check_apple_resident_totals(sample: dict[str, Any]) -> bool:
    """Independent recomputation from recorded byte components, not provider totals."""
    observations = sample.get("memory_observations") or {}
    for group, field in (
        ("model", "model_process_system_memory_used_mib"),
        ("product", "product_stack_memory_mib"),
    ):
        observation = observations.get(group) or {}
        values = observation.get("components_bytes") or {}
        parts = [values.get(key) for key in ("dirty", "swapped", "clean", "reclaimable")]
        expected = None
        if observation.get("status") == "available" and memory_dependencies_fresh(sample, field):
            if not all(finite(value) and value >= 0 for value in parts):
                return False
            numbers = [float(value) for value in parts if finite(value)]
            if numbers[1] > numbers[0]:
                return False
            expected = (numbers[0] - numbers[1] + numbers[2] + numbers[3]) / 1048576
        actual = sample.get(field)
        if expected is None:
            if actual is not None:
                return False
        elif not finite(actual) or abs(expected - actual) > 0.005:
            return False
    return True


def check_jetson_product_total(sample: dict[str, Any]) -> bool:
    if sample.get("memory_observation_version") == 1 and not memory_dependencies_fresh(
        sample, "product_stack_memory_mib"
    ):
        return sample.get("product_stack_memory_mib") is None
    rows = sample.get("benchmark_processes") or []
    # An absent process observation is unknown, including while the API restarts.
    # GPU allocations alone cannot stand in for the missing related-process PSS.
    parts = [row.get("pss_mib") for row in rows] if rows else [None]
    parts += [sample.get("model_process_gpu_memory_mib"), sample.get("sentrix_auxiliary_model_uma_mib")]
    return expected_total(parts, sample.get("product_stack_memory_mib"))


def memory_dependencies_fresh(sample: dict[str, Any], key: str) -> bool:
    """Independent oracle: do not trust a persisted age or the production validator."""
    dependencies = sample.get("memory_dependencies")
    refs = dependencies.get(key) if isinstance(dependencies, dict) else None
    sources = sample.get("memory_observations") or {}
    captured = sample.get("_t", sample.get("t"))
    if not finite(captured) or not isinstance(refs, list):
        return False
    if not refs:
        return False
    for ref in refs:
        row = sources.get(ref) if isinstance(ref, str) and isinstance(sources, dict) else None
        if not isinstance(row, dict) or row.get("status") != "available":
            return False
        observed, finished, ttl = (
            row.get(k) for k in ("observed_at_monotonic", "finished_at_monotonic", "ttl_seconds")
        )
        if not finite(observed) or not finite(finished) or not finite(ttl):
            return False
        if not (0 < ttl <= 3 and observed <= finished <= captured and 0 <= captured - observed <= ttl):
            return False
    return True


def check_memory_freshness(sample: dict[str, Any]) -> list[str]:
    if sample.get("memory_observation_version") != 1:
        return []
    errors = []
    dependencies = sample.get("memory_dependencies") or {}
    if not isinstance(dependencies, dict):
        return ["invalid_memory_dependencies"]
    fields = {
        "model_process_memory_used_mib",
        "model_process_host_memory_mib",
        "model_process_gpu_memory_mib",
        "model_process_system_memory_used_mib",
        "sentrix_stack_pss_mib",
        "sentrix_product_pss_mib",
        "sentrix_auxiliary_model_pss_mib",
        "sentrix_auxiliary_model_uma_mib",
        "product_stack_memory_mib",
        "benchmark_process_memory_used_mib",
        "system_memory_used_mib",
        "system_memory_total_mib",
        "system_memory_available_mib",
    }
    for key in fields | dependencies.keys():
        if not memory_dependencies_fresh(sample, key) and sample.get(key) is not None:
            errors.append(f"stale_or_unverified_memory:{key}")
    if sample.get("memory_complete") is True:
        totals = ("model_process_system_memory_used_mib", "product_stack_memory_mib")
        if any(not finite(sample.get(key)) or not memory_dependencies_fresh(sample, key) for key in totals):
            errors.append("memory_complete_with_unavailable_inputs")
        refs = {
            ref
            for key, values in dependencies.items()
            if not key.startswith("system_")
            and isinstance(values, list)
            and finite(sample.get(key))
            and memory_dependencies_fresh(sample, key)
            for ref in values
            if isinstance(ref, str)
        }
        raw_sources = sample.get("memory_observations")
        sources = raw_sources if isinstance(raw_sources, dict) else {}
        times = [
            row.get("observed_at_monotonic") for ref in refs if isinstance(row := sources.get(ref), dict)
        ]
        declared = sample.get("process_observed_at_monotonic")
        if not times or not all(finite(value) for value in times) or not finite(declared):
            errors.append("missing_oldest_memory_observation")
        elif abs(min(float(value) for value in times if finite(value)) - declared) > 0.01:
            errors.append("memory_observation_not_oldest_input")
    return errors


def check_agent_summary(
    items: list[dict[str, Any]], summary: dict[str, Any], phase: dict[str, Any]
) -> list[str]:
    facts = [
        fact
        for item in items
        for step in item.get("execution_trace") or []
        if (step.get("stage") or step.get("type")) == "model"
        and (step.get("call_type") or "agent") in {"agent", "recovery"}
        for fact in step.get("tool_argument_parse_facts") or []
        if isinstance(fact.get("json_object_valid"), bool)
    ]
    completions = []
    for item in items:
        outcomes: list[bool | None] = []
        for turn in item.get("runtime_turns") or [item]:
            reason = turn.get("termination_reason")
            budget = (turn.get("agent2_trace") or {}).get("budget_outcome") or {}
            n, maximum = budget.get("model_steps"), budget.get("max_steps")
            if reason and reason not in {"answered", "answered_with_gap"}:
                outcomes.append(False)
            elif not reason or not all(type(v) is int and v >= 0 for v in (n, maximum)):
                outcomes.append(None)
            else:
                assert isinstance(n, int) and isinstance(maximum, int)
                outcomes.append(
                    n <= maximum
                    and bool(str(turn.get("answer") or "").strip())
                    and (turn.get("agent_status") or turn.get("status") or turn.get("turn_status"))
                    not in {"partial", "error", "timeout", "failed", "cancelled", "canceled"}
                )
        if False in outcomes:
            completions.append(False)
        elif None not in outcomes:
            completions.append(True)
    successes = sum(fact["json_object_valid"] for fact in facts)
    expected = {
        "tool_argument_json_total": len(facts) or None,
        "tool_argument_json_success": successes if facts else None,
        "tool_argument_json_rate": round(successes / len(facts), 3) if facts else None,
        "qa_completion_valid_count": len(completions),
        "qa_completion_within_steps_rate": round(sum(completions) / len(completions), 3)
        if completions
        else None,
        "json_parse_success_rate": None,
    }
    wall, count = phase.get("agent_phase_wall_ms"), phase.get("agent_completed")
    if finite(wall) and wall > 0 and type(count) is int and count > 0:
        expected.update(
            agent_throughput_latency_ms=round(wall / count, 1),
            agent_throughput_qa_per_s=round(1000 * count / wall, 3),
        )
    return [f"agent_summary:{key}" for key, value in expected.items() if summary.get(key) != value]


def check_statistics(samples: list[dict[str, Any]], saved: dict[str, Any], api: dict[str, Any]) -> list[str]:
    """Independent arithmetic over raw values, without importing production formulas."""
    errors = []
    keys = {
        "system_memory_used_mib",
        "model_process_system_memory_used_mib",
        "model_process_host_memory_mib",
        "model_process_gpu_memory_mib",
        "model_process_memory_used_mib",
        "product_stack_memory_mib",
        "power_draw_w",
        "temperature_c",
        "gpu_utilization_pct",
        "model_process_footprint_mib",
        "model_process_mapped_weights_mib",
    }
    for key in sorted(keys):
        values = sorted(float(s[key]) for s in samples if finite(s.get(key)) and s[key] >= 0)
        for label, projection in (("saved", saved), ("api", api)):
            stats = ((projection.get("phases") or {}).get("gpu_metrics") or {}).get(key) or {}
            if not values:
                if stats.get("mean") is not None:
                    errors.append(f"{label}:{key}:missing_became_value")
                continue
            expected = {
                "peak": max(values),
                "mean": round(sum(values) / len(values), 1),
                "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)],
                "valid_count": len(values),
            }
            if any(not finite(stats.get(k)) or abs(stats[k] - v) > 0.01 for k, v in expected.items()):
                errors.append(f"{label}:{key}:full_sample_statistics")
        field_stats = (api.get("telemetry_live") or {}).get("field_stats") or {}
        if values and field_stats.get(key) != ((api.get("phases") or {}).get("gpu_metrics") or {}).get(key):
            errors.append(f"{key}:chart_statistics_mismatch")
    if saved.get("summary") != api.get("summary"):
        errors.append("saved_api_summary_mismatch")
    return errors


def epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


def local_listener(url: str, *, allow_local_address: bool = False) -> dict[str, Any]:
    parsed = urlparse(url)
    if allow_local_address:
        local_url(url, preserve_local=True)
    else:
        require(parsed.hostname in {"127.0.0.1", "localhost", "::1"}, f"Non-loopback dependency: {url}")
    require(not parsed.username and not parsed.password, "Credentials in local dependency URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pid = listener(port)
    require(pid, f"Missing local listener: {port}")
    command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
    # Loopback nginx/SSH/HAProxy can hide a remote model. A reviewed inference or
    # embedding process must own the listener; llama.cpp RPC/offload is forbidden.
    require(
        not any(
            x in command.lower()
            for x in (
                "ssh ",
                "socat",
                "haproxy",
                "nginx",
                "--rpc",
                "rpc-server",
                "--proxy",
                "--remote",
            )
        ),
        f"Proxy or remote offload found on {port}",
    )
    return {"port": port, "pid": pid, "command": command}


def dataset_hash(root: Path) -> dict[str, str]:
    files = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}
    return {
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "file_count": str(len(files)),
    }


def check_retrieval_summary(items: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    eligible = [
        item for item in items if str(item.get("answerability") or "").lower().strip() != "unanswerable"
    ]
    for prefix in ("media", "image", "video", "candidate"):
        field = f"{prefix}_retrieval_counts"
        rows = [
            item[field]
            for item in eligible
            if isinstance(item.get(field), dict) and item[field].get("gt", 0) > 0
        ]
        gt = sum(row["gt"] for row in rows)
        predicted = sum(row["predicted"] for row in rows)
        matched = sum(row["matched"] for row in rows)
        precision = matched / predicted if predicted else (0.0 if gt else None)
        recall = matched / gt if gt else None
        f1 = 2 * matched / (predicted + gt) if gt else None
        for key, value in (("precision", precision), ("recall", recall), ("f1", f1)):
            expected = round(value, 3) if value is not None else None
            if summary.get(f"{prefix}_retrieval_{key}_micro") != expected:
                errors.append(f"{prefix}_retrieval_{key}_arithmetic")
    return errors


def check_nvidia_attribution(sample: dict[str, Any], listener_pid: int) -> bool:
    rows = sample.get("model_system_processes") or []
    allowed = {listener_pid}
    while True:
        expanded = allowed | {row["pid"] for row in rows if row.get("ppid") in allowed}
        if expanded == allowed:
            break
        allowed = expanded
    ram_pids = {row["pid"] for row in rows}
    gpu_pids = {row["pid"] for row in sample.get("model_processes") or []}
    return bool(ram_pids) and ram_pids <= allowed and gpu_pids <= allowed


def check_device_freshness(sample: dict[str, Any]) -> list[str]:
    observed, captured = sample.get("device_observed_at_monotonic"), sample.get("_t")
    if not finite(observed) or not finite(captured):
        return []
    age = captured - observed
    declared = sample.get("device_observation_age_seconds")
    errors = []
    if not finite(declared) or abs(age - declared) > 0.01:
        errors.append("device_age_not_rebased_at_sample")
    ttl = sample.get("device_observation_ttl_seconds", 3.0)
    if not finite(ttl) or ttl <= 0 or not 0 <= age <= ttl:
        fields = (
            "power_draw_w",
            "temperature_c",
            "cpu_temperature_c",
            "gpu_utilization_pct",
            "memory_used_mib",
            "cpu_power_w",
            "gpu_power_w",
        )
        if any(finite(sample.get(key)) for key in fields):
            errors.append("stale_device_values")
        if any(finite(v) for v in (sample.get("power_rails_w") or {}).values()):
            errors.append("stale_device_rails")
    return errors


def face_weights(effective: dict[str, str]) -> list[dict[str, Any]]:
    chains = {}
    for key in ("FACE_PROVIDERS", "RETINAFACE_PROVIDERS"):
        chain = [part.strip() for part in effective.get(key, "").split(",") if part.strip()]
        require(bool(chain), f"Explicit face provider chain missing: {key}")
        chains[key] = chain[0]
    home = Path(effective.get("HOME", str(Path.home())))
    model_dir = (
        Path(effective.get("FACE_MODEL_ROOT", str(home / ".insightface")))
        / "models"
        / effective.get("FACE_MODEL_NAME", "buffalo_l")
    )
    paths = [
        Path(effective.get("RETINAFACE_MODEL_PATH", str(home / "benchmarks/retinaface/retinaface_r50.onnx"))),
        model_dir / "det_10g.onnx",
        model_dir / "w600k_r50.onnx",
    ]
    # Execute real local weights with profiling in a bounded child. Available EP names alone
    # do not prove acceleration; requesting CUDA then executing only CPU must fail.
    program = """import json,sys
from pathlib import Path
from scripts.deploy.runtime_acceleration_probe import onnx_inference
specs=json.loads(sys.argv[1])
print(json.dumps([onnx_inference(Path(path),provider) for path,provider in specs]))
"""
    for path in paths:
        require(path.is_file() and path.stat().st_size > 0, f"Missing local face weight: {path}")
    specs = [
        (str(path), chains["RETINAFACE_PROVIDERS" if i == 0 else "FACE_PROVIDERS"])
        for i, path in enumerate(paths)
    ]
    env = {**os.environ, **effective}
    process = subprocess.run(
        [sys.executable, "-c", program, json.dumps(specs)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=Path(__file__).resolve().parents[2],
    )
    result: list[dict[str, Any]] = json.loads(process.stdout)
    return result


def preflight(root: Path, runtime: Path) -> dict[str, Any]:
    revision = verify(root)["revision"]
    bindings = read_json(runtime / "bindings.json")
    connection = read_json(Path(bindings["config"]) / "runtime_connection.json")
    config = http("http://127.0.0.1:8872/api/v1/config")
    diagnostics = http("http://127.0.0.1:8291/api/v1/diagnostics")
    require(diagnostics.get("status") == "ok", f"Degraded product dependency: {diagnostics}")
    require(diagnostics.get("build_revision") == revision, "Product revision mismatch")
    require(config.get("build_revision") == revision, "PhotoBench revision mismatch")
    for key in ("sentrix_url", "model_base_url", "embedding", "product", "product_settings"):
        require(
            (config.get("runtime_config") or {}).get(key) == connection.get(key),
            f"Effective PhotoBench configuration differs: {key}",
        )
    require(
        config.get("default_vllm_model_base_url") == connection["model_base_url"],
        "PhotoBench environment overrides the model topology",
    )
    runs = http("http://127.0.0.1:8872/api/v1/runs")
    require(runs.get("active_count", 0) == 0, "Active orphan/run remains")
    model_url = connection["model_base_url"].rstrip("/")
    adjustments = read_json(runtime / "topology-adjustments.json")
    override = adjustments.get("local_model_endpoint_override")
    require(not override or override.rstrip("/") == model_url, "Undeclared model address override")
    main = local_listener(model_url, allow_local_address=bool(override))
    api_pid = listener(8291)
    require(api_pid, "No product listener")
    effective = process_environment(int(api_pid or 0))
    require(effective.get("SENTRIX_VLM_BASE_URL") == model_url, "Product/PB model topology mismatch")
    local_hosts = ["127.0.0.1", "localhost", "::1"]
    model_host = urlparse(model_url).hostname
    if model_host and model_host not in local_hosts:
        local_hosts.append(model_host)
    require(effective.get("NO_PROXY") == ",".join(local_hosts), "Local HTTP may use a proxy")
    weights = face_weights(effective)
    model_list = http(model_url + "/models")
    model_id = model_list["data"][0]["id"]
    model_probe = http(
        model_url + "/chat/completions",
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply OK."}],
            "temperature": 0,
            "max_tokens": 32,
        },
    )
    require(model_probe.get("choices"), "Empty main-model probe")
    embedding = connection["embedding"]
    unified = embedding["mode"] == "unified_multimodal"
    text_url = embedding["wemm_url"] if unified else embedding["text_url"]
    image_url = embedding["wemm_url"] if unified else embedding["image_url"]
    for name, url in (("text", text_url), ("image", image_url)):
        key = (
            "SENTRIX_WEMM_URL"
            if unified
            else ("SENTRIX_TEXT_EMBEDDER_URL" if name == "text" else "SENTRIX_LEGACY_CLIP_URL")
        )
        require(effective.get(key) == url, f"Product/PB {name} topology mismatch")
    services = [main, local_listener(text_url), local_listener(image_url)]
    # An actual image on this host; no download, remote storage or embedded QA.
    image = runtime / "embedding-probe.png"
    from PIL import Image

    Image.new("RGB", (96, 96), (220, 50, 50)).save(image)
    text_result, text_protocol = probe_text_embedding(text_url.rstrip("/"), unified)
    image_result = http(image_url.rstrip("/") + "/embed_image", {"path": str(image)})
    dimensions: dict[str, int] = {}
    for name, response, expected in (
        ("text", text_result, 2048 if unified else 1024),
        ("image", image_result, 2048 if unified else 768),
    ):
        vector = response.get("vector") or []
        require(len(vector) == expected and all(finite(v) for v in vector), f"Bad {name} embedding")
        require(sum(v * v for v in vector) > 0, f"Zero {name} embedding")
        dimensions[name] = len(vector)
    # Exercise the vision path on the SAME main endpoint, using local inline pixels.
    vision = http(
        model_url + "/chat/completions",
        {
            "model": model_id,
            "temperature": 0,
            "max_tokens": 32,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe the image briefly."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image.read_bytes()).decode()
                            },
                        },
                    ],
                }
            ],
        },
    )
    require(vision.get("choices"), "Main-model visual probe failed")
    album = Path(bindings["datasets"]) / "album3-14"
    require(album.is_dir(), "Missing album3-14 data")
    report = {
        "revision": revision,
        "diagnostics": diagnostics,
        "connection": {
            key: connection.get(key)
            for key in ("sentrix_url", "model_base_url", "embedding", "product", "product_settings")
        },
        "services": services,
        "models": model_list,
        "embedding_dimensions": dimensions,
        "text_embedding_protocol": text_protocol,
        "face_weights": weights,
        "dataset": dataset_hash(album),
        "api_pid": api_pid,
        "pb_pid": listener(8872),
        "checked_at_epoch": time.time(),
    }
    write_json(runtime / "preflight.json", report)
    return report


def run(runtime: Path, *, mode: str = "full", scope_id: str = "") -> dict[str, Any]:
    preflight_report = read_json(runtime / "preflight.json")
    require(time.time() - preflight_report["checked_at_epoch"] < 900, "Preflight is older than 15 minutes")
    if (runtime / "run-request.json").exists():
        raise RuntimeError(
            "This release already has a recorded request; retain it and use a new attempt folder"
        )
    connection = preflight_report["connection"]
    request = {
        "album_id": "album3-14",
        "qa_set": "" if mode == "build" else "compact-10q",
        "mode": mode,
        "models": ["__current__"],
        "model_base_url": connection["model_base_url"],
        "sentrix_url": "http://127.0.0.1:8291",
        "runtime_framework": "llama_cpp",
        "turn_timeout": 900,
    }
    if mode == "reuse":
        require(bool(scope_id), "reuse requires an existing scope")
        request["existing_scope_id"] = scope_id
    write_json(runtime / "run-request.json", request)
    response = http("http://127.0.0.1:8872/api/v1/runs", request)
    write_json(runtime / "run-response.json", response)
    return response


def explicit_score(value: Any) -> int | None:
    """Independent acceptance oracle, not a production score inference path."""
    if finite(value) and value in (0, 1, 2):
        return int(value)
    if isinstance(value, str) and re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value.strip()
    ):
        try:
            number = Decimal(value.strip())
            if number.is_finite() and number in (Decimal(0), Decimal(1), Decimal(2)):
                return int(number)
        except InvalidOperation:
            pass
    return None


def assess_tasks(items: list[dict[str, Any]], *, expected_count: int = 10) -> dict[str, Any]:
    """Contract v1: truthful terminal evidence, independently of answer quality."""
    success_reasons = {"answered", "answered_with_gap", "policy_refusal"}
    failure_reasons = {
        "no_tool_call_stopped",
        "tool_budget_exhausted",
        "step_budget_exhausted",
        "timeout",
        "model_error",
        "guard_blocked",
        "parse_failure",
        "planner_blocked",
    }
    report: dict[str, Any] = {
        key: 0
        for key in (
            "success",
            "recovered_success",
            "controlled_failure",
            "unhandled_failure",
            "cancelled",
            "unverified_recovery_events",
        )
    }
    defects = []
    rows = []
    call_count = error_count = 0
    if len(items) != expected_count:
        defects.append("scheduled_item_count_mismatch")
    ids = [item.get("qa_id") for item in items]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        defects.append("missing_or_duplicate_qa_id")
    for index, item in enumerate(items):
        reason = item.get("termination_reason")
        turns = item.get("runtime_turns") or []
        # Independently inspect every final product turn, including earlier
        # conversation turns; intermediate recovered model errors are not finals.
        failure = next(
            (
                turn.get("termination_reason")
                for turn in turns
                if turn.get("termination_reason") in failure_reasons
            ),
            None,
        )
        if failure:
            reason = failure
        terminal = bool(turns) and item.get("execution_status") == "completed"
        for turn in turns:
            trace = turn.get("agent2_trace") or {}
            budget = trace.get("budget_outcome") or {}
            n, maximum = budget.get("model_steps"), budget.get("max_steps")
            terminal = terminal and all(type(v) is int and v >= 0 for v in (n, maximum))
            if type(n) is int and type(maximum) is int:
                terminal = terminal and n <= maximum
                if turn.get("termination_reason") == "step_budget_exhausted":
                    terminal = terminal and n == maximum
            terminal = terminal and turn.get("agent_status") in {
                "complete",
                "partial",
                "failed",
                "blocked",
                "cancelled",
            }
            terminal = (
                terminal
                and trace.get("terminal_reason") == turn.get("termination_reason")
                and (trace.get("task_state") or {}).get("phase") == "terminal"
            )
        calls = [step for step in item.get("execution_trace") or [] if step.get("stage") == "model"]
        errors = [step for step in calls if step.get("status") in {"error", "failed"}]
        call_count += len(calls)
        error_count += len(errors)
        guards = [guard for turn in turns for guard in (turn.get("agent2_trace") or {}).get("guard") or []]
        recovery_evidence = sum(guard.get("code") == "prompt_too_long" for guard in guards)
        recovered = 0
        for position, call in enumerate(calls):
            if call.get("status") not in {"error", "failed"}:
                continue
            # Product contract: each compression removes a complete prior tool round;
            # the observed recovery must have a later successful call at the same step.
            if (
                call.get("error_type") == "PromptTooLongError"
                and call.get("step_id")
                and finite(call.get("duration_ms"))
                and recovery_evidence > 0
                and any(
                    later.get("step_id") == call["step_id"]
                    and later.get("status") in {"complete", "completed", "ok"}
                    for later in calls[position + 1 :]
                )
            ):
                recovered += 1
                recovery_evidence -= 1
            elif reason in success_reasons:
                report["unverified_recovery_events"] += 1
        if terminal and reason in success_reasons and str(item.get("answer") or "").strip():
            category = "success"
            if errors and recovered == len(errors):
                report["recovered_success"] += 1
        elif terminal and reason in failure_reasons:
            category = "controlled_failure"
        elif reason == "cancelled" or "cancelled" in str(item.get("error") or "").lower():
            category = "cancelled"
        else:
            category = "unhandled_failure"
            defects.append(f"{index}:missing_or_inconsistent_terminal_evidence")
        report[category] += 1
        rows.append(
            {
                "qa_id": item.get("qa_id"),
                "category": category,
                "termination_reason": reason,
                "terminal_verified": bool(terminal),
                "call_errors": len(errors),
                "verified_recovered_calls": recovered,
            }
        )
    report.update(
        rule_version=1,
        expected_count=expected_count,
        observed_count=len(items),
        service_errors=defects,
        items=rows,
        call_error_rate={"numerator": error_count, "denominator": call_count},
        baseline="same fixed 10QA; historical runs retained by revision; no statistical reliability claim",
        tolerance=(
            "no task-quality error-rate threshold; all tasks must have truthful terminals; "
            "telemetry thresholds unchanged"
        ),
    )
    return report


def check_terminal_status(
    saved: dict[str, Any], api: dict[str, Any], tasks: dict[str, Any], missing_scores: list[Any]
) -> list[str]:
    """Independent cross-check, without importing the production completion rule."""
    errors = []
    if saved.get("status") != api.get("status"):
        errors.append("terminal_status_saved_api_mismatch")
    outputs: list[Any] = []
    for item in saved.get("items") or []:
        for fact in [item, *(item.get("runtime_turns") or [])]:
            outputs.extend(fact.get(k) for k in ("judge", "task_judge", "evidence_judge"))
            outputs.extend(fact.get("task_judges") or [])
    failed_judge = any(isinstance(o, dict) and o.get("judge_status") == "failed" for o in outputs)
    task_failure = any(tasks.get(k) for k in ("controlled_failure", "unhandled_failure", "cancelled"))
    partial = any(p.get("status") == "partial" for p in (saved.get("phases") or {}).values())
    expected = (
        "completed_with_errors" if task_failure or missing_scores or failed_judge or partial else "completed"
    )
    if saved.get("status") != expected:
        errors.append(f"terminal_status_expected:{expected}")
    return errors


def audit(runtime: Path) -> dict[str, Any]:
    bindings = read_json(runtime / "bindings.json")
    pre = read_json(runtime / "preflight.json")
    run_id = read_json(runtime / "run-response.json")["run_ids"][0]
    directory = Path(bindings["results"]) / run_id
    saved = read_json(directory / "run.json")
    api = http(f"http://127.0.0.1:8872/api/v1/runs/{run_id}")
    write_json(runtime / "api-result.json", api)
    items = saved.get("items") or []
    if not items and (directory / "results.jsonl").is_file():
        items = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines() if line]
    lines = (directory / "gpu_samples.jsonl").read_text().splitlines()
    samples = [json.loads(line) for line in lines if line]
    chain_errors: list[str] = []
    metric_errors: list[str] = check_statistics(samples, saved, api)
    integrity = ((saved.get("phases") or {}).get("gpu_metrics") or {}).get("sampling_integrity") or {}
    for key in ("callback_failures", "publisher_failures", "collection_failures", "stop_pending_threads"):
        if integrity.get(key):
            chain_errors.append(f"telemetry_integrity:{key}")
    mode = saved.get("mode", "full")
    required_phases = ["model_deploy", "scope_setup", "gpu_metrics", "aggregate"]
    if mode != "reuse":
        required_phases += ["identity_seed", "photo_import", "pipeline_processing"]
    if mode != "build":
        required_phases.append("qa_eval")
    for phase in required_phases:
        status = ((saved.get("phases") or {}).get(phase) or {}).get("status")
        if status != "done" and not (phase == "qa_eval" and status == "partial"):
            chain_errors.append(f"required_phase_not_done:{phase}")
    expected_items = 0 if mode == "build" else 10
    if saved.get("status") not in {"completed", "completed_with_errors"} or len(items) != expected_items:
        chain_errors.append(f"run_not_terminal_with_{expected_items}_items")
    if mode == "build":
        if (
            saved.get("qa_count") != 0
            or saved.get("run_valid") is not None
            or api.get("summary", {}).get("run_valid") is not None
        ):
            metric_errors.append("build_qa_or_validity_not_applicable_contract")
        if (saved.get("scope_build") or {}).get("status") != "ready":
            chain_errors.append("scope_build_incomplete")
    if saved.get("build_revision") != pre["revision"] or api.get("build_revision") != pre["revision"]:
        metric_errors.append("revision_mismatch")
    tasks = assess_tasks(items, expected_count=expected_items)
    chain_errors.extend(tasks["service_errors"])
    scores: list[float] = []
    missing_scores = []
    normalizations = []
    for index, item in enumerate(items):
        judge = item.get("judge") or {}
        score = explicit_score(judge.get("score"))
        if (
            item.get("judge_status") != "completed"
            or score is None
            or judge.get("consistency_status") == "inconsistent"
        ):
            missing_scores.append(
                {
                    "index": index,
                    "qa_id": item.get("qa_id"),
                    "reason": judge.get("reason"),
                    "judge_status": item.get("judge_status"),
                }
            )
        else:
            scores.append(float(score))
        for attempt in judge.get("attempts") or []:
            fact = attempt.get("score_normalization") or {}
            if fact.get("converted"):
                normalizations.append({"index": index, "fact": fact})
    chain_errors.extend(check_terminal_status({**saved, "items": items}, api, tasks, missing_scores))
    infrastructure_errors: list[str] = []
    phase_report: dict[str, Any] = {}
    observed_phases = (
        ["qa_eval"]
        if mode == "reuse"
        else ["photo_import", "pipeline_processing"]
        if mode == "build"
        else ["photo_import", "pipeline_processing", "qa_eval"]
    )
    for phase in observed_phases:
        points = [s for s in samples if s.get("phase") == phase]
        core = [
            s
            for s in points
            if all(
                finite(s.get(k))
                for k in (
                    "system_memory_used_mib",
                    "model_process_system_memory_used_mib",
                    "product_stack_memory_mib",
                )
            )
            and s.get("memory_complete") is True
            and (
                s.get("memory_observation_version") != 1
                or all(
                    memory_dependencies_fresh(s, key)
                    for key in (
                        "system_memory_used_mib",
                        "model_process_system_memory_used_mib",
                        "product_stack_memory_mib",
                    )
                )
            )
            and finite(s.get("process_observed_at_monotonic"))
            and 0 <= s["_t"] - s["process_observed_at_monotonic"] <= 3
        ]
        intervals = [b["_t"] - a["_t"] for a, b in zip(core, core[1:], strict=False)]
        phase_report[phase] = {
            "samples": len(points),
            "valid_core_memory": len(core),
            "max_gap_seconds": max(intervals, default=None),
            "first_epoch": core[0].get("observed_at_epoch") if core else None,
            "last_epoch": core[-1].get("observed_at_epoch") if core else None,
            "valid_fraction": len(core) / len(points) if points else 0,
        }
        bucket = (saved.get("phases") or {}).get(phase) or {}
        start, end = epoch(bucket.get("started_at")), epoch(bucket.get("finished_at"))
        if start is not None and end is not None:
            first = core[0].get("observed_at_epoch") if core else None
            last = core[-1].get("observed_at_epoch") if core else None
            phase_report[phase].update(
                duration_seconds=end - start,
                start_gap_seconds=max(0, first - start) if finite(first) else None,
                end_gap_seconds=max(0, end - last) if finite(last) else None,
                observed_span_fraction=min(1, (last - first) / (end - start))
                if finite(first) and finite(last) and end > start
                else None,
            )
        if not core:
            metric_errors.append(f"no_core_memory_in_{phase}")
        if len(core) != len(points):
            metric_errors.append(f"incomplete_core_memory_in_{phase}")
        if any(
            finite(phase_report[phase].get(k)) and phase_report[phase][k] > 3
            for k in ("max_gap_seconds", "start_gap_seconds", "end_gap_seconds")
        ):
            metric_errors.append(f"core_memory_gap_over_3_seconds_in_{phase}")
    formulas_checked = 0
    rails: set[str] = set()
    for index, sample in enumerate(samples):
        metric_errors.extend(f"{index}:{reason}" for reason in check_device_freshness(sample))
        metric_errors.extend(f"{index}:{reason}" for reason in check_memory_freshness(sample))
        accounting = sample.get("memory_accounting")
        if accounting == "jetson_pss_plus_nvmap_estimate":
            components = [
                sample.get("model_process_host_memory_mib"),
                sample.get("model_process_gpu_memory_mib"),
            ]
            expected = sum(components) if all(finite(v) for v in components) else None
            if sample.get("memory_observation_version") == 1 and not memory_dependencies_fresh(
                sample, "model_process_system_memory_used_mib"
            ):
                expected = None
            actual = sample.get("model_process_system_memory_used_mib")
            if (
                expected is None
                and actual is not None
                or expected is not None
                and (not finite(actual) or abs(actual - expected) > 0.05)
            ):
                metric_errors.append(f"{index}:jetson_main_total")
            if not check_jetson_product_total(sample):
                metric_errors.append(f"{index}:jetson_product_total")
            formulas_checked += 1
        elif accounting == "apple_footprint_resident_v2":
            if not check_apple_resident_totals(sample):
                metric_errors.append(f"{index}:apple_resident_totals")
            formulas_checked += 1
        elif accounting == "apple_capacity_estimate":
            for total, left, right in (
                (
                    "model_process_system_memory_used_mib",
                    "model_process_footprint_mib",
                    "model_process_mapped_weights_mib",
                ),
                (
                    "product_stack_memory_mib",
                    "product_stack_footprint_mib",
                    "product_stack_mapped_weights_mib",
                ),
            ):
                parts = [sample.get(left), sample.get(right)]
                if not expected_total(parts, sample.get(total)):
                    metric_errors.append(f"{index}:{total}")
            formulas_checked += 1
        elif accounting == "nvidia_pss_and_vram":
            if not check_nvidia_attribution(sample, pre["services"][0]["pid"]):
                metric_errors.append(f"{index}:other_gpu_instance_attributed_to_main")
            if not finite(sample.get("model_process_memory_used_mib")):
                metric_errors.append(f"{index}:missing_main_vram")
            if sample.get("source") in {"jetson_local_pss", "orin_ssh_pss"}:
                metric_errors.append(f"{index}:nvidia_labeled_jetson")
            formulas_checked += 1
        if sample.get("system_memory_scope") in {
            "apple_physical_nonfree_including_cache_v2",
            "linux_physical_nonfree_including_cache_v2",
        }:
            system = (sample.get("memory_observations") or {}).get("system") or {}
            physical_total, physical_free = system.get("total_mib"), system.get("free_mib")
            physical_used = sample.get("system_memory_used_mib")
            if memory_dependencies_fresh(sample, "system_memory_used_mib") and (
                not finite(physical_total)
                or not finite(physical_free)
                or not finite(physical_used)
                or not 0 <= physical_free <= physical_total
                or abs(physical_used - (physical_total - physical_free)) > 0.05
            ):
                metric_errors.append(f"{index}:physical_system_total")
        if sample.get("power_rail") and finite(sample.get("power_draw_w")):
            rails.add(str(sample["power_rail"]))
        if sample.get("device_observation_age_seconds", 0) > 3 and finite(sample.get("power_draw_w")):
            metric_errors.append(f"{index}:stale_device_power")
    if not formulas_checked:
        metric_errors.append("no_accounted_samples")
    if any(s.get("memory_accounting") == "jetson_pss_plus_nvmap_estimate" for s in samples) and not rails:
        metric_errors.append("no_labeled_jetson_power")
    if saved.get("runtime_snapshot") != api.get("runtime_snapshot"):
        metric_errors.append("snapshot_projection_mismatch")
    for projection in (saved, api):
        summary = projection.get("summary") or {}
        metric_errors.extend(check_retrieval_summary(items, summary))
        metric_errors.extend(
            check_agent_summary(items, summary, (saved.get("phases") or {}).get("qa_eval") or {})
        )
        aqm = round(sum(scores) / len(scores), 3) if scores else None
        if summary.get("answer_quality_mean") != aqm or summary.get("judge_valid_count") != len(scores):
            metric_errors.append("judge_summary_arithmetic")
        agent_times = [item["wall_clock_ms"] for item in items if finite(item.get("wall_clock_ms"))]
        expected_latency = round(sum(agent_times) / len(agent_times), 1) if agent_times else None
        if summary.get("agent_task_latency_mean_ms") != expected_latency:
            metric_errors.append("agent_latency_arithmetic")
    for role in ("api", "photobench"):
        log = runtime / f"{role}.log"
        if log.exists():
            tail = log.read_text(errors="replace")[-2_000_000:].lower()
            if any(x in tail for x in ("out of memory", "outofmemoryerror", "oom-kill", "killed process")):
                infrastructure_errors.append(f"{role}:oom_in_service_log")
    if listener(8291) != pre["api_pid"] or listener(8872) != pre["pb_pid"]:
        infrastructure_errors.append("unexpected_service_restart")
    report = {
        "run_id": run_id,
        "revision": pre["revision"],
        "acceptance_rule_version": 4,
        "mode": mode,
        "service_pass": not chain_errors,
        "scoring_complete": None if mode == "build" else len(scores) == expected_items,
        "scoring": {
            "valid": len(scores),
            "expected": expected_items,
            "applicable": mode != "build",
            "missing": missing_scores,
            "standardized_responses": normalizations,
        },
        "tasks": tasks,
        "infrastructure_errors": infrastructure_errors,
        "chain_pass": not chain_errors,
        "metrics_pass": not metric_errors,
        "chain_errors": chain_errors,
        "metric_errors": metric_errors,
        "items": len(items),
        "valid_judge_scores": len(scores),
        "aqm": sum(float(score) for score in scores) / len(scores) if scores else None,
        "samples": len(samples),
        "formulas_checked": formulas_checked,
        "phases": phase_report,
        "power_rails": sorted(rails),
        "raw_directory": str(directory),
        "dataset": pre["dataset"],
    }
    write_json(runtime / "acceptance.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "run", "audit"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--mode", choices=["full", "build", "reuse"], default="full")
    parser.add_argument("--scope-id", default="")
    args = parser.parse_args()
    if args.action == "preflight":
        report = preflight(args.root.resolve(), args.runtime.resolve())
    elif args.action == "run":
        report = run(args.runtime.resolve(), mode=args.mode, scope_id=args.scope_id)
    else:
        report = audit(args.runtime.resolve())
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
