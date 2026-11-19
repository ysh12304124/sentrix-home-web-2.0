"""Host-owned, single-process SenseVoice service. Bind BEFORE loading weights."""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

# ruff: noqa: E402
from sentrix.infrastructure.thread_limits import configure_thread_environment

configure_thread_environment()

import uvicorn

from sentrix.bootstrap.speech_service import create_speech_service
from sentrix.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8107)
    parser.add_argument("--model-dir", type=Path, help="pre-provisioned host model directory")
    args = parser.parse_args()
    # No workers/reload option: a supervisor manages exactly one service per host.
    # A second launch on this port fails before creating any native model instance.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(16)
        app = create_speech_service(args.model_dir or load_settings().speech_model_dir)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, limit_concurrency=16))
        server.run(sockets=[listener])
        if not server.started:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
