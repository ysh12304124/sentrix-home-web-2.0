"""数据集展示层路由（1.0 ``do_GET`` ``:7685`` / ``:7690`` / ``:7712``）。

三条都不做计算：参数原样交给 ``DatasetCatalog``，错误文案由它抛的领域错误决定
（``{"error": ...}`` 由 ``api/errors.py`` 统一成形）。

**为什么查询参数声明成 ``str`` 而不是 ``int``。** 与 ``runs.py`` 同一条理由：
FastAPI 的类型化声明会把「缺参数」变成 422 ``{"detail": ...}``，而 1.0 回的是
400 ``{"error": "album_id and qa_set are required"}``。前端按后者读。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from photobench.api.deps import get_container
from photobench.domain.datasets.catalog import ALBUM_MEDIA_COLLECTIONS, album_media_type
from photobench.domain.datasets.qa_taxonomy import taxonomy_document
from photobench.domain.errors import NotFoundError

router = APIRouter(tags=["datasets"])


@router.get("/manifests")
def list_manifests(request: Request) -> dict[str, Any]:
    """1.0 ``:7685``。"""
    return get_container(request).datasets.list_manifests()


@router.get("/manifests/{album_id}")
def get_manifest(request: Request, album_id: str) -> dict[str, Any]:
    """1.0 ``:7689``。1.0 对路径参数做了一次 ``unquote``；FastAPI 自己会解，不再手动解。

    读不到 → 404 ``{"error": "not found"}``（1.0 的文案就是这两个字）。
    """
    return get_container(request).datasets.get_manifest(album_id)


@router.get("/qa-dataset")
def qa_dataset(
    request: Request,
    album_id: str = "",
    qa_set: str = "",
    sentrix_url: str = "",
) -> dict[str, Any]:
    """1.0 ``:7695``。``sentrix_url`` 缺省取配置里的——调用方可以指向别的 Sentrix。"""
    return get_container(request).datasets.qa_dataset(album_id, qa_set, sentrix_url)


@router.get("/albums/{album}/{coll}/{file_name:path}")
def album_media(request: Request, album: str, coll: str, file_name: str) -> FileResponse:
    """1.0 ``:7713`` + ``_resolve_album_media_file``（``:1188``）+ ``_serve_file``（``:8020``）。

    **``file_name`` 用 ``:path`` 收**：1.0 是 ``split("/", 2)``，所以第三段可以带子目录
    （``photos/2026/a.jpg``）。用普通的 ``{file_name}`` 会让带子目录的名字落到 404 上。

    **Range 不手写。** 1.0 的 ``_serve_file``（``:8020`` 起）自己解析 ``Range`` 并把
    视频按段发；2.0 的 starlette 是 1.6.0，``FileResponse`` 原生支持 Range
    （``responses.py:341`` 的 ``__call__``），非法 Range 也有同样的 416 +
    ``Content-Range: bytes */<size>``。照抄那 60 行只会多一份会漂的实现。

    **一处真实差异**：格式错误的 Range（比如 ``bytes=abc``）starlette 回 **400**，
    而 1.0 的 ``re.fullmatch`` 不匹配时回 **416**。两者都带不了 body，且浏览器不会
    发这种头，所以照 starlette 的行为走，登记在行为基线里。
    """
    path = (
        get_container(request).datasets.album_media_file(album, coll, file_name)
        if coll in ALBUM_MEDIA_COLLECTIONS
        else None
    )
    if path is None:
        raise NotFoundError("media not found")
    # ``FileResponse`` 的 ``mimetypes.guess_type`` 不加 ``charset``，而 1.0 加；
    # 显式传 media_type 让这两个口径一致（见 ``album_media_type``）。
    return FileResponse(path, media_type=album_media_type(Path(path).name))


class ContentTask(BaseModel):
    label: str
    subtypes: dict[str, str]


class QaTaxonomyResponse(BaseModel):
    version: str
    schema_version: str
    tasks: dict[str, ContentTask]
    actions: dict[str, str]
    answerability: dict[str, str]
    difficulty: dict[str, str]
    special: dict[str, str]


@router.get("/qa-taxonomy", response_model=QaTaxonomyResponse)
def qa_taxonomy() -> dict[str, Any]:
    """The versioned dictionary used by QA files, filters and analysis."""
    return taxonomy_document()
