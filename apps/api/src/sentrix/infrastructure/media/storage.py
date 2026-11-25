"""按 SHA256 共享媒体内容，每个资产保留独立硬链接和文件名。"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import portalocker

from sentrix.domain.media.models import Asset

_COPY_CHUNK = 1024 * 1024


def sanitise(file_name: str) -> str:
    """只保留基础名，并剔除会破坏 ``{asset_id}_{name}`` 拼接的字符。"""
    base = Path(file_name or "unnamed").name
    cleaned = "".join(char for char in base if char not in {"/", "\\", "\x00"}).lstrip(".")
    return cleaned or "unnamed"


def fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(directory.resolve()), 0xC0000000, 7, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.FlushFileBuffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


class FileSystemMediaStorage:
    def __init__(self, media_dir: Path) -> None:
        self._media_dir = Path(media_dir)
        self._media_dir.mkdir(parents=True, exist_ok=True)
        self._blobs_dir = self._media_dir / ".blobs"
        self._blobs_dir.mkdir(exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # 所有实例/进程共用文件锁，发布与最后一个引用的回收在同一临界区。
        with portalocker.Lock(self._blobs_dir / ".lock", mode="a+b", flags=portalocker.LOCK_EX):
            yield

    def _destination(self, asset_id: str, file_name: str) -> Path:
        if re.fullmatch(r"[A-Za-z0-9_-]+", asset_id) is None:
            raise ValueError("invalid asset ID")
        return self._media_dir / f"{asset_id}_{sanitise(file_name)}"

    def _blob_path(self, digest: str) -> Path:
        return self._blobs_dir / digest

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_COPY_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _regular_source(source_path: Path) -> None:
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("source must be a regular file")

    def _link(self, blob: Path, destination: Path, *, replace: bool = False) -> None:
        if destination.is_symlink():
            raise ValueError("media destination must not be a symlink")
        if destination.exists():
            if destination.samefile(blob):
                return
            if not replace:
                raise FileExistsError(f"asset media already exists: {destination.name}")
        temporary = self.staging_dir / f"{uuid.uuid4().hex}.part"
        try:
            os.link(blob, temporary)
            os.replace(temporary, destination)
            fsync_directory(self._media_dir)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def media_dir(self) -> Path:
        return self._media_dir

    @property
    def staging_dir(self) -> Path:
        path = self._media_dir / ".staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        destination = self._destination(asset_id, file_name)
        blob = self._blob_path(hashlib.sha256(content).hexdigest())
        with self._locked():
            if not blob.exists():
                temporary = self.staging_dir / f"{uuid.uuid4().hex}.part"
                try:
                    with temporary.open("wb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, blob)
                    fsync_directory(self._blobs_dir)
                finally:
                    temporary.unlink(missing_ok=True)
            self._link(blob, destination)
        return destination

    def move_from_path(self, asset_id: str, file_name: str, source_path: Path) -> Path:
        """上传原件原子移入内容库；已存在的内容只新增资产引用。"""
        self._regular_source(source_path)
        destination = self._destination(asset_id, file_name)
        blob = self._blob_path(self._digest(source_path))
        with self._locked():
            if not blob.exists():
                if source_path.resolve().parent == self._media_dir.resolve():
                    os.link(source_path, blob)
                else:
                    os.replace(source_path, blob)
                fsync_directory(self._blobs_dir)
            self._link(blob, destination, replace=destination == source_path)
            if source_path != destination:
                source_path.unlink(missing_ok=True)
                fsync_directory(source_path.parent)
        return destination

    def copy_from_path(self, asset_id: str, file_name: str, source_path: Path) -> Path:
        """外部源文件保持独立；已有受管文件可直接纳入共享内容库。"""
        self._regular_source(source_path)
        destination = self._destination(asset_id, file_name)
        digest = self._digest(source_path)
        blob = self._blob_path(digest)
        managed = source_path.resolve().parent == self._media_dir.resolve()
        with self._locked():
            if not blob.exists():
                if managed:
                    os.link(source_path, blob)
                else:
                    temporary = self.staging_dir / f"{uuid.uuid4().hex}.part"
                    try:
                        with source_path.open("rb") as source, temporary.open("wb") as target:
                            copied_digest = hashlib.sha256()
                            while chunk := source.read(_COPY_CHUNK):
                                copied_digest.update(chunk)
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                        if copied_digest.hexdigest() != digest:
                            raise ValueError("source changed during media import")
                        os.replace(temporary, blob)
                    finally:
                        temporary.unlink(missing_ok=True)
                fsync_directory(self._blobs_dir)
            if managed:
                self._link(blob, source_path, replace=True)
            self._link(blob, destination, replace=destination == source_path)
        return destination

    def path_for(self, asset: Asset) -> Path:
        return Path(asset.path)

    def read_asset(self, asset: Asset) -> bytes:
        return self.path_for(asset).read_bytes()

    def read(self, asset_id: str) -> bytes:
        matches = sorted(self._media_dir.glob(f"{asset_id}_*"))
        if not matches:
            raise FileNotFoundError(f"未找到 asset {asset_id} 的媒体文件")
        return matches[0].read_bytes()

    def delete_assets(self, asset_ids: Sequence[str], paths: Sequence[str]) -> tuple[str, ...]:
        """仅删除媒体根目录内受管文件及视频派生物；外部源文件保留并报告。"""
        root = self._media_dir.resolve()
        skipped: list[str] = []
        with self._locked():
            for raw in paths:
                path = Path(raw).resolve()
                if not path.is_relative_to(root) or path.is_relative_to(self._blobs_dir.resolve()):
                    skipped.append(raw)
                    continue
                self._remove_link(path)
        for asset_id in asset_ids:
            if re.fullmatch(r"[A-Za-z0-9_-]+", asset_id) is None:
                raise ValueError("invalid asset ID in cleanup job")
            derived = (root / "derived" / "video" / asset_id).resolve()
            if not derived.is_relative_to(root):
                raise ValueError("unsafe derived media path")
            if derived.is_dir():
                shutil.rmtree(derived)
        return tuple(skipped)

    def _remove_link(self, path: Path) -> None:
        blob = None
        if path.parent.resolve() == self._media_dir.resolve() and path.is_file():
            blob = self._blob_path(self._digest(path))
        path.unlink(missing_ok=True)
        if blob is not None and blob.exists() and blob.stat().st_nlink == 1:
            blob.unlink()
            fsync_directory(self._blobs_dir)
        if path.parent.is_dir():
            fsync_directory(path.parent)

    def cleanup_orphans(self, referenced: set[str], cutoff: float) -> int:
        """回收过期资产引用及崩溃遗留的无引用内容。"""
        removed = 0
        with self._locked():
            for path in self._media_dir.glob("asset_*"):
                if not path.is_file() or path.is_symlink() or str(path) in referenced:
                    continue
                stat = path.stat()
                if max(stat.st_mtime, stat.st_ctime) < cutoff:
                    self._remove_link(path)
                    removed += 1
            for blob in self._blobs_dir.iterdir():
                if re.fullmatch(r"[0-9a-f]{64}", blob.name) is None:
                    continue
                stat = blob.stat()
                if stat.st_nlink == 1 and max(stat.st_mtime, stat.st_ctime) < cutoff:
                    blob.unlink()
            fsync_directory(self._blobs_dir)
        return removed
