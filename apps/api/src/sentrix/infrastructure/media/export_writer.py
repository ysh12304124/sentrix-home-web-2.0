"""图片记忆导出包的文件写入。只写应用受管目录，不接收绝对路径。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from sentrix.domain.media.models import ExportFile


class FileSystemMemoryExportWriter:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _snapshot_dir(self, snapshot_id: str) -> Path:
        if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id:
            raise ValueError("invalid snapshot id")
        return self._root / snapshot_id

    def write(self, snapshot_id: str, files: Mapping[str, bytes]) -> tuple[ExportFile, ...]:
        directory = self._snapshot_dir(snapshot_id)
        directory.mkdir(parents=True, exist_ok=True)
        written: list[ExportFile] = []
        for name, payload in files.items():
            if not name or "/" in name or "\\" in name or name.startswith("."):
                raise ValueError(f"invalid export file name: {name!r}")
            path = directory / name
            path.write_bytes(payload)
            written.append(
                ExportFile(
                    name=name,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                )
            )
        return tuple(written)

    def path_for(self, snapshot_id: str, file_name: str) -> Path:
        if not file_name or "/" in file_name or "\\" in file_name or file_name.startswith("."):
            raise ValueError("invalid export file name")
        return self._snapshot_dir(snapshot_id) / file_name
