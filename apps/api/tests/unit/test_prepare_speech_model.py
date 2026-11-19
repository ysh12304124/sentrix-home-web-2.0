import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_speech_model", Path(__file__).resolve().parents[4] / "scripts/deploy/prepare_sensevoice.py"
)
assert spec and spec.loader
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_prepare_verifies_and_reuses_without_download(tmp_path, monkeypatch):
    content = b"test model"
    archive = tmp_path / "model.tar.bz2"
    with tarfile.open(archive, "w:bz2") as bundle:
        entry = tarfile.TarInfo("official/model.int8.onnx")
        entry.size = len(content)
        bundle.addfile(entry, io.BytesIO(content))
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "archive_root": "official",
                "sha256": prepare.sha256(archive),
                "url": "unused",
                "files": {"model.int8.onnx": hashlib.sha256(content).hexdigest()},
            }
        )
    )
    output = tmp_path / "data" / "models" / "sensevoice-small"
    monkeypatch.setattr(prepare, "LOCK_PATH", lock)
    monkeypatch.setattr(prepare, "load_settings", lambda: SimpleNamespace(speech_model_dir=output))
    monkeypatch.setattr(prepare.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected download"))
    assert prepare.prepare(archive) == output
    assert (output / "model.int8.onnx").read_bytes() == content
    assert prepare.prepare() == output

    # A corrupt installed file cannot pass the cache check; bad archive cannot replace it.
    (output / "model.int8.onnx").write_bytes(b"corrupt")
    archive.write_bytes(b"wrong archive")
    with pytest.raises(ValueError, match="SHA-256"):
        prepare.prepare(archive)
    assert (output / "model.int8.onnx").read_bytes() == b"corrupt"
