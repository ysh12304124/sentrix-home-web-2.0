"""记忆空间（相册）列表。

**这是 2.0 一直缺的一个端点。** 1.0 有它——``src/api.js:17`` 的
``memorySpaces: () => request("/api/memory-spaces")``，侧边栏的相册切换器靠它。
2.0 的 domain 端口（``domain/spaces/ports.py``）、仓储
（``infrastructure/persistence/repositories/spaces.py``）与容器字段都在，
**只差这一层 HTTP**，于是前端 F1 的相册切换器拿不到列表。

**返回体形状按 1.0 前端的消费处定**，不是我自己选的：``src/app.js:1161`` 写的是
``state.spaces = spaceResult[0].value.spaces || []``，即后端给 ``{"spaces": [...]}``
而不是裸数组。照搬它，从 1.0 迁过来的前端代码不用改消费方式。
"""

from fastapi import APIRouter, Request

router = APIRouter(tags=["spaces"])


@router.get("/memory-spaces")
def list_memory_spaces(request: Request) -> dict[str, list[dict[str, str]]]:
    spaces = request.app.state.container.spaces.list_all()
    return {
        "spaces": [
            {
                "id": space.id,
                "name": space.name,
                "kind": space.kind,
                "status": space.status,
                # 1.0 的 ``SELECT *`` 把它一起回了，而 PhotoBench 按它排「新创建的在前」
                # 并挑最新 scope 做复用基座。少了它那两处退化成空串比较（S10a）。
                "created_at": space.created_at,
            }
            for space in spaces
        ]
    }
