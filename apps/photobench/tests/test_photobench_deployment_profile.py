"""PhotoBench 的部署 profile：server / mac 只差 QA 并发与导入超时的默认值。

显式 ``PHOTOBENCH_QA_CONCURRENCY`` / run payload 的 ``import_timeout`` 永远优先。
"""

from __future__ import annotations

import pytest

from photobench.application.run_execution.service import RunExecution
from photobench.config import (
    Settings,
    default_import_timeout,
    default_qa_concurrency,
    deployment_profile,
)


def _clean(monkeypatch) -> None:
    monkeypatch.delenv("SENTRIX_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.delenv("PHOTOBENCH_QA_CONCURRENCY", raising=False)


def test_server_profile_keeps_endpoint_detection_and_two_hour_import(monkeypatch):
    _clean(monkeypatch)
    assert deployment_profile() == "server"
    assert default_qa_concurrency() is None
    assert default_import_timeout() == 7200
    assert Settings.load().deployment_profile == "server"


def test_mac_profile_sets_two_concurrency_and_one_day_import(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    assert deployment_profile() == "mac"
    assert default_qa_concurrency() == 2
    assert default_import_timeout() == 86400
    assert Settings.load().deployment_profile == "mac"


def test_explicit_qa_concurrency_env_wins(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    monkeypatch.setenv("PHOTOBENCH_QA_CONCURRENCY", "4")
    assert RunExecution._env_qa_concurrency() == 4


def test_qa_concurrency_falls_back_to_the_profile(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    assert RunExecution._env_qa_concurrency() == 2


def test_unknown_profile_is_rejected(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "windows")
    with pytest.raises(ValueError):
        deployment_profile()
