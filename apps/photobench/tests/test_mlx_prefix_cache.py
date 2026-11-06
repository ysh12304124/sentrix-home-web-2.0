"""Policy/guard regressions; real cache-state parity is qualified on the Mac."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "mlx_prefix_cache", ROOT / "scripts/benchmarks/mlx_prefix_cache.py"
)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
patch_spec = importlib.util.spec_from_file_location(
    "mlx_prefix_patch", ROOT / "scripts/benchmarks/apply_mlx_prefix_patch.py"
)
assert patch_spec and patch_spec.loader
patcher = importlib.util.module_from_spec(patch_spec)
patch_spec.loader.exec_module(patcher)


def test_prefix_policy_preserves_template_and_never_enters_generation_suffix():
    class Generator:
        def __init__(self):
            self.template = "original historical think blocks"
            self._normalize_chat_template_for_prefix_cache()
            self.prefix_cache = SimpleNamespace(
                memory_limit_mb=1024, _config=SimpleNamespace(kv_quantize=False)
            )

        def _normalize_chat_template_for_prefix_cache(self):
            self.template = "rewritten history"

        def _conversation_boundary(self, tokens):
            return len(tokens) - 4 if len(tokens) > 4 else None

    policy.install_prefix_policy(Generator)
    generator = Generator()
    assert generator.template == "original historical think blocks"
    for length, expected in [(4, None), (25, 21), (1024, 1020), (1028, 1024), (32768, 1024)]:
        tokens = list(range(length))
        assert generator._conversation_boundary(tokens) == expected
        assert tokens == list(range(length))


@pytest.mark.parametrize("memory,quantized", [(None, False), (2048, False), (1024, True)])
def test_unqualified_cache_config_rejected(memory, quantized):
    class Generator:
        def __init__(self):
            self.prefix_cache = SimpleNamespace(
                memory_limit_mb=memory, _config=SimpleNamespace(kv_quantize=quantized)
            )

        def _conversation_boundary(self, tokens):
            return len(tokens) - 4

    policy.install_prefix_policy(Generator)
    with pytest.raises(RuntimeError, match="1024 MiB unquantized"):
        Generator()


def test_patch_rejects_unknown_source_without_mutation(tmp_path):
    for name in patcher.BEFORE:
        (tmp_path / name).write_text("unexpected local source")
    before = patcher.file_hashes(tmp_path)
    with pytest.raises(RuntimeError, match="unexpected vllm-mlx source"):
        patcher.apply_patches(tmp_path)
    assert patcher.file_hashes(tmp_path) == before
