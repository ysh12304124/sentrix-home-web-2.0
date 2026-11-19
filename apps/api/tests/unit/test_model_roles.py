"""Cloud role profiles: explicit chat/vision models and fail-loud validation."""

from __future__ import annotations

import json

import pytest

from sentrix.bootstrap.model_roles import load_model_profile


def _write(tmp_path, payload):
    path = tmp_path / "cloud.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _roles(**overrides):
    role = {
        "base_url": "https://example.test/v1",
        "model": "m",
        "api_key_env": "TEST_CLOUD_KEY",
        "context_tokens": 32768,
        "max_output_tokens": 4096,
    }
    vision = dict(role, supports_vision=True)
    return {"profile": "cloud", "roles": {"chat": dict(role), "vision": vision}, **overrides}


def test_loads_chat_and_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLOUD_KEY", "secret")
    profile = load_model_profile(_write(tmp_path, _roles()))
    assert profile.roles["chat"].model == "m"
    assert profile.roles["vision"].supports_vision is True
    assert profile.roles["chat"].api_key == "secret"


def test_vision_is_required(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLOUD_KEY", "secret")
    payload = _roles()
    del payload["roles"]["vision"]
    with pytest.raises(ValueError, match="roles.vision"):
        load_model_profile(_write(tmp_path, payload))


def test_vision_must_declare_supports_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLOUD_KEY", "secret")
    payload = _roles()
    payload["roles"]["vision"]["supports_vision"] = False
    with pytest.raises(ValueError, match="supports_vision"):
        load_model_profile(_write(tmp_path, payload))


def test_missing_api_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_CLOUD_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_CLOUD_KEY"):
        load_model_profile(_write(tmp_path, _roles()))


def test_role_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLOUD_KEY", "secret")
    payload = _roles()
    payload["roles"]["chat"].pop("base_url")
    payload["roles"]["chat"].pop("model")
    profile = load_model_profile(
        _write(tmp_path, payload),
        default_base_url="https://fallback.test/v1",
        default_model="fallback-model",
    )
    assert profile.roles["chat"].base_url == "https://fallback.test/v1"
    assert profile.roles["chat"].model == "fallback-model"
