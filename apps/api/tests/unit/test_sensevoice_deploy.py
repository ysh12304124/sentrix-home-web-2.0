"""Exercise the normal model-stack shell entry with other models already running."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

STACK_SCRIPT = Path(__file__).resolve().parents[4] / "scripts/deploy/start_local_models_mac.sh"


@pytest.mark.parametrize(
    "existing,identity",
    [(False, "sentrix-sensevoice"), (True, "sentrix-sensevoice"), (True, "wrong-service")],
)
def test_stack_starts_or_reuses_sensevoice_and_checks_identity(tmp_path, existing, identity):
    weights = tmp_path / "models/sensevoice-small"
    weights.mkdir(parents=True)
    (weights / "model.int8.onnx").write_bytes(b"model")
    (weights / "tokens.txt").write_text("tokens")
    scripts = tmp_path / "scripts/deploy"
    scripts.mkdir(parents=True)
    shutil.copy(STACK_SCRIPT, scripts / STACK_SCRIPT.name)
    launcher = scripts / "start_sensevoice.sh"
    launcher.write_text('#!/bin/bash\ntouch "$SENTRIX_DATA_DIR/launched"\n')
    launcher.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    lsof = binaries / "lsof"
    lsof.write_text(
        '#!/bin/bash\nif [[ "$*" == *8107* ]]; then exit ' + ("0" if existing else "1") + "; fi\nexit 0\n"
    )
    lsof.chmod(0o755)
    curl = binaries / "curl"
    curl_script = "#!/bin/bash\n"
    if not existing:
        curl_script += 'if [[ "$*" == *8107* ]] && [ ! -f "$SENTRIX_DATA_DIR/launched" ]; then exit 1; fi\n'
    curl_script += 'printf \'{"service":"' + identity + '\",\"available\":true}\\n\'\n'
    curl.write_text(curl_script)
    curl.chmod(0o755)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "SENTRIX_DATA_DIR": str(tmp_path),
        "SENTRIX_ENABLE_UTILITY_VLM": "0",
        "SENTRIX_ENABLE_RERANK": "0",
        "SENTRIX_MLX_AUDIO_SERVER_BIN": "",
        "SENTRIX_QWEN_ASR_BASE_URL": "",
    }
    result = subprocess.run(
        ["bash", str(scripts / STACK_SCRIPT.name)], env=env, capture_output=True, text=True, timeout=10
    )
    if identity == "wrong-service":
        assert result.returncode != 0
        assert "not a ready SenseVoice service" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
    # Reusing a healthy listener must not launch a second inference service.
    assert (tmp_path / "launched").exists() is not existing
