"""文件系统媒体存储。

文件名规则 ``{asset_id}_{原始文件名}`` 沿用 1.0（``app.py:117 _upload_destination``），
但原始文件名先过 :func:`sanitise`——1.0 只用 ``Path.name``，遇到 ``../`` 会写到媒体目录之外。
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from sentrix.domain.media.models import Asset

_COPY_CHUNK = 1024 * 1024


def sanitise(file_name: str) -> str:
    """只保留基础名，并剔除会破坏 ``{asset_id}_{name}`` 拼接的字符。"""
    base = Path(file_name or "unnamed").name
    cleaned = "".join(char for char in base if char not in {"/", "\\", "\x00"}).lstrip(".")
    return cleaned or "unnamed"


def fsync_directory(directory: Path) -> None:
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


class FileSystemMediaStorage:
    def __init__(self, media_dir: Path) -> None:
        self._media_dir = Path(media_dir)
        self._media_dir.mkdir(parents=True, exist_ok=True)

    @property
    def media_dir(self) -> Path:
        return self._media_dir

    @property
    def staging_dir(self) -> Path:
        path = self._media_dir / ".staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        destination = self._media_dir / f"{asset_id}_{sanitise(file_name)}"
        destination.write_bytes(content)
        return destination

    def move_from_path(self, asset_id: str, file_name: str, source_path: Path) -> Path:
        """同盘原子改名。调用方负责在改名后 fsync 父目录前不返回“已接收”。"""
        destination = self._media_dir / f"{asset_id}_{sanitise(file_name)}"
        if destination == source_path:
            return destination
        os.replace(source_path, destination)
        fsync_directory(self._media_dir)
        return destination

    def copy_from_path(self, asset_id: str, file_name: str, source_path: Path) -> Path:
        """目录导入用：不破坏调用方的原文件，按块拷贝后落盘。"""
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("source must be a regular file")
        destination = self._media_dir / f"{asset_id}_{sanitise(file_name)}"
        if destination == source_path:
            return destination
        with source_path.open("rb") as source, destination.open("wb") as target:
            while True:
                chunk = source.read(_COPY_CHUNK)
                if not chunk:
                    break
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        fsync_directory(self._media_dir)
        return destination

    def path_for(self, asset: Asset) -> Path:
        return Path(asset.path)

    def read(self, asset_id: str) -> bytes:
        matches = sorted(self._media_dir.glob(f"{asset_id}_*"))
        if not matches:
            raise FileNotFoundError(f"未找到 asset {asset_id} 的媒体文件")
        return matches[0].read_bytes()

    def delete_assets(self, asset_ids: Sequence[str], paths: Sequence[str]) -> tuple[str, ...]:
        """仅删除媒体根目录内受管文件及视频派生物；外部源文件保留并报告。"""
        root = self._media_dir.resolve()
        skipped: list[str] = []
        for raw in paths:
            path = Path(raw).resolve()
            if not path.is_relative_to(root):
                skipped.append(raw)
                continue
            path.unlink(missing_ok=True)
        for asset_id in asset_ids:
            if re.fullmatch(r"[A-Za-z0-9_-]+", asset_id) is None:
                raise ValueError("invalid asset ID in cleanup job")
            derived = (root / "derived" / "video" / asset_id).resolve()
            if not derived.is_relative_to(root):
                raise ValueError("unsafe derived media path")
            if derived.is_dir():
                shutil.rmtree(derived)
        return tuple(skipped)
