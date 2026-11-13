"""主模型「真实生成」健康探针。

`/models` 只能证明端点活着，不能证明推理可用。体验执行器在放行新作业前需要一个
有缓存、带冷却的真实生成结果：短消息、`max_tokens=1`、`stream=false`，成功且
响应含非空 `choices` 才算可用。并发调用共用一次探测。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx

from sentrix.infrastructure.models.gateway import model_auth_headers

PROBE_TIMEOUT_S = 10.0
SUCCESS_TTL_S = 30.0
FAILURE_TTL_S = 5.0


class ModelGenerationHealth:
    def __init__(
        self,
        base_url: str,
        model_name: Callable[[], str],
        *,
        timeout_s: float = PROBE_TIMEOUT_S,
        success_ttl_s: float = SUCCESS_TTL_S,
        failure_ttl_s: float = FAILURE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._timeout_s = timeout_s
        self._success_ttl_s = success_ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._available = False
        self._models_ok = False
        self._error = "not_checked"
        self._next_check = 0.0

    def check(self) -> tuple[bool, str]:
        with self._lock:
            now = self._clock()
            if now < self._next_check:
                return self._available, self._error
            models_ok = False
            try:
                model = self._model_name()
                models_ok = True
                response = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers={**model_auth_headers(), "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                    timeout=self._timeout_s,
                )
                available = False
                error = f"HTTP {response.status_code}"
                if response.is_success:
                    try:
                        choices = response.json().get("choices")
                    except ValueError:
                        choices = None
                    if isinstance(choices, list) and len(choices) > 0:
                        available = True
                        error = ""
                    else:
                        error = "generation returned no choices"
            except Exception as exc:  # noqa: BLE001 - 探针只回答可用性
                available = False
                error = f"{type(exc).__name__}: {exc}"
            self._available = available
            self._models_ok = models_ok
            self._error = error
            self._next_check = now + (self._success_ttl_s if available else self._failure_ttl_s)
            return available, error

    def describe(self) -> dict[str, object]:
        return {
            "available": self._available,
            "models_ok": self._models_ok,
            "last_error": self._error,
        }
