from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from sentrix.domain.assistant.ports import (
    InvalidSpeechAudio,
    SpeechBusy,
    SpeechRecognitionFailed,
    SpeechUnavailable,
)
from sentrix.infrastructure.models.sensevoice import SenseVoiceRecognizer


@pytest.fixture
def service(tmp_path, monkeypatch):
    (tmp_path / "model.int8.onnx").write_bytes(b"model")
    (tmp_path / "tokens.txt").write_text("tokens")
    streams = []

    def create_stream():
        stream = SimpleNamespace(accept_waveform=Mock(), result=SimpleNamespace(text="你好"))
        streams.append(stream)
        return stream

    native = SimpleNamespace(create_stream=Mock(side_effect=create_stream), decode_stream=Mock())
    factory = Mock(return_value=native)
    monkeypatch.setitem(
        __import__("sys").modules,
        "sherpa_onnx",
        SimpleNamespace(
            OfflineRecognizer=SimpleNamespace(from_sense_voice=factory),
        ),
    )
    return SenseVoiceRecognizer(tmp_path), factory, native, streams


def pcm(seconds=1, rate=16000):
    return np.zeros(int(seconds * rate), dtype="<f4").tobytes()


def test_eager_load_once_reuses_model_but_isolates_streams(service):
    recognizer, factory, native, streams = service
    with pytest.raises(SpeechUnavailable):
        recognizer.transcribe(pcm(), 16000)
    factory.assert_not_called()
    recognizer.load()
    recognizer.load()
    assert recognizer.available
    assert recognizer.load_error == ""
    assert recognizer.transcribe(pcm(), 16000) == "你好"
    assert recognizer.transcribe(pcm(rate=48000), 48000) == "你好"
    factory.assert_called_once()
    assert len(streams) == 2 and streams[0] is not streams[1]
    assert streams[1].accept_waveform.call_args.args[0] == 48000
    assert native.decode_stream.call_count == 2
    recognizer.close()
    assert not recognizer.available


def test_missing_weights_are_visible_without_lazy_retry(tmp_path):
    recognizer = SenseVoiceRecognizer(tmp_path)
    with pytest.raises(SpeechUnavailable, match="prepare_sensevoice"):
        recognizer.load()
    assert not recognizer.available
    with pytest.raises(SpeechUnavailable, match="prepare_sensevoice"):
        recognizer.transcribe(pcm(), 16000)


@pytest.mark.parametrize(
    "audio,rate",
    [
        (b"123", 16000),
        (pcm(0.1), 16000),
        (pcm(60.01), 16000),
        (pcm(), 0),
        (pcm(), 192001),
        (np.full(3200, np.nan, dtype="<f4").tobytes(), 16000),
        (np.full(3200, 2, dtype="<f4").tobytes(), 16000),
    ],
)
def test_invalid_audio_never_enters_native_decoder(service, audio, rate):
    recognizer, factory, native, _ = service
    recognizer.load()
    with pytest.raises(InvalidSpeechAudio):
        recognizer.transcribe(audio, rate)
    native.decode_stream.assert_not_called()


def test_busy_rejected_without_queue_and_model_survives(service):
    recognizer, factory, native, _ = service
    recognizer.load()
    entered, finish = Event(), Event()

    def decode(_stream):
        entered.set()
        assert finish.wait(5)

    native.decode_stream.side_effect = decode
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(recognizer.transcribe, pcm(), 16000)
        try:
            assert entered.wait(5)
            with pytest.raises(SpeechBusy):
                recognizer.transcribe(pcm(), 16000)
        finally:
            finish.set()
        assert task.result() == "你好"
    native.decode_stream.side_effect = None
    assert recognizer.transcribe(pcm(), 16000) == "你好"
    factory.assert_called_once()


def test_decoder_failure_is_not_empty_success_and_releases_slot(service):
    recognizer, factory, native, _ = service
    recognizer.load()
    native.decode_stream.side_effect = RuntimeError("native failure")
    with pytest.raises(SpeechRecognitionFailed):
        recognizer.transcribe(pcm(), 16000)
    native.decode_stream.side_effect = None
    assert recognizer.transcribe(pcm(), 16000) == "你好"
    factory.assert_called_once()
