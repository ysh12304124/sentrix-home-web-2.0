"""用户路由表：账号身份只在这里映射到本机 API，浏览器不能提供目标。

转发实现属于任务 2；本模块先固定“谁是合法目标”的规则，供 ready 检查使用。
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class RouteTableError(ValueError):
    pass


@dataclass(frozen=True)
class UserRoute:
    account_id: str
    api_url: str
    api_token: str


def _require_loopback(account_id: str, url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or not parts.port:
        raise RouteTableError(f"user {account_id}: api url must be http://host:port")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError as error:
        raise RouteTableError(f"user {account_id}: api host must be a literal IP") from error
    if not address.is_loopback:
        raise RouteTableError(f"user {account_id}: api host must be loopback")


class RouteTable:
    """从 0600 的 users 文件加载账号 → 本机 API 映射。"""

    def __init__(self, routes: dict[str, UserRoute], error: str = "") -> None:
        self._routes = routes
        self._error = error

    @classmethod
    def load(cls, path: Path) -> RouteTable:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return cls({}, f"route table unavailable: {error}")
        entries = document.get("users") if isinstance(document, dict) else None
        if not isinstance(entries, dict):
            return cls({}, "route table unavailable: missing users object")
        routes: dict[str, UserRoute] = {}
        for account_id, value in entries.items():
            if not isinstance(value, dict):
                return cls({}, f"route table invalid for {account_id}")
            api_url = str(value.get("api_url") or "")
            api_token = str(value.get("api_token") or "")
            try:
                _require_loopback(account_id, api_url)
            except RouteTableError as error:
                return cls({}, str(error))
            if not api_token:
                return cls({}, f"user {account_id}: api token missing")
            routes[str(account_id)] = UserRoute(str(account_id), api_url.rstrip("/"), api_token)
        return cls(routes)

    def get(self, account_id: str) -> UserRoute | None:
        return self._routes.get(account_id)

    def __len__(self) -> int:
        return len(self._routes)

    @property
    def error(self) -> str:
        return self._error

    @property
    def ready(self) -> bool:
        return not self._error
