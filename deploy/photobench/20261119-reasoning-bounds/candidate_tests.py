"""Test committed payload in the inactive candidate, without using product data."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / "manifest.json").read_text())
release = HERE / "release"
product = release / "q4km"
evaluation = release / "evaluation"
python = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
for relative, row in manifest["test_files"].items():
    source = HERE / "payload" / relative
    assert hashlib.sha256(source.read_bytes()).hexdigest() == row["after"]
    target = product / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
tests = [
    "apps/api/tests/unit/test_request_budget.py",
    "apps/api/tests/unit/test_assistant_policy.py",
    "apps/api/tests/unit/test_assistant_task_state.py",
    "apps/api/tests/unit/test_container_wiring.py",
    "apps/api/tests/integration/test_turn_dispatch.py",
    "apps/api/tests/integration/test_run_turn.py",
    "apps/api/tests/integration/test_chat_client.py",
    "apps/api/tests/contract/test_assistant_routes.py",
    "apps/api/tests/contract/test_assistant_events.py",
]
environment = {**os.environ, "PYTHONPATH": str(product / "apps/api/src")}
with (HERE / "candidate-product-tests.log").open("x") as log:
    subprocess.run(
        [python, "-m", "pytest", "-o", "addopts=", "-q", *tests],
        cwd=product,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )
tests = [
    "apps/photobench/tests/test_config_service.py",
    "apps/photobench/tests/test_run_read.py",
    "apps/photobench/tests/test_trace_checkpoint_reuse.py",
]
environment = {**os.environ, "PYTHONPATH": str(evaluation / "apps/photobench/src")}
with (HERE / "candidate-evaluation-tests.log").open("x") as log:
    subprocess.run(
        [python, "-m", "pytest", "-o", "addopts=", "-q", *tests],
        cwd=evaluation,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )
for stage in ["q4km", "qwen37"]:
    for relative, row in manifest["product_files"].items():
        assert hashlib.sha256((release / stage / relative).read_bytes()).hexdigest() == row["after"]
for relative, row in manifest["evaluation_files"].items():
    assert hashlib.sha256((evaluation / relative).read_bytes()).hexdigest() == row["after"]
result = {
    "source_revision": manifest["source_revision"],
    "sha256_verified": True,
    "product": (HERE / "candidate-product-tests.log").read_text()[-1000:],
    "evaluation": (HERE / "candidate-evaluation-tests.log").read_text()[-1000:],
}
with (HERE / "remote-tests-passed.json").open("x") as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(result))
