"""外观取样移出请求：确认 / 合并只入队，后台线程逐人调视觉模型。

确认一个人时，``ObserveAppearance`` 给他每个事件挑一张脸送视觉模型，一次约 10 秒、
串行。2026-11-20 实测：出现在 1 / 5 / 16 个事件里的人，确认请求分别要 11 / 48 / 196 秒，
期间详情侧栏整段锁住。外观证据只有人物详情的「衣着」一段在读——事件参与者、重分段、
记忆图与检索都不依赖它（``ObserveAppearance`` 的 docstring：外观证据是增强，不是
确认的前置条件），所以它可以晚到。

- ``QueueAppearance``：装进 ``ReviewCluster`` / ``MergePeople`` 的 ``appearance``，
  入队并唤醒后台线程，请求立即继续；
- ``AppearanceSamplingExecutor``：**独立线程**，不与记忆修正同步共用——一个人三分钟
  的模型调用不能堵住改名、改关系的图谱同步。
"""

from __future__ import annotations

import logging
import threading

from sentrix.application.identity.observe_appearance import ObserveAppearance
from sentrix.domain.identity.models import ENTITY_STATUS_CONFIRMED, ENTITY_TYPE_PERSON
from sentrix.domain.identity.ports import EntityRepository, PersonAppearanceJobRepository

log = logging.getLogger(__name__)

_WAKE = threading.Event()


def wake_appearance_sampling() -> None:
    _WAKE.set()


class QueueAppearance:
    """``RequestAppearance`` 的生产实现：只记账，不调模型。"""

    def __init__(self, jobs: PersonAppearanceJobRepository) -> None:
        self._jobs = jobs

    def __call__(self, person_id: str) -> None:
        self._jobs.enqueue(person_id)
        wake_appearance_sampling()


class AppearanceSamplingExecutor:
    def __init__(
        self,
        *,
        jobs: PersonAppearanceJobRepository,
        entities: EntityRepository,
        observe: ObserveAppearance,
        idle_seconds: float = 2.0,
    ) -> None:
        self._jobs = jobs
        self._entities = entities
        self._observe = observe
        self._idle_seconds = idle_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            self._jobs.requeue_running()
        except Exception:  # noqa: BLE001 - 读不了队列也不阻止服务启动，线程里会再试
            log.exception("无法恢复外观取样任务")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="appearance-sampling", daemon=True)
        self._thread.start()
        _WAKE.set()

    def stop(self) -> None:
        self._stop.set()
        _WAKE.set()
        if self._thread is not None:
            # 正在跑的模型调用不等它：守护线程随进程退出，那一行留在 running，
            # 下次启动时 ``requeue_running`` 放回队列。
            self._thread.join(timeout=2)

    def run_once(self) -> int:
        """跑完当前所有到期任务，返回处理过的人数。"""
        handled = 0
        while not self._stop.is_set():
            claimed = self._jobs.claim_next()
            if claimed is None:
                break
            person_id, generation = claimed
            handled += 1
            try:
                if self._still_confirmed(person_id):
                    self._observe(person_id)
            except Exception as error:  # noqa: BLE001 - 失败留在队列里退避重试
                log.warning("外观取样失败（person=%s）: %s", person_id, error)
                self._jobs.fail(person_id, generation, str(error) or type(error).__name__)
                continue
            self._jobs.finish(person_id, generation)
        return handled

    def _still_confirmed(self, person_id: str) -> bool:
        """入队之后这个人可能被并走、驳回：那就不再给他采。"""
        entity = self._entities.get(person_id)
        return (
            entity is not None
            and entity.entity_type == ENTITY_TYPE_PERSON
            and entity.status == ENTITY_STATUS_CONFIRMED
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            _WAKE.wait(self._idle_seconds)
            _WAKE.clear()
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - 队列读写的瞬时失败留给下一轮
                log.exception("无法读取或更新外观取样任务")
