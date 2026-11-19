#!/usr/bin/env python3
"""Install only hash-pinned native SenseVoice assets under SENTRIX_DATA_DIR."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from sentrix.config import load_settings

LOCK_PATH = Path(__file__).with_name("sensevoice-model.lock.json")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(archive_path: Path | None = None) -> Path:
    lock: dict[str, Any] = json.loads(LOCK_PATH.read_text())
    output = load_settings().speech_model_dir
    files: dict[str, str] = lock["files"]
    if all((output / name).is_file() and sha256(output / name) == digest for name, digest in files.items()):
        print(f"SenseVoice model ready: {output}")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem for final per-file atomic replace; never expose partial downloads.
    with tempfile.TemporaryDirectory(prefix=".sensevoice-", dir=output.parent) as temporary:
        stage = Path(temporary)
        archive = archive_path or stage / "model.tar.bz2"
        if archive_path is None:
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    "--connect-timeout",
                    "20",
                    "--max-time",
                    "1800",
                    lock["url"],
                    "--output",
                    str(archive),
                ],
                check=True,
                timeout=5500,
            )
        if sha256(archive) != lock["sha256"]:
            raise ValueError("SenseVoice archive SHA-256 mismatch; not installed")
        with tarfile.open(archive, "r:bz2") as bundle:
            for name, digest in files.items():
                member = bundle.getmember(f"{lock['archive_root']}/{name}")
                if not member.isfile():
                    raise ValueError(f"Not an ordinary file: {name}")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError(f"Missing file: {name}")
                (stage / name).parent.mkdir(parents=True, exist_ok=True)
                with source, (stage / name).open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                if sha256(stage / name) != digest:
                    raise ValueError(f"SenseVoice file SHA-256 mismatch: {name}")
        output.mkdir(exist_ok=True)
        for name in files:
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).replace(output / name)
        (output / "manifest.json").write_text(json.dumps(lock, indent=2) + "\n")
    print(f"SenseVoice model installed: {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path, help="optional already-downloaded official archive")
    args = parser.parse_args()
    prepare(args.archive)


if __name__ == "__main__":
    main()
