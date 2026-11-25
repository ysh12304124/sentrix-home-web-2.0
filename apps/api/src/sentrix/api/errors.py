"""领域错误 → HTTP 状态。所有 v1 端点共用同一结构。

**映射只有这一处。** 用例抛出**带码**的错误（基类在 ``domain/errors.py``，理由写在
那里），路由把它们原样放行；这个处理器是唯一把码翻成状态与响应体的地方。路由里出现
第二个映射点时，「状态码从哪来」就又变成看运气了。

本文件里的四个子类是 **api 层自己**在边界上发现的问题（找不到 asset、目录不存在、
bbox 非法）：它们不是用例的结论，而是请求本身在这里就没法继续。用同一族类型而不是
``HTTPException``，是为了让**所有**领域错误的响应形状只有一种。
"""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sentrix.domain.errors import DomainError


class DomainErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, object]


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404


class InvalidRequestError(DomainError):
    code = "invalid_request"
    status_code = 400


class ConflictError(DomainError):
    code = "conflict"
    status_code = 409


class UnprocessableError(DomainError):
    """请求语法没问题、内容无法处理（1.0 的人脸裁剪端点在框非法时用的就是 422）。"""

    code = "unprocessable"
    status_code = 422


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Starlette 的 add_exception_handler 要求第二参数是 Exception（函数参数逆变），
    # 而这里注册的就是 DomainError；cast 零开销，运行时要拿到的仍然是 DomainError。
    error = cast(DomainError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=DomainErrorResponse(code=error.code, message=error.message, detail={}).model_dump(),
    )
