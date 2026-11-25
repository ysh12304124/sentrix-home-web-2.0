"""数据集展示层的纯投影：manifest 摘要、QA 文件解析、媒体 MIME。

1.0 这三段都在 ``OrchestratorHandler`` 那一层：``list_manifests``（``:5144``）、
``load_jsonl``（``:1600``）、``_serve_file`` 里的 MIME 头（``:8022``）。2.0 只把
**不碰 IO 的那部分**搬到这里——目录遍历与文件读取在 ``AlbumSource`` 端口上
（``infrastructure/datasets/albums.py``）。

**它们为什么落在 domain 而不是 api。** 这三段是「1.0 的响应体怎么长出来」的规则，
不是 HTTP 映射。放进路由的话，「缺 ``album_id`` 的目录要跳过」这条口径就只活在一个
``except`` 子句里，谁也没法在不发一次请求的情况下钉住它。
"""

from __future__ import annotations

import json
import mimetypes
from typing import Any

#: 1.0 ``:7716``：相册媒体只从这三个子目录交付。
ALBUM_MEDIA_COLLECTIONS = frozenset({"photos", "faces", "videos", "documents"})


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """1.0 ``:5152-5159`` 的逐行照搬。

    ``album_id`` / ``album_name`` **用下标取**（不是 ``.get``）：1.0 让 ``KeyError``
    漏到调用方的 ``except`` 上，那条目录于是被**跳过**。换成 ``.get`` 会给缺字段的
    目录编一个 ``None``——前端下拉里就多一个点不开的相册，而它连名字都没有。

    三个计数同样照搬 ``len(m.get(key, []))``：1.0 没有 ``or []``，所以
    ``"faces": null`` 会抛 ``TypeError`` 并**中断整个列表**（那不是 ``except`` 里的
    三种异常）。改成 ``or []`` 会把一份坏 manifest 从「报错」变成「少数几张」。
    """
    return {
        "album_id": manifest["album_id"],
        "album_name": manifest["album_name"],
        "face_count": len(manifest.get("faces", [])),
        "photo_count": len(manifest.get("photos", [])),
        "video_count": len(manifest.get("videos", [])),
        "document_count": len(manifest.get("documents", [])),
        "qa_sets": list(manifest.get("qa_sets", {}).keys()),
    }


def qa_set_file(manifest: dict[str, Any], qa_set: str) -> str | None:
    """1.0 ``:7695-7699``：``qa_sets`` 既可能是 dict（新题库），也可能是 list（老题库）。

    返回要拼到 album 目录下的**相对路径**；取不到时 ``None``（1.0 的 ``if not qa_file``
    把「键不存在」与「值是空串」当同一件事）。
    """
    qa_sets = manifest.get("qa_sets") or {}
    if isinstance(qa_sets, dict):
        value = qa_sets.get(qa_set)
        return str(value) if value else None
    return qa_set if qa_set in qa_sets else None


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    """1.0 ``load_jsonl``（``:1600``）：空行跳过，其余按 JSON 解析。

    **解析失败不吞**——1.0 让 ``JSONDecodeError`` 冒到 handler 的兜底 ``except`` 上，
    回一句可读的 400。吞掉它，一份写坏的题库会被当成「这道题没有内容」。
    """
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def album_media_type(name: str) -> str:
    """1.0 ``:8022-8025``：``mimetypes`` 认不出就用 ``application/octet-stream``，
    文本与 JS **补 charset**。

    补 charset 不是锦上添花：1.0 的这份头就是浏览器解 ``.json`` / ``.txt`` 的依据，
    而 ``mimetypes`` 只给 ``text/plain``——按 latin-1 解的话中文题库会花屏。2.0 的
    ``FileResponse`` 用的是同一个 ``mimetypes.guess_type``，但**不加 charset**
    （``starlette/responses.py:315``），所以这里显式算出来传给它。
    """
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type == "application/javascript":
        return f"{content_type}; charset=utf-8"
    return content_type
