"""Optional OpenAI-compatible Qwen ASR adapter for timestamped video speech."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from sentrix.domain.media.ports import TranscriptionSegment


class QwenAudioTranscriber:
    """Extract mono 16 kHz audio and request segment timestamps from a Qwen ASR endpoint.

    The endpoint must implement ``POST /audio/transcriptions`` with ``verbose_json``
    segments, as provided by OpenAI-compatible Qwen3-ASR serving setups.
    """

    def __init__(self, base_url: str, model: str, *, api_key: str = "", timeout_s: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key.strip()
        self._timeout_s = timeout_s

    def transcribe_video(self, path: Path) -> tuple[TranscriptionSegment, ...]:
        with tempfile.TemporaryDirectory(prefix="sentrix-asr-") as temporary:
            audio_path = Path(temporary) / "audio.wav"
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
            with audio_path.open("rb") as audio:
                headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
                response = httpx.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers=headers,
                    data={
                        "model": self._model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                    files={"file": (audio_path.name, audio, "audio/wav")},
                    timeout=self._timeout_s,
                )
            response.raise_for_status()
            payload: Any = response.json()

        result: list[TranscriptionSegment] = []
        for item in payload.get("segments", []):
            try:
                start, end = float(item["start"]), float(item["end"])
                text = str(item.get("text") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
            if start < 0 or end <= start or not text:
                continue
            result.append(
                TranscriptionSegment(
                    start_sec=start,
                    end_sec=end,
                    text=text,
                    speaker=str(item.get("speaker") or "").strip(),
                )
            )
        return tuple(result)
