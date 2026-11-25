"""记忆空间：一个 scope 的元数据。

它原来住在 ``infrastructure/persistence/repositories/spaces.py``——那里是**一行 SQL 的
返回形状**的常见住法，但记忆空间不是某个适配器的私事：``include_in_people`` 决定一个
空间里聚出来的人脸进不进人物视图（``FaceRepository.list_clusters`` 与
``EntityRepository.list_entities`` 都按它过滤），这是**领域规则**，只是恰好存在一行数据里。

字段就是 ``memory_spaces`` 的列，没有派生字段：这份记录是读出来的那一行，
不是给界面看的模型。

``created_at`` 是 2026-10-05 补的（S10a）。原先只带四列，而 1.0 的读侧是
``SELECT *``（``backend/db.py:1410``）——多出来的那一列**有读者**：PhotoBench 的
``_build_reuse_bases`` 按它挑最新的 scope、``/api/memory-spaces`` 按它排「新创建的在前」、
``_qa_scope_id_for_album`` 拿它当次级排序键。少了它那三处会一起退化成空串比较，
**不报错，只是结论默默地不对**。用 ``str`` 而不是 ``datetime``：1.0 回的就是那个
TEXT 列，而它唯一的用法是比较大小——ISO-8601 串的字典序就是时间序，
解析成 datetime 只会凭空引入一个没人用的时区约定。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemorySpace:
    id: str
    name: str
    kind: str
    status: str
    #: ``memory_spaces.created_at``，ISO-8601 串（``SqliteSpaceRepository.ensure``
    #: 写的是 ``datetime.now(UTC).isoformat()``）。消费方只拿它排序。
    created_at: str
    include_in_people: bool = True
