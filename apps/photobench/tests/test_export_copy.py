"""Compatibility exports must be independent files, including native APFS clones."""

import ctypes
import errno
import sys

import pytest

from photobench.infrastructure.results import file_copy
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore


def test_modifying_legacy_exports_never_modifies_committed_history(tmp_path):
    state = {"run_id": "r", "items": [{"answer": "frozen", "judge": {"score": 2}}]}
    FileResultWriter(tmp_path).write_snapshot("r", state)
    for name in ("run.json", "results.jsonl"):
        (tmp_path / "r" / name).write_text("modified by legacy consumer")
    assert FileRunStore(tmp_path).load_state("r") == state


@pytest.mark.skipif(sys.platform != "darwin", reason="native Apple filesystem clone")
def test_native_clone_avoids_byte_copy_and_has_independent_inode(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"frozen" * 1000)
    monkeypatch.setattr(file_copy.shutil, "copyfile", lambda *args: pytest.fail("unexpected byte copy"))
    file_copy.copy_export(source, target)
    assert source.stat().st_ino != target.stat().st_ino
    assert source.read_bytes() == target.read_bytes()
    target.write_bytes(b"changed")
    assert source.read_bytes() == b"frozen" * 1000


def test_unsupported_clone_uses_independent_portable_copy(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"original")

    def unsupported(*args):
        ctypes.set_errno(errno.ENOTSUP)
        return -1

    monkeypatch.setattr(file_copy.sys, "platform", "darwin")
    monkeypatch.setattr(file_copy, "_clonefile", lambda: unsupported)
    file_copy.copy_export(source, target)
    target.write_bytes(b"changed")
    assert source.read_bytes() == b"original"


def test_real_clone_storage_error_is_reported_without_another_copy(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"original")

    def no_space(*args):
        ctypes.set_errno(errno.ENOSPC)
        return -1

    monkeypatch.setattr(file_copy.sys, "platform", "darwin")
    monkeypatch.setattr(file_copy, "_clonefile", lambda: no_space)
    monkeypatch.setattr(file_copy.shutil, "copyfile", lambda *args: pytest.fail("retried ENOSPC"))
    with pytest.raises(OSError) as error:
        file_copy.copy_export(source, target)
    assert error.value.errno == errno.ENOSPC
