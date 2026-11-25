"""Lossless JSON checkpoint encoding; readers accept plain and gzip generations."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, TextIO, cast


def snapshot_file(directory: Path, name: str) -> Path:
    plain = directory / name
    return plain if plain.exists() else directory / (name + ".gz")


def open_snapshot(path: Path, mode: str = "r") -> TextIO:
    if path.suffix == ".gz":
        return cast(TextIO, gzip.open(path, mode + "t", encoding="utf-8", compresslevel=1))
    return cast(TextIO, path.open(mode, encoding="utf-8"))


def read_snapshot(path: Path) -> dict[str, Any]:
    with open_snapshot(path) as handle:
        return cast(dict[str, Any], json.load(handle))
