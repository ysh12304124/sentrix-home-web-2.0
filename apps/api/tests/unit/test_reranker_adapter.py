"""BGE 重排适配器：正常路径 + 降级 + 条数校验 + 熔断 + 健康探针。

与 ``test_legacy_embedders.py`` 同一套口径：侧车是可选依赖，失败必须返回空表让
上层保留 RRF 顺序并标记 ``degraded``，不能把查询挂在一个 HTTP 超时上。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from sentrix.infrastructure.models import reranker
from sentrix.infrastructure.models.reranker import (
    CIRCUIT_OPEN_S,
    FAILURE_THRESHOLD,
    BgeSidecarReranker,
)

BASE = "http://127.0.0.1:8104"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), seen


def reranker_with(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BgeSidecarReranker, list[httpx.Request]]:
    client, seen = build(handler)
    return BgeSidecarReranker(BASE, client=client), seen


def test_sends_the_documented_body_and_returns_scores():
    adapter, seen = reranker_with(
        lambda request: httpx.Response(200, json={"scores": [0.9, 0.1], "model": "x"})
    )
    assert adapter.model_id == "BAAI/bge-reranker-v2-m3"
    assert adapter.rerank("海边", ["海边日落", "雪山"]) == [0.9, 0.1]
    assert seen[0].url.path == "/rerank"
    body = json.loads(seen[0].content)
    assert body == {"query": "海边", "documents": ["海边日落", "雪山"]}


def test_empty_input_does_not_hit_the_network_or_trip_the_breaker():
    adapter, seen = reranker_with(lambda request: httpx.Response(500))
    assert adapter.rerank("  ", ["文档"]) == []
    assert adapter.rerank("查询", []) == []
    assert seen == []


@pytest.mark.parametrize(
    "scores",
    [
        [0.5],  # 少一条
        [0.5, 0.4, 0.3],  # 多一条
    ],
)
def test_score_count_mismatch_is_a_failure(scores):
    """条数对不上说明侧车与调用方对这批文档的理解不一致，整批放弃。"""
    adapter, _ = reranker_with(lambda request: httpx.Response(200, json={"scores": scores}))
    assert adapter.rerank("查询", ["甲", "乙"]) == []
    assert "2" in adapter.load_error and str(len(scores)) in adapter.load_error


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({"scores": [0.5, float("nan")]}),
        json.dumps({"scores": [0.5, float("inf")]}),
    ],
)
def test_non_finite_scores_are_rejected(content):
    adapter, _ = reranker_with(lambda request: httpx.Response(200, content=content))
    assert adapter.rerank("查询", ["甲", "乙"]) == []
    assert adapter.load_error


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_failure_returns_empty_and_records_the_reason(status):
    adapter, _ = reranker_with(lambda request: httpx.Response(status))
    assert adapter.rerank("查询", ["甲"]) == []
    assert str(status) in adapter.load_error


def test_failure_returns_empty_and_trips_the_breaker(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(reranker.time, "monotonic", clock.monotonic)
    adapter, seen = reranker_with(lambda request: httpx.Response(500))

    for _ in range(FAILURE_THRESHOLD):
        assert adapter.rerank("查询", ["甲"]) == []
    assert len(seen) == FAILURE_THRESHOLD

    # 熔断期内不再打网络。
    assert adapter.rerank("查询", ["甲"]) == []
    assert len(seen) == FAILURE_THRESHOLD

    clock.advance(CIRCUIT_OPEN_S + 0.1)
    assert adapter.rerank("查询", ["甲"]) == []
    assert len(seen) == FAILURE_THRESHOLD + 1


def test_health_is_the_readiness_signal():
    adapter, seen = reranker_with(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert adapter.available is True
    assert seen[-1].url.path == "/health"

    dead, _ = reranker_with(lambda request: httpx.Response(503))
    assert dead.available is False
