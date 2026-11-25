"""Stage committed read code and publish static assets without restarting evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
SOURCE = BASE / "qwen-live-list-8b6da36/evaluation"
WEB = BASE / "full-lazy-18146f7/web-dist"
PYTHON = "/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python"
UNITS = [
    "sentrix-atm100-photobench.service",
    "sentrix-atm100-doubao-photobench.service",
    "sentrix-atm100-api.service",
    "sentrix-atm100-doubao-api.service",
    "sentrix-atm100-model.service",
    "sentrix2-api.service",
    "sentrix2-photobench.service",
]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def pids():
    return {
        unit: subprocess.check_output(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], text=True
        ).strip()
        for unit in UNITS
    }


def stage():
    info = json.loads((HERE / "manifest.json").read_text())
    assert not (HERE / "stage-intent.json").exists()
    assert not (HERE / "evaluation").exists()
    for path, digest in info["baseline_files"].items():
        assert sha(SOURCE / path) == digest, f"remote baseline changed: {path}"
    for path, digest in info["payload_files"].items():
        assert sha(HERE / "payload" / path) == digest, f"payload mismatch: {path}"
    before = pids()
    save("stage-intent.json", {"revision": info["revision"], "protected_pids": before})
    shutil.copytree(
        SOURCE,
        HERE / "evaluation",
        copy_function=os.link,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    for path in info["payload_files"]:
        if path.startswith("web-dist/"):
            continue
        target = HERE / "evaluation" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".new")
        shutil.copyfile(HERE / "payload" / path, temporary)
        os.replace(temporary, target)
    assert before == pids()
    for path, digest in info["payload_files"].items():
        if not path.startswith("web-dist/"):
            assert sha(HERE / "evaluation" / path) == digest
    save(
        "stage-response.json",
        {
            "revision": info["revision"],
            "source_sha_verified": True,
            "protected_pids_unchanged": True,
            "backend_activated": False,
        },
    )


def publish_static():
    info = json.loads((HERE / "manifest.json").read_text())
    assert (HERE / "stage-response.json").exists()
    assert (HERE / "real-read-response.json").exists()
    assert not (HERE / "static-intent.json").exists()
    before = pids()
    for unit in UNITS[:2]:
        values = Path(f"/proc/{before[unit]}/environ").read_bytes().split(b"\0")
        assert f"PHOTOBENCH_WEB_DIST_ROOT={WEB}".encode() in values
    paths = sorted(
        path
        for path in info["payload_files"]
        if path.startswith("web-dist/") and path != "web-dist/index.html"
    )
    paths.append("web-dist/index.html")
    for path in paths:
        assert sha(HERE / "payload" / path) == info["payload_files"][path]
    shutil.copyfile(WEB / "index.html", HERE / "previous-index.html")
    save(
        "static-intent.json",
        {
            "revision": info["revision"],
            "protected_pids": before,
            "previous_index_sha256": sha(WEB / "index.html"),
        },
    )
    for path in paths:
        destination = WEB / Path(path).relative_to("web-dist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".new")
        shutil.copyfile(HERE / "payload" / path, temporary)
        os.replace(temporary, destination)
        assert sha(destination) == info["payload_files"][path]
    assert before == pids()
    save(
        "static-response.json",
        {
            "revision": info["revision"],
            "ports": [8872, 8874],
            "web_root": str(WEB),
            "protected_pids_unchanged": True,
            "backend_activated": False,
            "index_sha256": sha(WEB / "index.html"),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "publish-static"])
    options = parser.parse_args()
    stage() if options.action == "stage" else publish_static()
