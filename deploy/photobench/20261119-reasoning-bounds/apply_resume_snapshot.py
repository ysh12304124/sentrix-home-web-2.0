"""Activate the committed resume-snapshot correction in the idle evaluation pair."""

import ast
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    spec = importlib.util.spec_from_file_location("bounded_release", HERE / "release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    before = release.inspect()
    release.require_idle(before)
    evaluation = HERE / "release/evaluation"
    relative = "apps/photobench/src/photobench/application/runs/suites.py"
    target = evaluation / relative
    assert release.sha(target) == "30f79223cc229052e477365d43e4e663dadb2ab0b48b05b2a57b1ce7d63d9eaa"
    release.save("resume-snapshot-intent.json", before)
    shutil.copy2(target, HERE / "suites-before-snapshot.py")
    for extra in (["--dry-run"], []):
        subprocess.run(
            [
                "patch",
                "--batch",
                "--forward",
                "--fuzz=0",
                "-p1",
                *extra,
                "-i",
                str(HERE / "freeze-resume-limits.patch"),
            ],
            cwd=evaluation,
            check=True,
            timeout=10,
        )
    original = ast.parse((HERE / "suites-before-snapshot.py").read_text())
    changed = ast.parse(target.read_text())

    def without_resume(tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resume_run":
                node.body = []
        return ast.dump(tree)

    assert without_resume(original) == without_resume(changed)
    test = "apps/photobench/tests/test_suites.py"
    shutil.copy2(HERE / "test_suites_snapshot.py", evaluation / test)
    with (HERE / "resume-snapshot-tests.log").open("x") as log:
        subprocess.run(
            [release.PYTHON, "-m", "pytest", "-o", "addopts=", "-q", test],
            cwd=evaluation,
            env={**os.environ, "PYTHONPATH": str(evaluation / "apps/photobench/src")},
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    for port, unit, _ in release.EVALUATORS.values():
        subprocess.run(["systemctl", "--user", "restart", unit], check=True, timeout=80)
        release.wait(port)
    after = release.inspect()
    release.require_idle(after)
    assert after["protected"] == before["protected"]
    assert after["source_runs"] == before["source_runs"]
    assert {s: p["pid"] for s, p in after["products"].items()} == {
        s: p["pid"] for s, p in before["products"].items()
    }
    release.save(
        "resume-snapshot-response.json",
        {
            "suites_sha256": release.sha(target),
            "after": after,
            "tests": (HERE / "resume-snapshot-tests.log").read_text()[-1000:],
        },
    )
    print("Resume snapshot correction activated; product and protected PIDs unchanged.")


if __name__ == "__main__":
    main()
