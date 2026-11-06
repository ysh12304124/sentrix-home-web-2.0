#!/usr/bin/env python3
"""Version-guarded vllm-mlx benchmark entry point (explicit window, request seed 42).

Upstream 0.6.0 has neither exact multimodal /tokenize nor a request seed.
This adapter uses its own preprocessing path and gives each sampler an explicit
MLX random key. It does not rotate KV, trim prompts, or serialize inference.
Only the fixed benchmark seed is accepted; other seeds fail visibly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import threading
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

WINDOW = int(os.environ.get("SENTRIX_MLX_CONTEXT_TOKENS", "32768"))
SEED = 42


def processor_context(
    requests: list[Any], histories: list[list[int]] | None, pending: list[list[int]]
) -> list[list[int]] | None:
    """Repair 0.6.0's chunked decoder context, preserving the ordinary path.

    The ordinary decoder passes a copy including the current input token. The
    chunked decoder passes the request's history object, which excludes that
    pending token. Grammar and repetition processors need the same complete
    history on both paths; do not mutate the request before it emits the token.
    """
    if histories is None:
        return None
    return [
        history + token if history is request.output_tokens else history
        for request, history, token in zip(requests, histories, pending, strict=True)
    ]


def install_chunked_context_fix() -> None:
    generator: Any = importlib.import_module("vllm_mlx.mllm_batch_generator").MLLMBatchGenerator
    original_step = generator._step

    def step(
        instance: Any,
        input_tokens: Any,
        cache: Any,
        logits_processors: Any = None,
        output_tokens: Any = None,
        samplers: Any = None,
        rope_deltas: Any = None,
    ) -> Any:
        if output_tokens is not None and instance.active_batch is not None:
            pending = input_tokens.tolist()
            if input_tokens.ndim == 1:
                pending = [[token] for token in pending]
            output_tokens = processor_context(instance.active_batch.requests, output_tokens, pending)
        return original_step(
            instance, input_tokens, cache, logits_processors, output_tokens, samplers, rope_deltas
        )

    generator._step = step


def process_queued_prompts(instance: Any, requests: list[Any], process: Callable[[], Any]) -> Any:
    """Consume each successfully handled prefill admission exactly once.

    0.6.0's inline short-prompt path during a partial prefill omits the queue
    removal performed by its ordinary path. It can repeatedly append the same
    request to the decoding batch. Capture IDs before preprocessing removes
    failed inputs, and consume both successful and explicitly failed inputs.
    Exceptions retain the caller's existing error handling.
    """
    requested_uids = {request.uid for request in requests}
    result = process()
    instance.unprocessed_requests = [
        request for request in instance.unprocessed_requests if request.uid not in requested_uids
    ]
    return result


def install_prefill_admission_fix() -> None:
    generator: Any = importlib.import_module("vllm_mlx.mllm_batch_generator").MLLMBatchGenerator
    original_process = generator._process_prompts

    def process(instance: Any, requests: list[Any]) -> Any:
        return process_queued_prompts(instance, requests, lambda: original_process(instance, requests))

    generator._process_prompts = process


def canonical_generation_step(instance: Any, canonical: Callable[[], Any]) -> Any:
    """Bound ordinary admission, including text joining an existing decode batch.

    In 0.6.0, both ordinary selectors use completion_batch_size as the number
    of NEW requests, without subtracting the active batch. This attribute is
    only used by those selectors on this path. Restore it before returning to
    chunked scheduling; existing decoders and waiting requests remain intact.
    """
    capacity = instance.completion_batch_size
    active = len(instance.active_batch) if instance.active_batch is not None else 0
    instance.completion_batch_size = min(instance.prefill_batch_size, max(0, capacity - active))
    try:
        return canonical()
    finally:
        instance.completion_batch_size = capacity


def install_canonical_admission_fix() -> None:
    generator: Any = importlib.import_module("vllm_mlx.mllm_batch_generator").MLLMBatchGenerator
    canonical = generator._next

    def next_batch(instance: Any) -> Any:
        return canonical_generation_step(instance, lambda: canonical(instance))

    # Install before engine construction: its chunked fallback captures _next.
    generator._next = next_batch


def install_nonthinking_json_fix(server: Any) -> None:
    """Align JSON instructions with this adapter's enforced nonthinking mode.

    Upstream uses the presence of a reasoning *parser* to allow reasoning in
    its JSON instructions, even with enable_thinking=False. Keep the parser,
    schema and original messages; select the existing strict instruction.
    """
    original = server.build_json_system_prompt

    def build(response_format: Any, *, thinking_model: bool = False) -> Any:
        return original(response_format, thinking_model=False)

    server.build_json_system_prompt = build


def fair_generation_step(instance: Any, chunked: Callable[[], Any], canonical: Callable[[], Any]) -> Any:
    """Drain the current batch when media waits; do not admit newer text forever.

    Upstream can only merge text into a decoding batch. Its chunked path also
    selects text ahead of media when idle. Preserve an in-flight prefill, then
    let the ordinary multimodal path admit the pending batch in arrival order.
    """
    if not any(r.images or r.videos or r.audio for r in instance.unprocessed_requests):
        return chunked()
    allowed = instance._allow_mid_batch_extend
    instance._allow_mid_batch_extend = False
    try:
        if not instance.active_batch and getattr(instance, "_partial", None) is None:
            # insert() sorts text ahead of media. Draining alone does not
            # prevent starvation: replenished text can fill every bounded
            # ordinary admission. UIDs are assigned monotonically on insert,
            # so restore arrival order before selecting the next batch.
            instance.unprocessed_requests.sort(key=lambda request: request.uid)
            # The ordinary 0.6 path uses completion_batch_size for admission,
            # ignoring its smaller configured vision prefill limit. A mixed
            # batch can retain many per-request caches and media tensors before
            # the first decode. Bound that admission only; text decoding keeps
            # its original capacity, and no waiting request is discarded.
            decode_capacity = instance.completion_batch_size
            instance.completion_batch_size = min(decode_capacity, instance.prefill_batch_size)
            try:
                return canonical()
            finally:
                instance.completion_batch_size = decode_capacity
        return chunked()
    finally:
        instance._allow_mid_batch_extend = allowed


def install_media_fairness_fix() -> None:
    module: Any = importlib.import_module("vllm_mlx.mllm_batch_generator")
    generator = module.MLLMBatchGenerator
    canonical = generator._next

    def next_batch(instance: Any) -> Any:
        with module.mx.stream(generator._stream):
            return fair_generation_step(instance, instance._next, lambda: canonical(instance))

    generator.next = next_batch


def pending_requests(scheduler: Any) -> int:
    generator = scheduler.batch_generator
    return len(scheduler.waiting) + (len(generator.unprocessed_requests) if generator is not None else 0)


def terminal_output_facts(request: Any, outputs: list[Any]) -> dict[str, Any]:
    """Observe both scheduler state and emitted usage without rewriting either."""
    finished = [o for o in outputs if o.request_id == request.request_id and o.finished]
    output = finished[0] if len(finished) == 1 else None
    return {
        "terminal_output_count": len(finished),
        "returned_prompt_tokens": output.prompt_tokens if output is not None else None,
        "returned_completion_tokens": output.completion_tokens if output is not None else None,
        "output_counters_consistent": output is not None
        and output.prompt_tokens == request.num_prompt_tokens
        and output.completion_tokens == request.num_output_tokens,
    }


def install_telemetry() -> None:
    """Record actual scheduler token batches and terminal timing, never content."""
    destination = os.environ.get("SENTRIX_MLX_EVENTS")
    if not destination:
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    scheduler: Any = importlib.import_module("vllm_mlx.mllm_scheduler").MLLMScheduler
    original_process = scheduler._process_batch_responses
    original_abort = scheduler.abort_request
    original_step = scheduler.step
    original_generate = scheduler.generate
    prometheus: Any = importlib.import_module("prometheus_client")
    pending = prometheus.Gauge(
        "sentrix_mlx_num_requests_waiting",
        "Scheduler queue plus admitted requests awaiting prefill",
        registry=None,
    )
    metrics_module: Any = importlib.import_module("vllm_mlx.metrics")
    collector = metrics_module.MetricsCollector
    original_init = collector._init_prometheus

    def init_metrics(instance: Any) -> None:
        original_init(instance)
        instance._prom["registry"].register(pending)

    collector._init_prometheus = init_metrics
    if metrics_module.metrics._prom is not None:
        metrics_module.metrics._prom["registry"].register(pending)

    def emit(event: dict[str, Any]) -> None:
        with lock, path.open("a") as stream:
            stream.write(json.dumps({"at": time.time(), **event}) + "\n")

    def terminal(request: Any, reason: str, outputs: list[Any] | None = None) -> None:
        from scripts.benchmarks.mlx_batch_decode import COUNTERS

        emit(
            {
                "event": "request_terminal",
                "decode_optimization_totals": dict(COUNTERS),
                "request_id": request.request_id,
                "finish_reason": reason,
                "arrival_at": request.arrival_time,
                "ttft_s": request.first_token_time - request.arrival_time
                if request.first_token_time is not None
                else None,
                "latency_s": time.time() - request.arrival_time,
                "prompt_tokens": request.num_prompt_tokens,
                "completion_tokens": request.num_output_tokens,
                **(terminal_output_facts(request, outputs) if outputs is not None else {}),
            }
        )

    def process(instance: Any, responses: Any) -> Any:
        result = original_process(instance, responses)
        outputs, finished = result
        size = len({output.request_id for output in outputs})
        if size != getattr(instance, "_evidence_last_batch", None):
            emit(
                {
                    "event": "token_batch",
                    "actual_batch_size": size,
                    "scheduler_running": len(instance.running),
                    "scheduler_waiting": len(instance.waiting),
                    "pending_prefill": pending_requests(instance),
                }
            )
            instance._evidence_last_batch = size
        for request_id in finished:
            request = instance.running.get(request_id)
            if request is not None:
                terminal(request, request.finish_reason or "unknown", outputs)
        return result

    def abort(instance: Any, request_id: str) -> Any:
        request = instance.requests.get(request_id)
        result = original_abort(instance, request_id)
        if result and request is not None:
            terminal(request, "abort")
        return result

    scheduler._process_batch_responses = process
    scheduler.abort_request = abort

    def step(instance: Any) -> Any:
        result = original_step(instance)
        pending.set(pending_requests(instance))
        return result

    scheduler.step = step

    async def generate(instance: Any, *args: Any, **kwargs: Any) -> Any:
        output = await original_generate(instance, *args, **kwargs)
        emit(
            {
                "event": "request_return",
                "request_id": output.request_id,
                "prompt_tokens": output.prompt_tokens,
                "completion_tokens": output.completion_tokens,
                "finish_reason": output.finish_reason,
            }
        )
        return output

    scheduler.generate = generate


def seeded_sampler(
    temp: float = 0.0,
    top_p: float = 0.0,
    min_p: float = 0.0,
    min_tokens_to_keep: int = 1,
    top_k: int = 0,
    xtc_probability: float = 0.0,
    xtc_threshold: float = 0.0,
    xtc_special_tokens: list[int] | None = None,
) -> Callable[[Any], Any]:
    """Upstream filters, with independent RNG state instead of a shared RNG."""
    mx = importlib.import_module("mlx.core")
    sampling: Any = importlib.import_module("mlx_lm.sample_utils")
    if xtc_probability != 0:
        raise ValueError("XTC is outside the frozen benchmark sampling profile")
    key = mx.random.key(SEED)

    def sample(logprobs: Any) -> Any:
        nonlocal key
        if temp == 0:
            return mx.argmax(logprobs, axis=-1)
        if 0 < top_p < 1:
            logprobs = sampling.apply_top_p(logprobs, top_p)
        if min_p != 0:
            logprobs = sampling.apply_min_p(logprobs, min_p, min_tokens_to_keep)
        if top_k > 0:
            logprobs = sampling.apply_top_k(logprobs, top_k)
        key, draw = mx.random.split(key)
        return mx.random.categorical(logprobs * (1.0 / temp), key=draw)

    return sample


def exact_count(server: Any, body: dict[str, Any]) -> int:
    """Match BatchedEngine.chat + MLLMBatchGenerator preprocessing exactly."""
    engine = server._engine
    if engine is None or not engine._loaded or not engine.is_mllm:
        raise HTTPException(503, "multimodal engine is not ready")
    utils = importlib.import_module("vllm_mlx.api.utils")
    tool_calling = importlib.import_module("vllm_mlx.api.tool_calling")
    mllm = importlib.import_module("vllm_mlx.models.mllm")
    vlm = importlib.import_module("mlx_vlm.utils")
    models = importlib.import_module("vllm_mlx.api.models")
    request = models.ChatCompletionRequest.model_validate(body)
    prepared = server._prepare_chat_completion_invocation(engine, request, 1)
    messages = prepared.messages
    _, images, videos, audios = utils.extract_multimodal_content(messages)
    if videos or audios:
        raise HTTPException(422, "this benchmark accepts video keyframes as images, not native video/audio")
    template_kwargs = prepared.chat_kwargs.get("chat_template_kwargs") or {}
    if (
        template_kwargs.get("enable_thinking", False) is not False
        or prepared.chat_kwargs.get("enable_thinking", False) is not False
    ):
        raise HTTPException(422, "thinking must be disabled for this benchmark")
    prompt = engine._apply_chat_template(
        messages,
        tool_calling.convert_tools_for_template(prepared.chat_kwargs.get("tools")),
        num_images=len(images),
        chat_template_kwargs=template_kwargs,
        enable_thinking=False,
    )
    inputs = vlm.prepare_inputs(
        engine._processor,
        images=[mllm.process_image_input(item) for item in images] or None,
        audio=None,
        prompts=prompt,
        image_token_index=getattr(engine._model.config, "image_token_index", None),
    )
    return int(inputs["input_ids"].shape[-1])


def install_adapter() -> None:
    for package, expected in {"vllm-mlx": "0.6.0", "mlx-vlm": "0.7.6", "transformers": "5.19.0"}.items():
        if version(package) != expected:
            raise RuntimeError(f"{package} must be {expected}")
    sampling: Any = importlib.import_module("mlx_lm.sample_utils")
    sampling.make_sampler = seeded_sampler
    optimizations = set(filter(None, os.environ.get("SENTRIX_MLX_OPTIMIZATIONS", "").split(",")))
    if optimizations - {"strided_kv", "batch_grammar", "shared_prefix"}:
        raise RuntimeError("unknown MLX optimization profile")
    if "shared_prefix" in optimizations:
        from scripts.benchmarks.mlx_prefix_cache import install_prefix_cache

        install_prefix_cache()
    if "strided_kv" in optimizations:
        from scripts.benchmarks.apply_mlx_kv_patch import AFTER
        from scripts.benchmarks.mlx_batch_decode import install_attention_bridge, install_kv_observer

        language = importlib.import_module("mlx_vlm.models.qwen3_5.language")
        if (
            language.__file__ is None
            or hashlib.sha256(Path(language.__file__).read_bytes()).hexdigest() != AFTER
        ):
            raise RuntimeError("upstream strided KV patch was not qualified/installed")
        install_attention_bridge(language)
        install_kv_observer(language)
    if "batch_grammar" in optimizations:
        from scripts.benchmarks.mlx_batch_decode import install_batched_decode

        install_batched_decode()
    install_chunked_context_fix()
    install_prefill_admission_fix()
    install_canonical_admission_fix()
    install_media_fairness_fix()
    install_telemetry()
    server = importlib.import_module("vllm_mlx.server")
    install_nonthinking_json_fix(server)
    app: FastAPI = server.app

    @app.post("/tokenize")
    async def tokenize(body: dict[str, Any]) -> dict[str, Any]:
        count = await run_in_threadpool(exact_count, server, body)
        return {"count": count, "max_model_len": WINDOW, "seed_policy": "per-request-42"}

    @app.middleware("http")
    async def enforce_window(request: Request, call_next: Any) -> Any:
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            try:
                body = await request.json()
                if body.get("seed", SEED) != SEED:
                    raise HTTPException(422, "only fixed benchmark seed 42 is supported")
                count = await run_in_threadpool(exact_count, server, body)
                output = body.get("max_tokens", WINDOW)
                if not isinstance(output, int) or output < 1 or count + output > WINDOW:
                    raise HTTPException(
                        400, f"context overflow: input={count}, output={output}, window={WINDOW}"
                    )
            except HTTPException as error:
                return JSONResponse({"error": {"message": error.detail}}, status_code=error.status_code)
            except (ValueError, KeyError, TypeError) as error:
                return JSONResponse({"error": {"message": str(error)}}, status_code=422)
        return await call_next(request)


if __name__ == "__main__":
    install_adapter()
    importlib.import_module("vllm_mlx.cli").main()
