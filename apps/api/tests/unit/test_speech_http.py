from unittest.mock import Mock

import httpx
import pytest

from sentrix.domain.assistant.ports import (
    InvalidSpeechAudio,
    SpeechBusy,
    SpeechRecognitionFailed,
    SpeechUnavailable,
)
from sentrix.infrastructure.models.speech_http import HttpSpeechRecognizer


def test_probe_and_transcribe_do_not_own_native_model_or_remote_shutdown():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"service": "sentrix-sensevoice", "available": True})
        return httpx.Response(200, json={"text": "你好"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    recognizer = HttpSpeechRecognizer("http://127.0.0.1:8107", client=client)
    recognizer.load()
    assert recognizer.available
    assert recognizer.transcribe(b"pcm", 48000) == "你好"
    assert calls[-1].content == b"pcm"
    assert calls[-1].url.params["sample_rate"] == "48000"
    recognizer.close()
    assert client.is_closed
    assert [r.url.path for r in calls] == ["/health", "/transcribe"]


@pytest.mark.parametrize(
    "status,error",
    [
        (400, InvalidSpeechAudio),
        (429, SpeechBusy),
        (500, SpeechRecognitionFailed),
        (503, SpeechRecognitionFailed),
    ],
)
def test_errors_propagate_without_retry(status, error):
    handle = Mock(return_value=httpx.Response(status))
    service = HttpSpeechRecognizer(
        "http://127.0.0.1:8107", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(error):
        service.transcribe(b"pcm", 16000)
    assert handle.call_count == 1
    service.close()


def test_failed_probe_recovers_and_wrong_service_is_rejected():
    handle = Mock(
        side_effect=[
            httpx.Response(200, json={"service": "wrong", "available": True}),
            httpx.Response(200, json={"service": "sentrix-sensevoice", "available": True}),
        ]
    )
    service = HttpSpeechRecognizer(
        "http://127.0.0.1:8107", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(SpeechUnavailable):
        service.load()
    assert not service.available
    service.load()
    assert service.available and service.load_error == ""
    service.close()


def test_timeout_is_visible_and_not_replayed():
    handle = Mock(side_effect=httpx.ReadTimeout("timeout"))
    service = HttpSpeechRecognizer(
        "http://127.0.0.1:8107", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(SpeechUnavailable, match="超时"):
        service.transcribe(b"pcm", 16000)
    assert handle.call_count == 1
    service.close()
