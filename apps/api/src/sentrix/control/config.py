"""控制服务的配置。

密钥只从 0600 的服务配置注入；``diagnostics()`` 只回非敏感有效值。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONTROL_DB = Path("/srv/sentrix/control/control.db")
DEFAULT_USERS_FILE = Path("/srv/sentrix/control/users.json")
DEFAULT_MODEL_CLIENTS_FILE = Path("/srv/sentrix/control/model-clients.json")


class ControlSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTRIX_CONTROL_",
        extra="forbid",
        populate_by_name=True,
    )

    db_path: Path = DEFAULT_CONTROL_DB
    listen_host: str = "127.0.0.1"
    listen_port: int = 8210

    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None

    # 账号 ID → {api_url, api_token}。文件权限必须 0600；令牌不得回传浏览器。
    users_file: Path = DEFAULT_USERS_FILE

    model_upstream_url: str = "http://127.0.0.1:8100/v1"
    model_upstream_api_key: str = ""
    model_id: str = ""
    model_slots: int = Field(default=2, ge=1)
    # 令牌 → {user_id, kind=user|benchmark}；PhotoBench 令牌固定评测级。
    model_clients_file: Path = DEFAULT_MODEL_CLIENTS_FILE

    sample_interval_s: float = Field(default=10.0, gt=0)
    restart_units: str = ""

    session_idle_s: int = Field(default=8 * 3600, gt=0)
    session_max_s: int = Field(default=7 * 24 * 3600, gt=0)
    secure_cookie: bool = True

    build_sha: str = "development"

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def restart_unit_list(self) -> tuple[str, ...]:
        return tuple(unit.strip() for unit in self.restart_units.split(",") if unit.strip())

    def diagnostics(self, *, route_users: int, ready: bool) -> dict[str, object]:
        """只包含可公开的有效配置，不含令牌、账号列表或文件系统私有路径。"""
        return {
            "service": "sentrix-control",
            "build_sha": self.build_sha,
            "ready": ready,
            "listen": {"host": self.listen_host, "port": self.listen_port},
            "tls": bool(self.tls_cert_file and self.tls_key_file),
            "route_users": route_users,
            "model": {
                "upstream": _public_url(self.model_upstream_url),
                "model_id": self.model_id,
                "slots": self.model_slots,
            },
            "sample_interval_s": self.sample_interval_s,
            "restart_units": list(self.restart_unit_list()),
        }

    def users_document(self) -> dict[str, object]:
        """读取用户路由映射；调用方必须把文件权限与解析失败分别处理。"""
        raw = json.loads(self.users_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("users file must be a JSON object")
        return raw


def _public_url(url: str) -> str:
    """去掉 userinfo 与查询串后返回 scheme://host:port/path。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path.rstrip('/')}"
