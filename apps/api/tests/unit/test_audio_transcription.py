from pathlib import Path
from types import SimpleNamespace

from sentrix.infrastructure.media import audio_transcription
from sentrix.infrastructure.media.audio_transcription import QwenAudioTranscriber


def test_qwen_transcriber_requests_timestamped_segments_with_optional_auth(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b"wav")

    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "segments": [
                {"start": 1.25, "end": 2.5, "text": " 开始做饭 ", "speaker": "speaker_0"},
                {"start": -1, "end": 2, "text": "invalid"},
            ]
        },
    )
    calls = {}

    def fake_post(url, **kwargs):
        calls.update(url=url, **kwargs)
        return response

    monkeypatch.setattr(audio_transcription.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(audio_transcription.httpx, "post", fake_post)

    segments = QwenAudioTranscriber(
        "http://127.0.0.1:8765/v1", "Qwen3-ASR", api_key="local-key"
    ).transcribe_video(video)

    assert calls["url"] == "http://127.0.0.1:8765/v1/audio/transcriptions"
    assert calls["headers"] == {"Authorization": "Bearer local-key"}
    assert calls["data"]["response_format"] == "verbose_json"
    assert calls["data"]["timestamp_granularities[]"] == "segment"
    assert len(segments) == 1
    assert (segments[0].start_sec, segments[0].end_sec) == (1.25, 2.5)
    assert segments[0].text == "开始做饭"
    assert segments[0].speaker == "speaker_0"
