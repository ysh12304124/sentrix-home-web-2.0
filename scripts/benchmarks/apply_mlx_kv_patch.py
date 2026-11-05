"""Apply the exact upstream mlx-vlm #2510 patch to an isolated 0.7.6 environment.

No running process is touched. Reject any source other than the inspected
0.7.6 file; retain provenance and validate the complete resulting file hash.
"""

import hashlib
import importlib.util
import subprocess
from pathlib import Path

BEFORE = "bdeb2cb3e01338b9cfdb2a5fac7f021265dda62d81d75b952136d38839d4b953"
AFTER = "55f5a1afbf966f34ce04e31635b846ba628f79397a8bbf0126aacac8a933f7e8"
SOURCE = "https://github.com/Neywa-Labs/mlx-vlm/pull/2510"


def main() -> None:
    spec = importlib.util.find_spec("mlx_vlm")
    if spec is None or spec.origin is None:
        raise RuntimeError("mlx-vlm is not installed in the target environment")
    target = Path(spec.origin).parent / "models/qwen3_5/language.py"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest == AFTER:
        print("upstream #2510 already applied and verified")
        return
    if digest != BEFORE:
        raise RuntimeError("unexpected mlx-vlm source; refusing to patch")
    patch = Path(__file__).with_name("patches") / "mlx-vlm-0.7.6-qwen35-strided-kv.patch"
    subprocess.run(["patch", "--dry-run", str(target), str(patch)], check=True)
    subprocess.run(["patch", str(target), str(patch)], check=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != AFTER:
        raise RuntimeError("patched source differs from inspected upstream change")
    print({"source": SOURCE, "before_sha256": BEFORE, "after_sha256": AFTER})


if __name__ == "__main__":
    main()
