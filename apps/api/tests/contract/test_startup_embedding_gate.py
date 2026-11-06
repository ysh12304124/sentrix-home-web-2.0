"""Schema assembly is offline; entering the real ASGI lifespan checks inference."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


def test_schema_export_does_not_invoke_embedding_model(isolated_app, monkeypatch):
    smoke = Mock(side_effect=AssertionError("schema export must be offline"))
    monkeypatch.setattr(isolated_app.state.container.embedding_health, "smoke_test", smoke)
    assert isolated_app.openapi()["paths"]
    smoke.assert_not_called()


def test_real_startup_requires_successful_embedding_inference(isolated_app, monkeypatch):
    smoke = Mock(return_value=(False, "sidecar inference failed"))
    monkeypatch.setattr(isolated_app.state.container.embedding_health, "smoke_test", smoke)
    with pytest.raises(RuntimeError, match="sidecar inference failed"), TestClient(isolated_app):
        pass
    smoke.assert_called_once_with()


def test_real_startup_runs_embedding_inference_once(isolated_app, monkeypatch):
    smoke = Mock(return_value=(True, ""))
    monkeypatch.setattr(isolated_app.state.container.embedding_health, "smoke_test", smoke)
    with TestClient(isolated_app) as client:
        assert client.get("/api/v1/health").status_code == 200
    smoke.assert_called_once_with()
