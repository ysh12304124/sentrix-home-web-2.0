from pathlib import Path
from types import SimpleNamespace

from sentrix.infrastructure.media import audio_transcription
from sentrix.infrastructure.media.audio_transcription import QwenAudioTranscriber


def test_qwen_transcriber_returns_timestamped_json_chunks_with_optional_auth(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    def fake_ffmpeg(command, **_kwargs):
        output = Path(command[-1])
        if output.name == "audio.wav":
            output.write_bytes(b"wav")
        else:
            (output.parent / "chunk_00000.wav").write_bytes(b"chunk0")
            (output.parent / "chunk_00001.wav").write_bytes(b"chunk1")

    class FakeWave:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getnframes(self):
            return 320_000

        def getframerate(self):
            return 16_000

    responses = iter(
        [
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"text": "language English<asr_text> 会议开始 </asr_text>"},
            ),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"text": "language English<asr_text> 接下来讨论 </asr_text>"},
            ),
        ]
    )
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr(audio_transcription.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(audio_transcription.wave, "open", lambda *_args, **_kwargs: FakeWave())
    monkeypatch.setattr(audio_transcription.httpx, "post", fake_post)

    segments = QwenAudioTranscriber(
        "http://127.0.0.1:8765/v1", "Qwen3-ASR", api_key="local-key"
    ).transcribe_video(video)

    assert len(calls) == 2
    assert all(call["url"] == "http://127.0.0.1:8765/v1/audio/transcriptions" for call in calls)
    assert all(call["headers"] == {"Authorization": "Bearer local-key"} for call in calls)
    assert all(call["data"]["response_format"] == "json" for call in calls)
    assert [(item.start_sec, item.end_sec) for item in segments] == [(0.0, 15.0), (15.0, 20.0)]
    assert [item.text for item in segments] == ["会议开始", "接下来讨论"]
