"""Assistant microphone transport; audio is bounded in memory and never persisted."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from sentrix.api.deps import get_container
from sentrix.bootstrap.container import Container
from sentrix.domain.assistant.ports import InvalidSpeechAudio, SpeechUnavailable

router = APIRouter(prefix="/assistant/speech", tags=["assistant"])


class SpeechStatus(BaseModel):
    available: bool
    message: str


class SpeechTranscript(BaseModel):
    text: str


@router.get("/status", response_model=SpeechStatus)
def speech_status(container: Container = Depends(get_container)) -> SpeechStatus:
    service = container.speech_recognizer
    return SpeechStatus(available=service.available, message=service.load_error)


@router.post(
    "/transcribe",
    response_model=SpeechTranscript,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
            "description": "Mono little-endian float32 PCM, normalized [-1,1], 0.2–60 seconds.",
        }
    },
)
async def transcribe_speech(
    request: Request,
    sample_rate: int = Query(ge=8000, le=192000),
    container: Container = Depends(get_container),
) -> SpeechTranscript:
    service = container.speech_recognizer
    if not await run_in_threadpool(lambda: service.available):
        raise SpeechUnavailable(service.load_error)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise InvalidSpeechAudio("请提交 application/octet-stream 格式的 float32 PCM。")
    limit = sample_rate * 60 * 4
    audio = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(audio) + len(chunk) > limit:
                    raise InvalidSpeechAudio("录音超过 60 秒大小限制。")
                audio.extend(chunk)
    except TimeoutError as exc:
        raise InvalidSpeechAudio("录音上传超时，请重试。") from exc
    if await request.is_disconnected():
        raise InvalidSpeechAudio("录音上传已取消。")
    text = await run_in_threadpool(service.transcribe, bytes(audio), sample_rate)
    return SpeechTranscript(text=text)
