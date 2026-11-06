"""Start the fixed MLX engine in an isolated experiment tree; record its PID."""

import json
import os
import subprocess
import time
from pathlib import Path

root = Path(os.environ["SENTRIX_MLX_EXPERIMENT_ROOT"]).resolve()
settings = json.loads((root / "private/photobench-config/runtime_connection.json").read_text())[
    "product_settings"
]
context = str(settings["vlm_context_tokens"])
# Match the shorter caller deadline instead of aborting active generation early.
request_timeout = str(min(settings["chat_timeout_s"], settings["vlm_timeout_s"]))
args = [
    str(root / os.environ.get("SENTRIX_MLX_ENGINE_ENV", "envs/vllm-mlx") / "bin/python"),
    str(root / "repo/scripts/benchmarks/serve_mlx.py"),
    "serve",
    str(root / "models/Qwen3.5-4B-MLX-4bit-hf"),
    "--mllm",
    "--continuous-batching",
    "--max-num-seqs",
    "16",
    "--prefill-batch-size",
    "4",
    "--completion-batch-size",
    "16",
    "--mllm-prefill-step-size",
    "512",
    "--chunked-prefill-tokens",
    "512",
    "--disable-prefix-cache",
    "--max-tokens",
    context,
    "--max-request-tokens",
    context,
    "--served-model-name",
    "qwen35-4b-mlx-q4",
    "--host",
    "127.0.0.1",
    "--port",
    "18100",
    "--enable-auto-tool-choice",
    "--tool-call-parser",
    "qwen3_coder",
    "--reasoning-parser",
    "qwen3",
    "--enable-metrics",
    "--default-chat-template-kwargs",
    '{"enable_thinking":false}',
    "--timeout",
    request_timeout,
]
if "shared_prefix" in os.environ.get("SENTRIX_MLX_OPTIMIZATIONS", "").split(","):
    from scripts.benchmarks.mlx_prefix_cache import CACHE_MEMORY_MB

    args.remove("--disable-prefix-cache")
    args.extend(["--cache-memory-mb", str(CACHE_MEMORY_MB)])
log = (root / "evidence/vllm-mlx-server.log").open("ab")
env = {
    **os.environ,
    "PYTHONPATH": str(root / "repo"),
    "SENTRIX_MLX_OPTIMIZATIONS": os.environ.get("SENTRIX_MLX_OPTIMIZATIONS", ""),
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
    "TOKENIZERS_PARALLELISM": "false",
    "MLX_BUFFER_CACHE_LIMIT": "512mb",
    "SENTRIX_MLX_EVENTS": str(root / "evidence/model-events.jsonl"),
    "SENTRIX_MLX_CONTEXT_TOKENS": context,
}
p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=env, cwd=root)
(root / "runtime/vllm-mlx-process.json").write_text(
    json.dumps(
        {
            "pid": p.pid,
            "argv": args,
            "started_at": time.time(),
            "environment": {
                k: env.get(k, "envs/vllm-mlx" if k == "SENTRIX_MLX_ENGINE_ENV" else "")
                for k in [
                    "SENTRIX_MLX_OPTIMIZATIONS",
                    "SENTRIX_MLX_ENGINE_ENV",
                    "MLX_BUFFER_CACHE_LIMIT",
                    "SENTRIX_MLX_EVENTS",
                    "TOKENIZERS_PARALLELISM",
                    "NO_PROXY",
                    "no_proxy",
                ]
            },
        },
        indent=2,
    )
)
print("started", p.pid)
