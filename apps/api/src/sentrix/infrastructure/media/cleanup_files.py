"""Managed-file removal through no-follow directory descriptors, never recursive rmtree."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


class UnsafeMediaFile(ValueError):
    pass


def file_identity(path: Path) -> dict[str, int] | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
    }


def managed_relative(data_dir: Path, path: Path) -> Path:
    # Lexical containment is checked before touching a possibly hostile path.
    if not path.is_absolute() or ".." in path.parts:
        raise UnsafeMediaFile("noncanonical_or_relative_path")
    try:
        relative = path.relative_to(data_dir)
    except ValueError as error:
        raise UnsafeMediaFile("outside_managed_root") from error
    if len(relative.parts) < 2 or relative.parts[0] not in {"media", "previews", "video-jobs"}:
        raise UnsafeMediaFile("outside_managed_root")
    # Explicitly forbid datasets even when somebody places them under a media root.
    if any(part.lower() in {"datasets", "dataset", "photobench"} for part in relative.parts):
        raise UnsafeMediaFile("protected_dataset_path")
    return relative


def unlink_managed(data_dir: Path, locator: dict[str, Any]) -> None:
    path = Path(locator["path"])
    relative = managed_relative(data_dir, path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(data_dir, flags)
    try:
        for component in relative.parts[:-1]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = child
        try:
            current = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(current.st_mode):
            raise UnsafeMediaFile("symlink_or_nonregular_file")
        actual = {
            "device": current.st_dev,
            "inode": current.st_ino,
            "size": current.st_size,
            "mtime_ns": current.st_mtime_ns,
        }
        if locator.get("stat") is None or actual != locator["stat"]:
            raise UnsafeMediaFile("file_changed_since_inventory")
        os.unlink(relative.name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
