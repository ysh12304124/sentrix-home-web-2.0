#!/usr/bin/env python3
"""把已有观察的文本向量补上（T115d）。**幂等，可反复跑。**

**为什么要回填。** 写入侧（T115b）从那一刻起才存在。此前导入的照片有完整的语义
（caption / activity / place / …都在 ``observations`` 里），但 ``text_vectors`` 里
**一条文本向量都没有**——文本检索通道接上去只会返回空，而「空」与「没接」在读数上
分不开。回填把这条通道对存量数据也变成真的。

**幂等靠用例自己**：``IndexObservationText.index`` 先查 ``has_vector`` 再嵌，
所以重跑不会重复打 sidecar、也不会写重复行。

**所以「本次写了 0 行」不是失败**——第二次跑本来就是 0。判据要看**这个 scope 现在
有多少条观察真的有文本向量**（``覆盖率``），那才是「这条通道通没通」。第一版把
「本次写 0 行」当失败，于是幂等重跑会报错，而它其实完全正常。

**没有事件的观察只写两行。** 用例在 ``event_id`` 为 ``None`` 时跳过
``episodic/event`` 那一行（``reason="no_event"``）。存量照片多半没有事件——
它们是在事件归并接线之前导入的。**这不是缺陷，是那份数据的现状。**
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sentrix.application.retrieval.index_observation_text import (  # noqa: E402
    IndexObservationText,
    compose_text,
)
from sentrix.bootstrap.container import Container  # noqa: E402
from sentrix.config import load_settings  # noqa: E402
from sentrix.domain.retrieval import scheme  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="回填文本向量（幂等，可反复跑）")
    parser.add_argument("scope", nargs="?", default="album1")
    scope = parser.parse_args().scope

    container = Container.build(load_settings())
    index = IndexObservationText(embedder=container.text_embedder, vectors=container.text_vector_repository)

    observations = container.observation_repository.list_scope(scope, limit=10_000)
    print(f"scope={scope}  观察 {len(observations)} 条")
    print(f"嵌入模型 {container.text_embedder.model_id} / {container.text_embedder.dimension} 维\n")
    if not observations:
        print("没有观察可回填。")
        return 0

    # 一次性取回「观察 → 事件」，避免逐条查库
    events = container.event_repository.events_for_observations([item.id for item in observations])

    written = 0
    no_event = 0
    empty_text = 0
    failed: list[str] = []

    for observation in observations:
        if not compose_text(observation):
            empty_text += 1
            continue
        event_ids = events.get(observation.id) or ()
        outcome = index.index(observation, event_ids[0] if event_ids else None)
        if not outcome.indexed:
            failed.append(f"{observation.id}: {outcome.reason}")
            continue
        written += outcome.rows
        if not event_ids:
            no_event += 1

    # 真正的判据：**现在**有多少条观察带文本向量（不是本次写了几行）
    covered = sum(
        1
        for observation in observations
        if container.text_vector_repository.has_vector(
            space=scheme.SEMANTIC_SPACE,
            source_type="observation",
            source_id=observation.id,
            model_name=container.text_embedder.model_id,
        )
    )

    expected = len(observations) - empty_text
    print(f"本次写 {written} 行（重跑时正常为 0——用例自带幂等）")
    print(f"  其中 {no_event} 条没有事件（只写观察那两行）")
    print(f"  文本为空跳过 {empty_text} 条")
    print(f"\n文本向量覆盖率：{covered}/{expected}（有语义的观察）")

    if failed:
        print(f"\n✗ 失败 {len(failed)} 条：")
        for item in failed[:10]:
            print(f"    {item}")
        return 1
    if covered == 0 and expected > 0:
        print("\n✗ 覆盖率是 0——不要当成「已经写过了」，先看嵌入侧是不是真的连着。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
