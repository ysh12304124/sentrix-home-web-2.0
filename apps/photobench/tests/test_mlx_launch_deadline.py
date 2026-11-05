"""The benchmark engine and its callers share the configured request deadline."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("chat,vision,expected", [(1800, 1800, 1800), (120, 240, 120), (240, 120, 120)])
def test_engine_uses_shorter_caller_deadline(tmp_path, monkeypatch, chat, vision, expected):
    root = tmp_path
    for directory in ("private/photobench-config", "runtime", "evidence"):
        (root / directory).mkdir(parents=True)
    (root / "private/photobench-config/runtime_connection.json").write_text(
        json.dumps(
            {
                "product_settings": {
                    "vlm_context_tokens": 32768,
                    "chat_timeout_s": chat,
                    "vlm_timeout_s": vision,
                }
            }
        )
    )
    monkeypatch.setenv("SENTRIX_MLX_EXPERIMENT_ROOT", str(root))
    monkeypatch.setenv("SENTRIX_MLX_OPTIMIZATIONS", "")
    calls = []
    monkeypatch.setattr(
        "subprocess.Popen", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(pid=12345)
    )
    runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/benchmarks/launch_mlx_engine.py"))
    assert len(calls) == 1
    argv = calls[0]
    assert float(argv[argv.index("--timeout") + 1]) == expected
    record = json.loads((root / "runtime/vllm-mlx-process.json").read_text())
    assert record["argv"] == argv
