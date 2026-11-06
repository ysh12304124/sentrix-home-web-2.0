"""Qwen ASR adapter that keeps video-time bounds when the server returns text-only JSON."""

from __future__ import annotations

import re
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import httpx

from sentrix.domain.media.ports import TranscriptionSegment


class QwenAudioTranscriber:
    """Transcribe mono 16 kHz chunks and preserve their source-video time windows.

    llama.cpp's Qwen3-ASR endpoint returns ``response_format=json`` with text but no
    segment timestamps. Each fixed-width chunk therefore becomes a coarse timed ASR
    segment. If a compatible server returns native segment timestamps, those are kept.
    """

    CHUNK_SECONDS = 15.0

    def __init__(self, base_url: str, model: str, *, api_key: str = "", timeout_s: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key.strip()
        self._timeout_s = timeout_s

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
            with wave.open(str(audio_path), "rb") as audio:
                duration = audio.getnframes() / float(audio.getframerate())

            chunk_pattern = directory / "chunk_%05d.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(audio_path),
                    "-f",
                    "segment",
                    "-segment_time",
                    str(self.CHUNK_SECONDS),
                    "-reset_timestamps",
                    "1",
                    "-c",
                    "copy",
                    str(chunk_pattern),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
            chunks = sorted(directory.glob("chunk_*.wav"))
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            result: list[TranscriptionSegment] = []
            for index, chunk_path in enumerate(chunks):
                offset = index * self.CHUNK_SECONDS
                with chunk_path.open("rb") as audio:
                    response = httpx.post(
                        f"{self._base_url}/audio/transcriptions",
                        headers=headers,
                        data={"model": self._model, "response_format": "json"},
                        files={"file": (chunk_path.name, audio, "audio/wav")},
                        timeout=self._timeout_s,
                    )
                response.raise_for_status()
                payload: Any = response.json()
                native_segments = payload.get("segments") or []
                if native_segments:
                    result.extend(_native_segments(native_segments, offset))
                    continue
                text = _clean_transcript(str(payload.get("text") or ""))
                end = min(offset + self.CHUNK_SECONDS, duration)
                if text and end > offset:
                    result.append(TranscriptionSegment(offset, end, text))
            return tuple(result)


def _clean_transcript(text: str) -> str:
    cleaned = re.sub(r"^\s*language\s+[A-Za-z-]+\s*<asr_text>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?asr_text>", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def _native_segments(items: list[dict[str, object]], offset: float) -> list[TranscriptionSegment]:
    result: list[TranscriptionSegment] = []
    for item in items:
        try:
            start, end = float(item["start"]) + offset, float(item["end"]) + offset
            text = _clean_transcript(str(item.get("text") or ""))
        except (KeyError, TypeError, ValueError):
            continue
        if start >= 0 and end > start and text:
            result.append(
                TranscriptionSegment(
                    start_sec=start,
                    end_sec=end,
                    text=text,
                    speaker=str(item.get("speaker") or "").strip(),
                )
            )
    return result
