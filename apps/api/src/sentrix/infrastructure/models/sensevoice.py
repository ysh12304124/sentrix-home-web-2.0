"""Process-local SenseVoice-Small: eager startup, one model, bounded concurrency."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

import numpy as np

from sentrix.domain.assistant.ports import (
    InvalidSpeechAudio,
    SpeechBusy,
    SpeechRecognitionFailed,
    SpeechUnavailable,
)

log = logging.getLogger(__name__)


class SenseVoiceRecognizer:
    def __init__(self, model_dir: Path) -> None:
        self._model_dir = model_dir
        self._recognizer: Any = None
        self._lock = Lock()
        self._load_error = "语音模型尚未加载。"

    @property
    def available(self) -> bool:
        return self._recognizer is not None

    @property
    def load_error(self) -> str:
        return self._load_error

    def load(self) -> None:
        with self._lock:
            if self._recognizer is not None:
                return
            started = perf_counter()
            try:
                model = self._model_dir / "model.int8.onnx"
                tokens = self._model_dir / "tokens.txt"
                if not model.is_file() or not tokens.is_file():
                    raise FileNotFoundError(
                        "请先运行 .venv/bin/python scripts/deploy/prepare_sensevoice.py "
                        "准备权重，再启动共享服务。"
                    )
                import sherpa_onnx

                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model),
                    tokens=str(tokens),
                    num_threads=2,
                    use_itn=True,
                    language="auto",
                    provider="cpu",
                )
            except Exception as exc:
                self._load_error = "本机后端语音模型加载失败，请检查后端日志。"
                if isinstance(exc, FileNotFoundError):
                    self._load_error = str(exc)
                log.warning("SenseVoice startup failed (%s): %s", self._model_dir, exc)
                raise SpeechUnavailable(self._load_error) from exc
            self._load_error = ""
            log.info("SenseVoice ready in %.2fs (%s)", perf_counter() - started, self._model_dir)

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not 8000 <= sample_rate <= 192000 or len(pcm) % 4:
            raise InvalidSpeechAudio("音频必须为单声道 float32 PCM，采样率 8000–192000 Hz。")
        duration = len(pcm) / 4 / sample_rate
        if not 0.2 <= duration <= 60:
            raise InvalidSpeechAudio("录音时长必须为 0.2–60 秒。")
        samples = np.frombuffer(pcm, dtype="<f4")
        if not np.isfinite(samples).all() or np.any(np.abs(samples) > 1):
            raise InvalidSpeechAudio("音频采样值必须为 -1 到 1 之间的有限数值。")
        if not self._lock.acquire(blocking=False):
            raise SpeechBusy("本机后端正在识别另一段语音，请稍后重试。")
        try:
            recognizer = self._recognizer
            if recognizer is None:
                raise SpeechUnavailable(self._load_error)
            started = perf_counter()
            stream = recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples)
            recognizer.decode_stream(stream)
            log.info("SenseVoice decoded %.2fs audio in %.2fs", duration, perf_counter() - started)
            return str(stream.result.text).strip()
        except SpeechUnavailable:
            raise
        except Exception as exc:
            # Do not log user audio, transcript or native error contents.
            log.warning("SenseVoice decode failed (%s)", type(exc).__name__)
            raise SpeechRecognitionFailed("本机后端语音识别失败，请重试。") from exc
        finally:
            self._lock.release()

    def close(self) -> None:
        # Wait for an already-running native decode before releasing the shared instance.
        with self._lock:
            self._recognizer = None
            self._load_error = "语音服务已停止。"
