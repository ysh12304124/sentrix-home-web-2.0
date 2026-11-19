"""Assemble the host-owned SenseVoice service, without a user database/container."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from sentrix.api.errors import domain_error_handler
from sentrix.api.speech_service import router
from sentrix.domain.errors import DomainError
from sentrix.infrastructure.models.sensevoice import SenseVoiceRecognizer


def create_speech_service(model_dir: Path) -> FastAPI:
    recognizer = SenseVoiceRecognizer(model_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail startup explicitly for absent/broken local weights, never download here.
        await run_in_threadpool(recognizer.load)
        app.state.model_loads += 1
        try:
            yield
        finally:
            await run_in_threadpool(recognizer.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.recognizer = recognizer
    app.state.gate = Lock()
    app.state.instance_id = uuid.uuid4().hex
    app.state.model_loads = 0
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(router)
    return app
