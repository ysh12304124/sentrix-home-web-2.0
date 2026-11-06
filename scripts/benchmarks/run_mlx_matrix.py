"""Durable owner for the authorized matrix. Unknown submissions are never replayed."""

import base64
import concurrent.futures
import fcntl
import hashlib
import io
import json
import os
import subprocess
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from PIL import Image

from scripts.benchmarks.mlx_capacity import (
    diagnostic_failure_categories,
    observe_run,
    optimization_speed_gate,
    percentile,
    read_completed_round,
    reconcile_model_calls,
    reconcile_prefix_reuse,
    save,
    submit_run_once,
    summarize_resources,
    validate_user_cancelled_baseline,
)

r = Path(os.environ["SENTRIX_MLX_EXPERIMENT_ROOT"]).resolve()
series = os.environ["SENTRIX_MLX_SERIES"]
if not series or any(
    c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in series
):
    raise ValueError("series must be an explicit filesystem-safe label")

python = r / "repo/.venv/bin/python"
statefile = r / "runtime/matrix-state.json"
lock = (r / "runtime/matrix.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
state: dict[str, Any] = (
    json.loads(statefile.read_text()) if statefile.exists() else {"status": "starting", "rounds": []}
)
client = httpx.Client(timeout=30, trust_env=False)


def checkpoint(**kwargs: Any) -> None:
    state.update(kwargs, updated_at=time.time())
    save(statefile, state)


def owned_stop(pid: int, needle: str) -> None:
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    command = " ".join(p.cmdline())
    assert needle in command and str(r) in command, "process ownership differs"
    p.terminate()
    try:
        p.wait(45)
    except psutil.TimeoutExpired:
        p.kill()
        p.wait(10)


def stop_round() -> None:
    current = r / "runtime/current-round.json"
    if not current.exists():
        return
    d = json.loads(current.read_text())
    owned_stop(d["bench_pid"], "photobench.main:create_app")
    owned_stop(d["api_pid"], "sentrix.main:create_app")
    current.unlink()


def restart_engine() -> None:
    p = json.loads((r / "runtime/vllm-mlx-process.json").read_text())
    owned_stop(p["pid"], "serve_mlx.py")
    subprocess.run([str(python), str(r / "repo/scripts/benchmarks/launch_mlx_engine.py")], check=True)
    for _ in range(90):
        try:
            d = client.get("http://127.0.0.1:18100/v1/models")
            d.raise_for_status()
            return
        except httpx.HTTPError:
            time.sleep(2)
    raise RuntimeError("engine did not become ready")


def warmup() -> list[dict[str, Any]]:
    im = Image.new("RGB", (224, 224), "red")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def call(i: int) -> dict[str, Any]:
        content = (
            "Reply with exactly READY."
            if i % 2 == 0
            else [
                {"type": "text", "text": "Name the background color. One word."},
                {"type": "image_url", "image_url": {"url": url}},
            ]
        )
        body = {
            "model": "qwen35-4b-mlx-q4",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 64,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "seed": 42,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        result = client.post("http://127.0.0.1:18100/v1/chat/completions", json=body, timeout=240)
        result.raise_for_status()
        d = result.json()
        assert d["choices"][0]["finish_reason"] == "stop"
        return {"usage": d["usage"], "finish_reason": "stop"}

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        return list(pool.map(call, range(4)))


def summarize(entry: dict[str, Any]) -> dict[str, Any]:
    stage = r / "rounds" / entry["label"]
    rid = entry["run_id"]
    raw = json.loads((r / "results" / rid / "run.json").read_text())
    items = raw["items"]
    phase = raw["phases"]["qa_eval"]
    start = datetime.fromisoformat(phase["agent_phase_started_at"]).timestamp()
    end = datetime.fromisoformat(phase["agent_phase_finished_at"]).timestamp()
    samples = [json.loads(x) for x in (stage / "resources.jsonl").read_text().splitlines()]
    samples = [s for s in samples if start <= s["at"] <= end]
    events = [json.loads(x) for x in (r / "evidence/model-events.jsonl").read_text().splitlines()]
    events = [e for e in events if start <= e["at"] <= end]
    terminal = [e for e in events if e["event"] == "request_terminal"]
    resource = summarize_resources(samples)
    latencies = [i["wall_clock_ms"] / 1000 for i in items if isinstance(i.get("wall_clock_ms"), (int, float))]
    failures: Counter[str] = Counter()
    from photobench.domain.runs.execution import TASK_FAILURE_REASONS, agent_completion_error

    for item in items:
        reason = agent_completion_error(item)
        if reason:
            failures[
                reason if reason in TASK_FAILURE_REASONS or reason == "cancelled" else "agent_error"
            ] += 1
        if item.get("execution_status") != "completed":
            failures["execution_" + str(item.get("execution_status"))] += 1
        if item.get("judge_status") != "completed":
            failures["judge_" + str(item.get("judge_status"))] += 1
    for e in terminal:
        if e["finish_reason"] != "stop":
            failures["model_" + str(e["finish_reason"])] += 1
    for category, count in diagnostic_failure_categories(items).items():
        if count:
            failures["diagnostic_" + category] += count
    model_evidence = reconcile_model_calls(items, terminal)
    if not model_evidence["verified"]:
        failures["incomplete_model_call_evidence"] += 1
    prefix_evidence = reconcile_prefix_reuse(terminal, events)
    if "shared_prefix" in os.environ.get("SENTRIX_MLX_OPTIMIZATIONS", "").split(","):
        if not prefix_evidence["verified"]:
            failures["incomplete_prefix_cache_evidence"] += 1
        elif prefix_evidence["cached_tokens"] == 0:
            failures["prefix_cache_unused"] += 1
    if not samples or resource["engine_metrics_missing_samples"] == len(samples):
        failures["missing_engine_metrics"] += 1
    batches = [e["actual_batch_size"] for e in events if e["event"] == "token_batch"]
    ttfts = [e["ttft_s"] for e in terminal if e["ttft_s"] is not None]
    if not batches:
        failures["missing_actual_batch_evidence"] += 1
    qa = phase["agent_phase_wall_ms"] / 1000
    completed = sum(i.get("execution_status") == "completed" and not agent_completion_error(i) for i in items)
    value = {
        "execution_revision": json.loads((stage / "diagnostics.private.json").read_text())["build_revision"],
        "reporting_revision": state["revision"],
        "metrics_probe_timeout_s": sorted({s.get("engine_probe_timeout_s", 5) for s in samples}),
        "label": entry["label"],
        "run_id": rid,
        "concurrency": entry["concurrency"],
        "questions": entry["questions"],
        "trial": entry.get("trial"),
        "completed": completed,
        "returned": sum(i.get("execution_status") == "completed" for i in items),
        "failure_categories": diagnostic_failure_categories(items),
        "judge_completed": sum(i.get("judge_status") == "completed" for i in items),
        "judge_failed": sum(i.get("judge_status") == "failed" for i in items),
        "status": raw["status"],
        "passed": raw.get("run_valid") is True and not failures and len(items) == entry["questions"],
        "qa_per_min": completed / qa * 60,
        "qa_seconds": qa,
        "judge_seconds": phase.get("judge_phase_wall_ms", 0) / 1000,
        "latency_p50_s": percentile(latencies, 0.5),
        "latency_p95_s": percentile(latencies, 0.95),
        "ttft_p95_s": percentile(ttfts, 0.95),
        "actual_batch_peak": max(batches) if batches else None,
        "queue_peak": resource["engine_num_requests_waiting_peak"],
        "memory_peak_gib": resource["memory_used_bytes"]["max"] / 2**30
        if resource["memory_used_bytes"]["max"] is not None
        else None,
        "swap_delta_gib": resource["swap_used_bytes_delta"] / 2**30
        if resource["swap_used_bytes_delta"] is not None
        else None,
        "failures": dict(failures),
        "samples": [
            {
                "seconds": s["at"] - start,
                "cpu": s.get("cpu_percent"),
                "gpu": s.get("gpu_percent"),
                "memory": s["memory_used_bytes"] / 2**30,
                "swap": s["swap_used_bytes"] / 2**30,
            }
            for s in samples
        ],
        "run_json_sha256": hashlib.sha256((r / "results" / rid / "run.json").read_bytes()).hexdigest(),
        "model_request_count": len(terminal),
        "resource_summary": resource,
        "model_call_evidence": model_evidence,
        "prefix_cache_evidence": prefix_evidence,
        "telemetry_status": "partial_scrapes" if resource["engine_metrics_missing_samples"] else "complete",
        "ttft_p50_s": percentile(ttfts, 0.5),
        "judge_per_min": sum(i.get("judge_status") == "completed" for i in items)
        / (phase.get("judge_phase_wall_ms", 0) / 1000)
        * 60
        if phase.get("judge_phase_wall_ms", 0) > 0
        else None,
        "input_tokens_max": max((e["prompt_tokens"] for e in terminal), default=0),
        "run_valid": raw.get("run_valid"),
    }
    save(stage / "public-summary.json", value)
    return value


def publish() -> None:
    reports: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for entry in state["rounds"]:
        p = r / "rounds" / entry["label"] / "public-summary.json"
        if p.exists():
            destination = diagnostics if entry.get("purpose") == "diagnostic" else reports
            destination.append(json.loads(p.read_text()))
    report = {
        "schema_version": 1,
        "execution_revision": state.get("inference_revision", state["revision"]),
        "reporting_revision": state["revision"],
        "updated_at": datetime.now(UTC).isoformat(),
        "hardware": "Apple M4 · 16 GB · macOS 26.6",
        "engine": state.get("engine_label", "vllm-mlx 0.6.0 / mlx-vlm 0.7.6"),
        "baseline_rounds": [
            json.loads((r / "rounds" / x / "public-summary.json").read_text())
            for x in state.get("baseline_labels", [])
        ],
        "prior_optimization_rounds": [
            json.loads((r / "rounds" / label / "public-summary.json").read_text())
            for label in state.get("prior_optimization_labels", [])
        ],
        "speed_gate": state.get("speed_gate"),
        "interrupted_rounds": state.get("interrupted_rounds", []),
        "baseline_reference_mode": state.get("baseline_reference_mode", "paired"),
        "model": "ModelScope Qwen3.5-4B-MLX-4bit",
        "model_sha256": "52dc943eaf6093a2313271a7a0abc36d127b2a16609a0a7c54ee1b4e4ed06cb8",
        "dataset_sha256": "73fa4512dde072fb0df9cf45ff3ebd192192db606f9764054bde002dcbf0be47",
        "context_tokens": json.loads((r / "private/photobench-config/runtime_connection.json").read_text())[
            "product_settings"
        ]["vlm_context_tokens"],
        "conditions": [
            "固定 64 题保留全部 69 轮对话；每轮恢复独立的冻结记忆库。",
            "关闭思考；30 个模型步骤；temperature 0.7 / top_p 0.8 / top_k 20 / "
            "presence_penalty 1.5 / seed 42。",
            "每轮重启推理引擎并进行同样的 4 请求预热；独立数据库、会话及模型缓存。",
            "QA 与云端 Judge 独立调度，评分排队不限制 QA。",
            "首 token 时间由模型调度器记录，包含模型队列等待；实际批次来自 token 生成批次。",
            "等待队列包含已进入调度器、仍在等待预填充的请求；不把这些请求算作实际生成批次。",
            "指标查询缺口单独展示，不替换为零；实际模型调用必须与引擎终态及 token 记录完整对应。",
            "资源曲线与汇总限定 QA 阶段；原始记录保留完整运行及评分阶段。",
            "macOS 历史 swap_in/out 字段实际为一般页面换入/出；交换存量增量不代表交换流量。"
            "优化轮次另采集 vm_stat Swapins/Swapouts。",
            "内存曲线采用 macOS 活跃及有线内存；PhotoBench 原始遥测另保留含缓存的整机占用。",
            "最高通过档要求两轮均完成、没有执行或评分错误，且监测证据完整。",
        ],
        "rounds": reports,
        "diagnostic_rounds": diagnostics
        + [
            json.loads((r / "rounds" / label / "public-summary.json").read_text())
            for label in state.get("diagnostic_labels", [])
        ],
    }
    save(r / "results/capacity-report.json", report)
    web = r / "repo/apps/photobench/web/dist"
    if web.is_dir():
        save(web / "capacity-report.json", report)


def execute(label: str, c: int, questions: int) -> dict[str, Any]:
    entry = next((x for x in state["rounds"] if x["label"] == label), None)
    if entry and entry.get("status") == "completed":
        return read_completed_round(
            r / "rounds" / label / "public-summary.json",
            state.get("inspected_failure_sha256", {}).get(label),
        )
    if entry and entry.get("status") == "observing" and entry.get("run_id"):
        stage = r / "rounds" / label
        checkpoint(status="running", current_round=label)
        observe_run(
            "http://127.0.0.1:18772", "http://127.0.0.1:18100", stage, entry["run_id"], deadline_s=86400
        )
        summary = summarize(entry)
        entry.update(status="completed", passed=summary["passed"])
        checkpoint()
        publish()
        if not summary["passed"]:
            raise RuntimeError("round failed; inspect preserved records: " + label)
        return summary
    if entry:
        raise RuntimeError("existing incomplete round needs explicit inspection; no automatic replay")
    entry = {
        "label": label,
        "concurrency": c,
        "questions": questions,
        "status": "preparing",
        "trial": int(label.split("-")[1][1:]) if "-R" in label else None,
    }
    state["rounds"].append(entry)
    checkpoint(status="running", current_round=label)
    stop_round()
    restart_engine()
    subprocess.run([str(python), str(r / "repo/scripts/benchmarks/prepare_mlx_round.py"), label], check=True)
    stage = r / "rounds" / label
    save(stage / "warmup.json", warmup())
    identity = json.loads((r / "source200/runtime/memory-baseline-identity.json").read_text())
    payload = {
        "album_id": "album3-max-video10",
        "qa_set": "mlx-fixed64" if questions == 64 else "mlx-full487",
        "models": ["__current__"],
        "endpoint_model": "qwen35-4b-mlx-q4",
        "mode": "reuse",
        "existing_scope_id": identity["scope_id"],
        "sentrix_url": "http://127.0.0.1:18191",
        "model_base_url": "http://127.0.0.1:18100/v1",
        "runtime_framework": "mlx",
        "qa_concurrency": c,
        "judge_backpressure": False,
        "turn_timeout": 4000,
    }
    entry["status"] = "submitting"
    checkpoint()
    run_id = submit_run_once(client, stage / "submission.json", payload)
    entry.update(run_id=run_id, status="observing")
    checkpoint()
    observe_run("http://127.0.0.1:18772", "http://127.0.0.1:18100", stage, entry["run_id"], deadline_s=86400)
    summary = summarize(entry)
    entry.update(status="completed", passed=summary["passed"])
    checkpoint()
    publish()
    if not summary["passed"]:
        raise RuntimeError("round failed; preserve records and repair before retesting: " + label)
    return summary


try:
    manifest = json.loads((r / "evidence/source-code-manifest.json").read_text())
    if not all(
        hashlib.sha256((r / "repo" / p).read_bytes()).hexdigest() == h for p, h in manifest["files"].items()
    ):
        raise RuntimeError("frozen source manifest mismatch")
    pre = json.loads((r / "evidence/preflight-result.json").read_text())
    assert pre["status"] == "completed" and pre["run_valid"] is True
    assert json.loads((r / "evidence/window-seed-qualification.json").read_text())["window_passed"]
    assert json.loads((r / "evidence/engine-qualification.json").read_text())["all_passed"]
    tiers = [16] if state.get("protocol") == "optimized_c16" else [4, 8, 12, 16]
    for c in tiers:
        for trial in [1, 2]:
            execute(f"C{c}-R{trial}-{series}", c, 64)
    passing = [
        c
        for c in tiers
        if all(
            next(x for x in state["rounds"] if x["label"] == f"C{c}-R{trial}-{series}").get("passed")
            for trial in [1, 2]
        )
    ]
    if not passing:
        raise RuntimeError("no accepted concurrency tier")
    if state.get("protocol") == "optimized_c16":
        baseline = []
        for label, expected in state["baseline_summary_sha256"].items():
            path = r / "rounds" / label / "public-summary.json"
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("baseline changed since speed gate was declared")
            baseline.append(json.loads(path.read_text()))
        candidate = [
            json.loads((r / "rounds" / f"C16-R{trial}-{series}" / "public-summary.json").read_text())
            for trial in [1, 2]
        ]
        single_reference = state.get("baseline_reference_mode") == "single_completed_trial_user_cancel"
        if single_reference:
            receipt_path = r / "evidence/baseline-user-cancellation.json"
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != state["baseline_interruption_sha256"]:
                raise RuntimeError("baseline interruption evidence changed")
            receipt = json.loads(receipt_path.read_text())
            interrupted = next(x for x in state["interrupted_rounds"] if x["label"] == "C16-R2-v7")
            validate_user_cancelled_baseline(receipt, interrupted["run_id"])
        gate = optimization_speed_gate(baseline, candidate, allow_single_baseline=single_reference)
        checkpoint(speed_gate=gate, highest_passing=16)
        publish()
        if not gate["passed"]:
            raise RuntimeError("C16 speed gate not met; preserve both trials and optimize before487")
    execute(f"C{max(passing)}-FULL487-{series}", max(passing), 487)
    checkpoint(status="evaluation_complete", highest_passing=max(passing))
    publish()
except BaseException as error:
    checkpoint(status="needs_attention", error=repr(error), traceback=traceback.format_exc())
    raise
finally:
    client.close()
