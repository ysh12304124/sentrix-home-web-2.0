"""Real process boundary: one native ASR service shared by independent user APIs."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from sentrix.config import load_settings


@pytest.fixture(scope="session")
def native_model_dir():
    return load_settings().speech_model_dir.resolve()


@pytest.mark.real_environment
def test_host_service_shared_by_independent_user_apis(native_model_dir, tmp_path):
    assert (native_model_dir / "model.int8.onnx").is_file(), "run scripts/deploy/prepare_sensevoice.py first"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": str(Path("apps/api/src").resolve())}
    command = [
        "bash",
        "scripts/deploy/start_sensevoice.sh",
        "--port",
        str(port),
        "--model-dir",
        str(native_model_dir),
    ]
    children = []
    with (tmp_path / "service.log").open("w+") as log:
        server = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=5) as client:
                identity = None
                for _ in range(150):
                    if server.poll() is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    try:
                        response = client.get(url + "/health")
                        if response.status_code == 200:
                            identity = response.json()
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                assert identity and identity["available"]
                assert identity["pid"] == server.pid and identity["model_loads"] == 1
                # Duplicate supervisor launches fail before model creation.
                duplicate = subprocess.run(command, env=env, capture_output=True, timeout=10)
                assert duplicate.returncode != 0 and b"Address already in use" in duplicate.stderr

                worker = r"""
import json,sys,wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
import psutil
from fastapi.testclient import TestClient
from sentrix.config import Settings
from sentrix.main import create_app
probe = patch("sentrix.infrastructure.models.wemm.WeMMSidecarEmbedder.smoke_test", return_value=(True,""))
probe.start()
app=create_app(Settings(data_dir=Path(sys.argv[1]), speech_service_url=sys.argv[2]))
with TestClient(app) as client:
    assert client.get("/api/v1/assistant/speech/status").json()["available"]
    assert "sherpa_onnx" not in sys.modules
    print(json.dumps({"pid":__import__("os").getpid(),"native_loaded":False,
                      "rss":psutil.Process().memory_info().rss}),flush=True)
    for command in sys.stdin:
        with wave.open(sys.argv[3]) as wav:
            rate=wav.getframerate()
            pcm=(np.frombuffer(wav.readframes(wav.getnframes()),dtype="<i2").astype("<f4")/32768).tobytes()
        result=client.post("/api/v1/assistant/speech/transcribe",params={"sample_rate":rate},
                          content=pcm,headers={"Content-Type":"application/octet-stream"})
        assert "sherpa_onnx" not in sys.modules
        print(json.dumps({"status":result.status_code,"text":result.json().get("text")}),flush=True)
"""
                process_states = []
                for index in range(3):
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            worker,
                            str(tmp_path / f"user-{index}"),
                            url,
                            str(native_model_dir / "test_wavs/zh.wav"),
                        ],
                        env=env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=log,
                        text=True,
                    )
                    children.append(child)
                    # Bounded read: subprocess pipe readline otherwise has no timeout.
                    import select

                    assert select.select([child.stdout], [], [], 30)[0], "user API startup timeout"
                    state = json.loads(child.stdout.readline())
                    assert state["pid"] != server.pid and state["native_loaded"] is False
                    process_states.append(state)
                answers = []
                for child in children:
                    child.stdin.write("decode\n")
                    child.stdin.flush()
                    assert select.select([child.stdout], [], [], 30)[0], "ASR response timeout"
                    answer = json.loads(child.stdout.readline())
                    assert answer["status"] == 200 and answer["text"]
                    answers.append(answer["text"])
                    assert client.get(url + "/health").json() == identity
                assert len(set(answers)) == 1
                children[0].stdin.close()
                children[0].wait(timeout=10)
                assert client.get(url + "/health").json() == identity
                children[1].stdin.write("decode\n")
                children[1].stdin.flush()
                assert select.select([children[1].stdout], [], [], 30)[0]
                assert json.loads(children[1].stdout.readline())["text"] == answers[0]
                assert client.get(url + "/health").json() == identity
                print(json.dumps({"service": identity, "user_processes": process_states}))
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
            server.terminate()
            server.wait(timeout=15)
