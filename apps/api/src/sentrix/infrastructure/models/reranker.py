"""BGE Reranker 的 HTTP 适配器：``POST /rerank`` → 每条候选的相关性分数。

模型是 BAAI/bge-reranker-v2-m3（XLM-RoBERTa 交叉编码器）。API 进程不 import
torch，模型跑在侧车里（``scripts/maintenance/bge_reranker_sidecar.py``）。默认
8104——8101 是 bge-m3 嵌入、8102 是 wemm、8103 是 chinese-clip，8140 以上留给
临时服务。

**失败语义与 ``legacy.py`` / ``wemm.py`` 一致**：任何失败返回空表、不抛；连续 5
次失败熔断 10 秒；健康检查 10 秒 TTL。这与嵌入器相同的理由——重排是可选增强，
侧车没起时检索必须照常返回 RRF 顺序，而不是让整条查询挂在一个可选依赖上。

**长度校验是必须的**：返回的分数条数必须等于送进去的文档数。少一条或多一条都
说明侧车与调用方对这批文档的理解不一致，此时宁可整批放弃（保留 RRF 顺序），也
不能按位置硬配——那会把 A 的分数贴到 B 上，排序错得毫无痕迹。
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Sequence

import httpx

CIRCUIT_OPEN_S = 10.0
HEALTH_TTL_S = 10.0
FAILURE_THRESHOLD = 5

#: 侧车 ``/health`` 与检索轨迹里报的模型名。
DEFAULT_MODEL_ID = "BAAI/bge-reranker-v2-m3"


class BgeSidecarReranker:
    """实现 ``Reranker``：查询 + 候选文档 → 相关性分数。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        model_id: str = DEFAULT_MODEL_ID,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout_s = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)
        # 熔断器是进程内可变状态；uvicorn 的同步端点在线程池里跑。
        self._lock = threading.Lock()
        self._failures = 0
        self._tripped_at = 0.0
        self._health_checked_at = 0.0
        self._health_value = False
        self._error = ""

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def load_error(self) -> str:
        """最近一次失败的原因。与 ``available`` 分开，理由同 ``wemm.py``。"""
        return self._error

    @property
    def available(self) -> bool:
        with self._lock:
            if self._tripped_at and time.monotonic() - self._tripped_at < CIRCUIT_OPEN_S:
                return False
            now = time.monotonic()
            if now - self._health_checked_at < HEALTH_TTL_S:
                return self._health_value
        try:
            response = self._client.get(f"{self._base_url}/health")
            healthy = response.status_code == 200
        except Exception:  # noqa: BLE001 - 探活失败就是不可用，不该让调用方处理异常
            healthy = False
        with self._lock:
            self._health_checked_at = time.monotonic()
            self._health_value = healthy
        return healthy

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """返回与 ``documents`` 等长的分数；任何失败返回空表。

        空查询或空文档表直接返回空表，且**不计入熔断失败**：那不是侧车的问题，
        让一次空查询把整条重排通道断开没有道理。
        """
        if not query.strip() or not documents:
            return []
        with self._lock:
            if self._tripped_at and time.monotonic() - self._tripped_at < CIRCUIT_OPEN_S:
                return []
        try:
            scores = self._request_scores(query, list(documents))
        except Exception as error:  # noqa: BLE001 - 降级，不抛
            self._record_failure(f"{type(error).__name__}: {error}")
            return []
        if len(scores) != len(documents):
            self._record_failure(f"侧车返回 {len(scores)} 个分数，候选 {len(documents)} 条")
            return []
        if not all(math.isfinite(score) for score in scores):
            self._record_failure("重排分数含非有限数值")
            return []
        self._record_success()
        return scores

    def _request_scores(self, query: str, documents: list[str]) -> list[float]:
        response = self._client.post(
            f"{self._base_url}/rerank",
            json={"query": query, "documents": documents},
        )
        response.raise_for_status()
        body = response.json()
        return [float(item) for item in (body.get("scores") or [])]

    def _record_failure(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            self._error = f"rerank: {reason}"
            self._health_value = False
            self._health_checked_at = 0.0
            if self._failures >= FAILURE_THRESHOLD:
                self._tripped_at = time.monotonic()
                self._failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._tripped_at = 0.0
            self._health_value = True
