"""``ConfigStore`` 的文件实现：``apps/photobench/config/*.json``。

**配置根从 1.0 的 ``services/photobench/config/`` 换成 ``apps/photobench/config/``**
——2.0 的 PhotoBench 是 ``apps/photobench``（``pyproject.toml`` 与 ``src/`` 都在那里），
配置跟着应用走，不跟着 1.0 的目录结构走。1.0 里这些路径是模块级常量
（``RUNTIME_CONNECTION_CONFIG_PATH`` ``:67`` 等），写死在源码里；2.0 由装配点决定根，
所以同一份代码可以在别的部署上指到别的配置目录。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def read_build_revision(repo_root: Path) -> str:
    """A release records its committed revision at packaging time, once at boot."""
    if revision := os.environ.get("PHOTOBENCH_BUILD_REVISION"):
        return revision
    try:
        value = json.loads((repo_root / "build-info.json").read_text(encoding="utf-8"))
        return str(value["revision"])
    except (OSError, ValueError, KeyError, TypeError):
        return "development"


class JsonConfigStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def read_json(self, name: str) -> dict[str, Any] | None:
        path = self._root / name
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            # 1.0 没有这条判断（``value.get(...)`` 会 AttributeError）；这里换成明确的
            # 值错误，由 application 翻成 1.0 那条 ``invalid ... config`` 文案。
            raise ValueError(f"{path} 的顶层不是 JSON 对象，而是 {type(value).__name__}")
        return value

    def write_json(self, name: str, value: Mapping[str, Any]) -> None:
        """原子写：1.0 ``persist_runtime_connection_config``（``:445-447``）的做法。

        写一半被读到的配置文件比写失败更糟——进程重启后它是一份语法完好的**错误**配置。
        """
        path = self._root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def path_for(self, name: str) -> str:
        return str((self._root / name).resolve())
