"""文件系统媒体存储。

文件名规则 ``{asset_id}_{原始文件名}`` 沿用 1.0（``app.py:117 _upload_destination``），
但原始文件名先过 :func:`sanitise`——1.0 只用 ``Path.name``，遇到 ``../`` 会写到媒体目录之外。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from sentrix.domain.media.models import Asset


def sanitise(file_name: str) -> str:
    """只保留基础名，并剔除会破坏 ``{asset_id}_{name}`` 拼接的字符。"""
    base = Path(file_name or "unnamed").name
    cleaned = "".join(char for char in base if char not in {"/", "\\", "\x00"}).lstrip(".")
    return cleaned or "unnamed"


class FileSystemMediaStorage:
    def __init__(self, media_dir: Path) -> None:
        self._media_dir = Path(media_dir)
        self._media_dir.mkdir(parents=True, exist_ok=True)

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        destination = self._media_dir / f"{asset_id}_{sanitise(file_name)}"
        destination.write_bytes(content)
        return destination

    def path_for(self, asset: Asset) -> Path:
        return Path(asset.path)

    def read(self, asset_id: str) -> bytes:
        matches = sorted(self._media_dir.glob(f"{asset_id}_*"))
        if not matches:
            raise FileNotFoundError(f"未找到 asset {asset_id} 的媒体文件")
        return matches[0].read_bytes()

    def read_asset(self, asset: Asset) -> bytes:
        return self.path_for(asset).read_bytes()

    def delete_assets(self, asset_ids: Sequence[str], paths: Sequence[str]) -> tuple[str, ...]:
        """Delete only files owned by this data directory, including video derivatives."""
        root = self._media_dir.resolve()
        skipped: list[str] = []
        for raw in paths:
            path = Path(raw).resolve()
            if not path.is_relative_to(root):
                skipped.append(raw)
                continue
            path.unlink(missing_ok=True)
        for asset_id in asset_ids:
            # Asset IDs come from SQLite, but still reject path components here.
            if re.fullmatch(r"[A-Za-z0-9_-]+", asset_id) is None:
                raise ValueError("invalid asset ID in cleanup job")
            derived = (root / "derived" / "video" / asset_id).resolve()
            if not derived.is_relative_to(root):
                raise ValueError("unsafe derived media path")
            if derived.is_dir():
                shutil.rmtree(derived)
        return tuple(skipped)
