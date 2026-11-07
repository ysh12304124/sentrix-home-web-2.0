"""把 judge 召回图取回、缩放、转成 data URL——1.0 ``_inline_judge_images``（``:1549``）。

**为什么在 infrastructure 而不是 ``application/run_execution/judge.py``。**
``photobench-application-purity`` 的 forbidden 名单里有 **PIL**（与 ``domain-purity``
同一份清单）：application 不许 import 图像库。证据判分要缩放 JPEG，这件事本身就
「碰字节 + 用第三方解码器」，按分层只能落在 infrastructure——与 ``httpx`` 的处理
同一口径（HTTP 也走端口/适配器，不直接进 application）。

**一个真实的门禁红**：这段代码最初写在 ``JudgeRunner._inline_images`` 里，``arch``
那一步报 ``photobench.application is not allowed to import PIL:
photobench.application.run_execution.judge -> PIL (l.421)``——**惰性 import 也照样算**，
import-linter 看的是 AST 里的 import 节点，不是运行时机。

PIL 惰性导入。可选的召回图片读取失败时跳过该图；标有
``require_attachment=True`` 的GT图片向上抛出实际错误，由评分记录保存。
"""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.sentrix.ports import API_PREFIX

#: 1.0 ``:1549`` 的 ``max_dimension``。不设上限会把提示词撑爆。
JUDGE_IMAGE_MAX_DIMENSION = 896


def inline_judge_images(
    http: JsonHttpClient,
    sentrix_url: str,
    images: Sequence[Mapping[str, Any]],
    assets_by_name: Mapping[str, list[dict[str, Any]]],
    *,
    max_dimension: int = JUDGE_IMAGE_MAX_DIMENSION,
) -> list[dict[str, Any]]:
    """按 ``asset_id`` 取回图片字节，缩到 ``max_dimension``，转 JPEG data URL。

    单张失败只跳过它自己（1.0 的 ``except Exception: continue``）——一张取不回来的图
    不该让整次证据判分变成错误。asset_id 走 ``quote(safe="")``（与 ``SentrixClient``
    同一条口径：路径参数里斜杠会切段）。
    必需的GT附件通过 ``require_attachment=True`` 保留失败原因。
    """
    assets_by_id = {
        str(asset["id"]): asset for assets in assets_by_name.values() for asset in assets if asset.get("id")
    }
    content: list[dict[str, Any]] = []
    for image in images:
        asset = assets_by_id.get(str(image.get("asset_id") or ""))
        asset_id = str((asset or {}).get("id") or image.get("asset_id") or "")
        if not asset_id:
            continue
        try:
            from PIL import Image

            image_bytes = http.request_bytes(
                f"{sentrix_url.rstrip('/')}{API_PREFIX}/assets/{quote(asset_id, safe='')}/file", timeout=60
            )
            with Image.open(io.BytesIO(image_bytes)) as source:
                resized = source.convert("RGB")
                resized.thumbnail((max_dimension, max_dimension))
                output = io.BytesIO()
                resized.save(output, format="JPEG", quality=90, optimize=True)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
                    },
                }
            )
        except Exception:  # noqa: BLE001 —— evidence may omit an unavailable image
            if image.get("require_attachment") is True:
                raise  # Frozen GT failures must retain the actual HTTP/decode error.
            continue
    return content


__all__ = ["JUDGE_IMAGE_MAX_DIMENSION", "inline_judge_images"]
