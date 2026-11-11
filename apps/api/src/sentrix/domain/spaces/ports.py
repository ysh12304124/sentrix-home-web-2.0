"""记忆空间的端口。SQL 在 ``infrastructure/persistence/repositories/spaces.py``。

**为什么现在才补这个端口。** S3 把这个字段的类型直接写成 ``SqliteSpaceRepository``，
理由是「还没有 application / api 消费者，补端口属投机抽象」——那条理由当时成立，
因为那时唯一的使用者是测试。现在 ``Container`` 的每一个字段都按端口声明这条不变量
（``tests/unit/test_container_wiring.py``）上，它是一条**没有理由的例外**：说不出理由的
例外名单会把不变量重新变成摆设。补端口的代价是一个 8 行的 Protocol，收益是例外从两条
降到一条。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sentrix.domain.spaces.models import MemorySpace


class SpaceRepository(Protocol):
    def ensure(self, scope_id: str, name: str, kind: str = "household") -> str:
        """保证这个空间存在，返回它的 id。已存在时**什么都不改**（幂等）。

        返回 id 而不是布尔：调用方拿它直接往下用（建资产、建事件），
        「建没建」这个信息没有人消费。
        """
        ...

    def list_all(self) -> Sequence[MemorySpace]: ...

    def get(self, scope_id: str) -> MemorySpace | None: ...
