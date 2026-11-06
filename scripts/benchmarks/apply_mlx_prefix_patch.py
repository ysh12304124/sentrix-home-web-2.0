"""Install #842/#843, cache observations and native final-token prefill scheduling.

Use only in a stopped, isolated environment. Stage and verify both files before
replacing either installed file; reject unknown sources instead of best-effort patching.
"""

import hashlib
import importlib.util
import subprocess
import tempfile
from pathlib import Path

BEFORE = {
    "memory_cache.py": "c45f46590723d4170ec1066d53ae487fed67f4c0544f91f9c4659aa0b940b6c6",
    "mllm_batch_generator.py": "592e1955cd00fd997ab52bf841b61cc5ee8c21871b0268b4545b8db0cd830e98",
}
PREFIX_AFTER = {
    "memory_cache.py": "e6bf6b48ac344067f638db9f8b4fdf28e974b327381c24d05535aba3fd04ecb1",
    "mllm_batch_generator.py": "d3f1991ce490067344c0e51091df289e53db8f262fc4cad8206a0a995dce9d8f",
}
AFTER = {
    **PREFIX_AFTER,
    "mllm_batch_generator.py": "1b2e2be41f49a368ed8697ef27f848436f160c8c55ed5ebc9153ce814ef553b5",
}


def file_hashes(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in BEFORE}


def apply_patches(directory: Path) -> None:
    actual = file_hashes(directory)
    if actual == AFTER:
        return
    if actual not in (BEFORE, PREFIX_AFTER):
        raise RuntimeError("unexpected vllm-mlx source; refusing to patch")
    patches = Path(__file__).with_name("patches")
    with tempfile.TemporaryDirectory(prefix="sentrix-prefix-") as temporary:
        stage = Path(temporary) / "vllm_mlx"
        stage.mkdir()
        for name in BEFORE:
            (stage / name).write_bytes((directory / name).read_bytes())
        changes = ["842", "843", "observation"] if actual == BEFORE else []
        patch_names = [f"vllm-mlx-0.6.0-prefix-{change}.patch" for change in changes]
        patch_names.append("vllm-mlx-0.6.0-final-token.patch")
        for name in patch_names:
            subprocess.run(
                ["patch", "--batch", "-p1", "-i", str(patches / name)],
                cwd=temporary,
                check=True,
                capture_output=True,
            )
        if file_hashes(stage) != AFTER:
            raise RuntimeError("staged prefix-cache source differs from inspected hashes")
        for name in BEFORE:
            temporary_file = directory / (name + ".sentrix-tmp")
            temporary_file.write_bytes((stage / name).read_bytes())
            temporary_file.replace(directory / name)


def main() -> None:
    spec = importlib.util.find_spec("vllm_mlx")
    if spec is None or spec.origin is None:
        raise RuntimeError("vllm-mlx is not installed in the target environment")
    apply_patches(Path(spec.origin).parent)
    print(
        {
            "before": BEFORE,
            "after": AFTER,
            "upstream_prs": [842, 843],
            "local_compatibility": "final-token prefill scheduling matches native mlx-vlm",
        }
    )


if __name__ == "__main__":
    main()
