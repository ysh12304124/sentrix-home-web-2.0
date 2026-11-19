import dataclasses
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentrix.domain.assistant.ports import SpeechBusy


@pytest.fixture
def speech_client(isolated_app):
    service = Mock(available=True, load_error="")
    service.transcribe.return_value = "找海边的照片"
    isolated_app.state.container = dataclasses.replace(
        isolated_app.state.container,
        speech_recognizer=service,
    )
    # Real application lifespan and routing, with only the recognition port replaced.
    with TestClient(isolated_app) as client:
        yield client, service
    service.close.assert_called_once()


def test_startup_loads_once_and_requests_reuse(speech_client):
    client, service = speech_client
    service.load.assert_called_once()
    assert client.get("/api/v1/assistant/speech/status").json() == {
        "available": True,
        "message": "",
    }
    audio = np.zeros(16000, dtype="<f4").tobytes()
    for _ in range(2):
        response = client.post(
            "/api/v1/assistant/speech/transcribe?sample_rate=16000",
            content=audio,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert response.json() == {"text": "找海边的照片"}
    assert service.transcribe.call_count == 2
    service.transcribe.assert_called_with(audio, 16000)
    service.load.assert_called_once()


def test_missing_model_is_503_and_does_not_load_on_request(speech_client):
    client, service = speech_client
    service.available = False
    service.load_error = "模型缺失"
    response = client.post("/api/v1/assistant/speech/transcribe?sample_rate=16000", content=b"")
    assert response.status_code == 503
    assert response.json()["code"] == "speech_unavailable"
    service.transcribe.assert_not_called()
    service.load.assert_called_once()


def test_bad_transport_and_sample_rate_rejected(speech_client):
    client, service = speech_client
    assert (
        client.post(
            "/api/v1/assistant/speech/transcribe?sample_rate=16000", json={"audio": "bad"}
        ).status_code
        == 400
    )
    assert client.post("/api/v1/assistant/speech/transcribe?sample_rate=0", content=b"").status_code == 422
    response = client.post(
        "/api/v1/assistant/speech/transcribe?sample_rate=8000",
        content=b"0" * (8000 * 60 * 4 + 1),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    service.transcribe.assert_not_called()


def test_busy_is_explicit_and_never_automatically_retried(speech_client):
    client, service = speech_client
    service.transcribe.side_effect = SpeechBusy("正在识别")
    response = client.post(
        "/api/v1/assistant/speech/transcribe?sample_rate=16000",
        content=b"audio",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "speech_busy"
    service.transcribe.assert_called_once()


def test_failed_startup_keeps_text_api_alive_and_reports_unavailable(isolated_app):
    from sentrix.domain.assistant.ports import SpeechUnavailable

    service = Mock(available=False, load_error="模型未就绪")
    service.load.side_effect = SpeechUnavailable("模型未就绪")
    isolated_app.state.container = dataclasses.replace(
        isolated_app.state.container,
        speech_recognizer=service,
    )
    with TestClient(isolated_app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/assistant/speech/status").json() == {
            "available": False,
            "message": "模型未就绪",
        }
    service.load.assert_called_once()
    service.close.assert_called_once()
