"""通用 ASR sidecar HTTP 客户端；FunASR/Whisper 都实现同一份契约。"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx

from sentrix.domain.media.ports import AudioTranscriptionError
from sentrix.domain.media.transcription import AsrResult, AsrSegment


class AsrSidecarTranscriber:
    """低并发 ASR 适配器。

    导入相册会并行 prepare，多张音频可能同时到这里。信号量把 ASR 限制为 1，避免
    同一个模型进程因并发复制中间张量而冲高内存。
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 300.0,
        health_timeout_s: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_s)
        self._health_timeout_s = health_timeout_s
        self._slot = threading.Semaphore(1)
        self._health_lock = threading.Lock()
        self._health_checked_at = 0.0
        self._health_value = False
        self._error = ""

    @property
    def load_error(self) -> str:
        return self._error

    @property
    def available(self) -> bool:
        with self._health_lock:
            if time.monotonic() - self._health_checked_at < 10.0:
                return self._health_value
        try:
            response = self._client.get(f"{self._base_url}/health", timeout=self._health_timeout_s)
            if response.status_code != 200:
                self._error = f"health HTTP {response.status_code}"
                healthy = False
            else:
                payload = response.json()
                healthy = bool(payload.get("available", True))
                self._error = "" if healthy else str(payload.get("load_error") or "model unavailable")
        except Exception as error:  # noqa: BLE001 - 不可用由上层降级，不传播 httpx 细节
            self._error = f"{type(error).__name__}: {error}"
            healthy = False
        with self._health_lock:
            self._health_checked_at = time.monotonic()
            self._health_value = healthy
        return healthy

    def transcribe(self, path: Path) -> AsrResult:
        if not path.is_file():
            raise AudioTranscriptionError(f"待转写媒体文件不存在: {path}")
        if not self.available:
            raise AudioTranscriptionError(self._error or "ASR sidecar unavailable")
        try:
            with self._slot:
                response = self._client.post(f"{self._base_url}/transcribe", json={"path": str(path)})
                response.raise_for_status()
                payload = response.json()
            result = self._parse(payload)
        except AudioTranscriptionError:
            raise
        except Exception as error:  # noqa: BLE001 - 对 domain 只暴露稳定的失败类型
            self._error = f"{type(error).__name__}: {error}"
            raise AudioTranscriptionError(self._error) from error
        self._error = ""
        return result

    @staticmethod
    def _parse(payload: Any) -> AsrResult:
        if not isinstance(payload, dict):
            raise AudioTranscriptionError("ASR sidecar 响应不是对象")
        raw_segments = payload.get("segments") or []
        if not isinstance(raw_segments, list):
            raise AudioTranscriptionError("ASR sidecar segments 不是数组")
        segments: list[AsrSegment] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            segments.append(
                AsrSegment(
                    start_ms=int(item.get("start_ms") or 0),
                    end_ms=int(item.get("end_ms") or 0),
                    text=str(item.get("text") or ""),
                    confidence_raw=_optional_float(item.get("confidence")),
                    alignment_score=_optional_float(item.get("alignment_score")),
                    speech_probability=_optional_float(item.get("speech_probability")),
                    language_probability=_optional_float(item.get("language_probability")),
                    local_speaker_label=str(item.get("speaker_label") or "").strip(),
                    speaker_confidence=_optional_float(item.get("speaker_confidence")),
                )
            )
        model_name = str(payload.get("model_name") or "").strip()
        config_hash = str(payload.get("config_hash") or "").strip()
        if not model_name or not config_hash:
            raise AudioTranscriptionError("ASR sidecar 缺少 model_name/config_hash")
        return AsrResult(
            model_name=model_name,
            model_version=str(payload.get("model_version") or ""),
            config_hash=config_hash,
            language=str(payload.get("language") or ""),
            raw_text=str(payload.get("raw_text") or ""),
            segments=tuple(segments),
        )


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except ValueError as error:
        raise AudioTranscriptionError(f"ASR sidecar 分数字段不是数字: {value!r}") from error


# 保留旧名字，避免外部装配与已有测试在切换 Whisper 时被迫同步改名。
FunAsrSidecarTranscriber = AsrSidecarTranscriber
