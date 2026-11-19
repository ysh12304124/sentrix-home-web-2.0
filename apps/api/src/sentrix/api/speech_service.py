"""Internal host-wide ASR transport. Deployment binds it to loopback only."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from sentrix.domain.assistant.ports import InvalidSpeechAudio, SpeechBusy

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "service": "sentrix-sensevoice",
        "available": request.app.state.recognizer.available,
        "pid": os.getpid(),
        "instance_id": request.app.state.instance_id,
        "model_loads": request.app.state.model_loads,
    }


@router.post("/transcribe")
async def transcribe(request: Request, sample_rate: int = Query(ge=8000, le=192000)) -> dict[str, str]:
    # Reserve before buffering: one in-flight upload/decode across ALL user APIs.
    gate = request.app.state.gate
    if not gate.acquire(blocking=False):
        raise SpeechBusy("共享语音服务繁忙，请稍后重试。")
    delegated = False
    try:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
            raise InvalidSpeechAudio("需要 float32 PCM 音频。")
        audio = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(audio) + len(chunk) > sample_rate * 60 * 4:
                    raise InvalidSpeechAudio("录音超过 60 秒。")
                audio.extend(chunk)
        if await request.is_disconnected():
            raise InvalidSpeechAudio("录音上传已取消。")
        pcm = bytes(audio)

        def decode() -> str:
            try:
                return str(request.app.state.recognizer.transcribe(pcm, sample_rate))
            finally:
                # Cancellation of HTTP must not release the slot while native code runs.
                gate.release()

        # Ensure the task enters the thread even if the HTTP coroutine is cancelled.
        task = asyncio.create_task(run_in_threadpool(decode))
        delegated = True
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return {"text": await asyncio.shield(task)}
    except TimeoutError as exc:
        raise InvalidSpeechAudio("录音上传超时。") from exc
    finally:
        if not delegated:
            gate.release()
