"""Shared host ASR client. This module never imports the native inference runtime."""

from __future__ import annotations

import contextlib
import logging
import time

import httpx

from sentrix.domain.assistant.ports import (
    InvalidSpeechAudio,
    SpeechBusy,
    SpeechRecognitionFailed,
    SpeechUnavailable,
)

log = logging.getLogger(__name__)


class HttpSpeechRecognizer:
    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(110, connect=2, pool=1),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            trust_env=False,
        )
        self._checked = 0.0
        self._available = False
        self._error = "共享语音服务尚未检查。"

    @property
    def available(self) -> bool:
        if time.monotonic() - self._checked >= 2:
            with contextlib.suppress(SpeechUnavailable):
                self.load()
        return self._available

    @property
    def load_error(self) -> str:
        return self._error

    def load(self) -> None:
        """Probe only. The deployment supervisor owns the shared model lifecycle."""
        try:
            response = self._client.get(f"{self._url}/health", timeout=2)
            response.raise_for_status()
            data = response.json()
            if data.get("service") != "sentrix-sensevoice" or data.get("available") is not True:
                raise ValueError("unexpected service identity/readiness")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            self._available = False
            self._error = "共享语音服务未就绪，请检查主机 SenseVoice 服务。"
            self._checked = time.monotonic()
            log.warning("Shared ASR probe failed (%s): %s", self._url, type(exc).__name__)
            raise SpeechUnavailable(self._error) from exc
        self._available = True
        self._error = ""
        self._checked = time.monotonic()

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        try:
            response = self._client.post(
                f"{self._url}/transcribe",
                params={"sample_rate": sample_rate},
                content=pcm,
                headers={"Content-Type": "application/octet-stream"},
            )
        except httpx.HTTPError as exc:
            self._available = False
            self._checked = 0
            self._error = "共享语音服务连接失败或超时，请稍后重试。"
            log.warning("Shared ASR request failed (%s)", type(exc).__name__)
            raise SpeechUnavailable(self._error) from exc
        if response.status_code == 429:
            raise SpeechBusy("共享语音服务正在处理其他录音，请稍后重试。")
        if response.status_code == 400:
            raise InvalidSpeechAudio("录音格式或时长不符合要求，请重新录音。")
        if response.status_code != 200:
            raise SpeechRecognitionFailed("共享语音识别失败，请检查主机服务后重试。")
        try:
            text = response.json()["text"]
            if not isinstance(text, str):
                raise ValueError("invalid transcript")
        except (ValueError, TypeError, KeyError) as exc:
            raise SpeechRecognitionFailed("共享语音服务返回了无法读取的识别结果。") from exc
        return text

    def close(self) -> None:
        self._client.close()
