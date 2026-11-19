import pytest

from sentrix.infrastructure.media.storage import FileSystemMediaStorage, fsync_directory


def test_durable_copy_and_move_preserve_bytes(tmp_path):
    storage = FileSystemMediaStorage(tmp_path / "media")
    source = tmp_path / "source.jpg"
    source.write_bytes(b"original upload bytes")
    copied = storage.copy_from_path("copy", "photo.jpg", source)
    assert source.read_bytes() == copied.read_bytes() == b"original upload bytes"
    moved = storage.move_from_path("move", "photo.jpg", source)
    assert moved.read_bytes() == copied.read_bytes()
    assert not source.exists()
    with pytest.raises(OSError):
        fsync_directory(tmp_path / "missing")


def test_store_writes_under_media_dir_with_asset_prefix(tmp_path):
    storage = FileSystemMediaStorage(tmp_path / "media")

    path = storage.store("asset_1", "IMG_0001.HEIC", b"\x00\x01binary")

    assert path.parent == tmp_path / "media"
    assert path.name == "asset_1_IMG_0001.HEIC"
    assert path.read_bytes() == b"\x00\x01binary"


def test_store_sanitises_path_traversal_in_file_name(tmp_path):
    storage = FileSystemMediaStorage(tmp_path / "media")

    path = storage.store("asset_1", "../../etc/passwd", b"x")

    assert path.parent == tmp_path / "media"
    assert ".." not in path.name


def test_read_returns_original_bytes(tmp_path):
    storage = FileSystemMediaStorage(tmp_path / "media")
    storage.store("asset_1", "a.jpg", b"payload")

    assert storage.read("asset_1") == b"payload"


def test_read_is_deterministic_across_calls(tmp_path):
    """相同 asset_id 重复读取必须稳定，1.0 的 glob 匹配在多文件时会取错。"""
    storage = FileSystemMediaStorage(tmp_path / "media")
    storage.store("asset_1", "a.jpg", b"first")

    assert storage.read("asset_1") == storage.read("asset_1")
