from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from sentrix.bootstrap.speech_service import create_speech_service
from sentrix.domain.assistant.ports import SpeechUnavailable


def test_host_service_loads_once_has_identity_and_rejects_busy_uploads(tmp_path):
    service = Mock(available=True)
    from unittest.mock import patch

    with patch("sentrix.bootstrap.speech_service.SenseVoiceRecognizer", return_value=service):
        app = create_speech_service(tmp_path)
    entered, finish = Event(), Event()

    def decode(*_args):
        entered.set()
        assert finish.wait(5)
        return "本次请求"

    service.transcribe.side_effect = decode
    with TestClient(app) as client:
        service.load.assert_called_once()
        identity = client.get("/health").json()
        assert identity["model_loads"] == 1
        assert identity["instance_id"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post,
                "/transcribe?sample_rate=16000",
                content=b"pcm",
                headers={"Content-Type": "application/octet-stream"},
            )
            try:
                assert entered.wait(5)
                assert client.post("/transcribe?sample_rate=16000").status_code == 429
                assert client.get("/health").json() == identity
            finally:
                finish.set()
            assert future.result().json() == {"text": "本次请求"}
        service.transcribe.side_effect = None
        service.transcribe.return_value = "新请求"
        assert client.post(
            "/transcribe?sample_rate=16000",
            content=b"pcm",
            headers={"Content-Type": "application/octet-stream"},
        ).json() == {"text": "新请求"}
        assert client.post("/transcribe?sample_rate=16000", json={}).status_code == 400
    service.close.assert_called_once()


def test_missing_weights_fail_startup(tmp_path):
    with (
        pytest.raises(SpeechUnavailable, match="prepare_sensevoice"),
        TestClient(create_speech_service(tmp_path)),
    ):
        pass


def test_cancelled_http_keeps_global_slot_until_native_decode_finishes(tmp_path):
    import asyncio
    import contextlib
    from unittest.mock import patch

    import httpx

    service = Mock(available=True)
    entered, finish = Event(), Event()

    def decode(*_args):
        entered.set()
        assert finish.wait(5)
        return "discarded result"

    service.transcribe.side_effect = decode
    with patch("sentrix.bootstrap.speech_service.SenseVoiceRecognizer", return_value=service):
        app = create_speech_service(tmp_path)

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://service",
            ) as client,
        ):
            task = asyncio.create_task(
                client.post(
                    "/transcribe?sample_rate=16000",
                    content=b"pcm",
                    headers={"Content-Type": "application/octet-stream"},
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                assert app.state.gate.locked()
                assert (await client.post("/transcribe?sample_rate=16000")).status_code == 429
            finally:
                finish.set()
            for _ in range(100):
                if not app.state.gate.locked():
                    break
                await asyncio.sleep(0.01)
            assert not app.state.gate.locked()

    asyncio.run(scenario())
