"""Bounded, exact-token prefix reuse for the fixed Qwen3.5 benchmark.

The upstream hybrid snapshot machinery owns copies, cancellation and lookup.
This policy keeps original chat tokens and checkpoints a shared prefix instead
of retaining a separate growing snapshot for each question. No KV quantization,
prompt truncation, approximate matches or cross-round disk persistence.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

PREFIX_TOKENS = 1024
CACHE_MEMORY_MB = 1024


def shared_boundary(original_boundary: int | None) -> int | None:
    """Retain only an exact prefix ending before the generation think suffix."""
    return min(original_boundary, PREFIX_TOKENS) if original_boundary is not None else None


def install_prefix_policy(generator: Any) -> None:
    original_boundary = generator._conversation_boundary
    original_init = generator.__init__

    def boundary(instance: Any, token_ids: list[int]) -> int | None:
        return shared_boundary(original_boundary(instance, token_ids))

    def preserve_template(instance: Any) -> None:
        # Upstream normalizes historical think blocks when caching is enabled.
        # Keeping the supplied template is part of benchmark input identity.
        return None

    @functools.wraps(original_init)
    def initialize(instance: Any, *args: Any, **kwargs: Any) -> None:
        original_init(instance, *args, **kwargs)
        cache = instance.prefix_cache
        if cache is None or cache.memory_limit_mb != CACHE_MEMORY_MB or cache._config.kv_quantize:
            raise RuntimeError("shared-prefix profile requires a 1024 MiB unquantized cache")

    generator._conversation_boundary = boundary
    generator._normalize_chat_template_for_prefix_cache = preserve_template
    generator.__init__ = initialize


def install_prefix_cache() -> None:
    from scripts.benchmarks.apply_mlx_prefix_patch import AFTER, file_hashes

    module: Any = importlib.import_module("vllm_mlx.mllm_batch_generator")
    if module.__file__ is None or file_hashes(Path(module.__file__).parent) != AFTER:
        raise RuntimeError("prefix-cache patches do not match the qualified source")
    install_prefix_policy(module.MLLMBatchGenerator)
    destination = os.environ.get("SENTRIX_MLX_EVENTS")
    if not destination:
        raise RuntimeError("prefix-cache profile requires request-level evidence")
    path = Path(destination)
    lock = threading.Lock()

    def observe(request: Any, cached_tokens: int) -> None:
        total = int(request.input_ids.size)
        if not 0 <= cached_tokens <= total:
            raise RuntimeError("invalid accepted prefix-cache token count")
        event = {
            "at": time.time(),
            "event": "request_prefill",
            "request_id": request.request_id,
            "prompt_tokens": total,
            "cached_tokens": cached_tokens,
        }
        with lock, path.open("a") as stream:
            stream.write(json.dumps(event) + "\n")

    module.MLLMBatchGenerator._sentrix_prefix_observer = staticmethod(observe)
    server: Any = importlib.import_module("vllm_mlx.server")

    async def no_disk_cache(engine: Any = None) -> None:
        # Each formal round starts with an empty cache and the same warm-up.
        return None

    server._load_prefix_cache_from_disk = no_disk_cache
    server._save_prefix_cache_to_disk = no_disk_cache
