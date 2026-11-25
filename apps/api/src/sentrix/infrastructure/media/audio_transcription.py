"""Qwen ASR adapter that keeps video-time bounds when the server returns text-only JSON."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import wave
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import numpy as np
import sherpa_onnx

from sentrix.domain.media.ports import TranscriptionSegment

log = logging.getLogger(__name__)


class QwenAudioTranscriber:
    """Transcribe mono 16 kHz chunks and preserve their source-video time windows.

    llama.cpp's Qwen3-ASR endpoint returns ``response_format=json`` with text but no
    segment timestamps. Each fixed-width chunk therefore becomes a coarse timed ASR
    segment. If a compatible server returns native segment timestamps, those are kept.
    """

    CHUNK_SECONDS = 15.0

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout_s: float = 300.0,
        speaker_model_dir: Path | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key.strip()
        self._timeout_s = timeout_s
        self._speaker_model_dir = speaker_model_dir
        self._speaker_model: Any = None
        self._speaker_lock = Lock()

    def transcribe_video(self, path: Path) -> tuple[TranscriptionSegment, ...]:
        with tempfile.TemporaryDirectory(prefix="sentrix-asr-") as temporary:
            directory = Path(temporary)
            audio_path = directory / "audio.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
            chunk_path = directory / "chunk.wav"
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            result: list[TranscriptionSegment] = []
            with wave.open(str(audio_path), "rb") as audio:
                rate = audio.getframerate()
                step = int(self.CHUNK_SECONDS * rate)
                for start_sample in range(0, audio.getnframes(), step):
                    offset = start_sample / rate
                    end = min(start_sample + step, audio.getnframes()) / rate
                    with wave.open(str(chunk_path), "wb") as chunk:
                        chunk.setparams(audio.getparams())
                        chunk.writeframes(audio.readframes(step))
                    with chunk_path.open("rb") as chunk_file:
                        response = httpx.post(
                            f"{self._base_url}/audio/transcriptions",
                            headers=headers,
                            data={"model": self._model, "response_format": "verbose_json"},
                            files={"file": (chunk_path.name, chunk_file, "audio/wav")},
                            timeout=self._timeout_s,
                        )
                    response.raise_for_status()
                    payload: Any = response.json()
                    native_segments = payload.get("segments") or []
                    if native_segments:
                        result.extend(_native_segments(native_segments, offset, end))
                        continue
                    text = _clean_transcript(str(payload.get("text") or ""))
                    if text and end > offset:
                        result.append(TranscriptionSegment(offset, end, text))
            if any(not segment.speaker for segment in result):
                try:
                    return _label_speech(result, self._speaker_turns(audio_path))
                except Exception:  # noqa: BLE001 — retain successful transcription
                    log.warning("video speaker diarization failed path=%s", path, exc_info=True)
            return tuple(result)

    def _speaker_turns(self, audio_path: Path) -> list[tuple[float, float, int]]:
        if self._speaker_model_dir is None:
            return []
        segmentation = self._speaker_model_dir / "segmentation.onnx"
        embedding = self._speaker_model_dir / "embedding.onnx"
        if not segmentation.is_file() or not embedding.is_file():
            return []
        with self._speaker_lock:
            if self._speaker_model is None:
                config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                    segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(str(segmentation))
                    ),
                    embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(str(embedding)),
                    clustering=sherpa_onnx.FastClusteringConfig(),
                )
                self._speaker_model = sherpa_onnx.OfflineSpeakerDiarization(config)
            with wave.open(str(audio_path), "rb") as wave_file:
                pcm = np.frombuffer(wave_file.readframes(wave_file.getnframes()), dtype="<i2")
            samples = pcm.astype(np.float32) / 32768.0
            result = self._speaker_model.process(samples)
            return [(turn.start, turn.end, turn.speaker) for turn in result.sort_by_start_time()]


def _label_speech(
    segments: list[TranscriptionSegment], turns: list[tuple[float, float, int]]
) -> tuple[TranscriptionSegment, ...]:
    labelled: list[TranscriptionSegment] = []
    for segment in segments:
        if segment.speaker:
            labelled.append(segment)
            continue
        overlap: dict[int, float] = {}
        for start, end, speaker in turns:
            duration = min(segment.end_sec, end) - max(segment.start_sec, start)
            if duration > 0:
                overlap[speaker] = overlap.get(speaker, 0.0) + duration
        if overlap:
            speaker, duration = max(overlap.items(), key=lambda item: item[1])
            if duration >= 0.2 and duration >= sum(overlap.values()) * 0.75:
                segment = replace(segment, speaker=f"speaker_{speaker + 1}")
        labelled.append(segment)
    return tuple(labelled)


def _clean_transcript(text: str) -> str:
    cleaned = re.sub(r"^\s*language\s+[A-Za-z-]+\s*<asr_text>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?asr_text>", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def _native_segments(
    items: list[dict[str, object]], offset: float, limit: float
) -> list[TranscriptionSegment]:
    result: list[TranscriptionSegment] = []
    for item in items:
        try:
            start, end = float(str(item["start"])) + offset, float(str(item["end"])) + offset
            text = _clean_transcript(str(item.get("text") or ""))
        except (KeyError, TypeError, ValueError):
            continue
        end = min(end, limit)
        if start >= offset and end > start and text:
            result.append(
                TranscriptionSegment(
                    start_sec=start,
                    end_sec=end,
                    text=text,
                    speaker=str(item.get("speaker_label") or item.get("speaker") or "").strip(),
                )
            )
    return result
