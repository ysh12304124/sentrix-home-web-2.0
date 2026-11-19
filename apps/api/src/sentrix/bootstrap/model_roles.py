"""云端 LLM 角色配置。

`SENTRIX_LLM_PROFILE=cloud` 时从 JSON 读取 `chat` / `vision` 两个角色。文件里只保存
**环境变量名**（``api_key_env``），密钥本身在进程环境里；启动校验通过后只把值交给
模型适配器，不写日志、不进 ``runtime_configuration``。

`local` 路径完全不读这个模块，仍然由 ``container.py`` 构造单个 ``ServedModel``。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RoleModelConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    api_key_env: str
    context_tokens: int
    max_output_tokens: int
    timeout_s: float
    supports_vision: bool
    payload: Mapping[str, Any] = field(default_factory=dict)
    retry: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    profile: str
    roles: Mapping[str, RoleModelConfig]
    agent: Mapping[str, Any] = field(default_factory=dict)
    vision: Mapping[str, Any] = field(default_factory=dict)
    memory: Mapping[str, Any] = field(default_factory=dict)


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object, *, what: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} 必须是对象")
    return dict(value)


def _positive_int(value: object, *, what: str) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{what} 必须是正整数") from error
    if number <= 0:
        raise ValueError(f"{what} 必须是正整数")
    return number


def _positive_float(value: object, *, what: str) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{what} 必须是正数") from error
    if number <= 0:
        raise ValueError(f"{what} 必须是正数")
    return number


def _role(
    name: str,
    raw: Mapping[str, Any],
    *,
    default_base_url: str,
    default_model: str,
    require_vision: bool,
) -> RoleModelConfig:
    base_url = _text(raw.get("base_url")) or default_base_url
    if not base_url:
        raise ValueError(f"roles.{name}.base_url 为空，且没有可回退的 SENTRIX_VLM_BASE_URL")
    model = _text(raw.get("model")) or default_model
    if not model:
        raise ValueError(f"roles.{name}.model 为空，且没有可回退的 SENTRIX_VLM_MODEL")
    api_key_env = _text(raw.get("api_key_env")) or "SENTRIX_CLOUD_API_KEY"
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"roles.{name} 需要环境变量 {api_key_env}")
    supports_vision = bool(raw.get("supports_vision", False))
    if require_vision and not supports_vision:
        raise ValueError(f"roles.{name}.supports_vision 必须为 true")
    return RoleModelConfig(
        name=name,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        context_tokens=_positive_int(raw.get("context_tokens"), what=f"roles.{name}.context_tokens"),
        max_output_tokens=_positive_int(raw.get("max_output_tokens"), what=f"roles.{name}.max_output_tokens"),
        timeout_s=_positive_float(raw.get("timeout_s", 120.0), what=f"roles.{name}.timeout_s"),
        supports_vision=supports_vision,
        payload=_mapping(raw.get("payload"), what=f"roles.{name}.payload"),
        retry=_mapping(raw.get("retry"), what=f"roles.{name}.retry"),
    )


def load_model_profile(
    path: Path,
    *,
    default_base_url: str = "",
    default_model: str = "",
) -> ModelProfile:
    """读取并校验云端角色配置。任何缺失都抛 ``ValueError``，由启动流程大声失败。"""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"读不了 LLM 配置 {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"LLM 配置 {path} 不是合法 JSON: {error}") from error
    if not isinstance(document, Mapping):
        raise ValueError("LLM 配置必须是 JSON 对象")
    profile = _text(document.get("profile")) or "cloud"
    if profile != "cloud":
        raise ValueError(f"不支持的 LLM profile: {profile}")
    raw_roles = _mapping(document.get("roles"), what="roles")
    if "chat" not in raw_roles:
        raise ValueError("LLM 配置缺少 roles.chat")
    if "vision" not in raw_roles:
        raise ValueError("LLM 配置缺少 roles.vision")
    roles = {
        "chat": _role(
            "chat",
            _mapping(raw_roles["chat"], what="roles.chat"),
            default_base_url=default_base_url,
            default_model=default_model,
            require_vision=False,
        ),
        "vision": _role(
            "vision",
            _mapping(raw_roles["vision"], what="roles.vision"),
            default_base_url=default_base_url,
            default_model=default_model,
            require_vision=True,
        ),
    }
    return ModelProfile(
        profile=profile,
        roles=roles,
        agent=_mapping(document.get("agent"), what="agent"),
        vision=_mapping(document.get("vision"), what="vision"),
        memory=_mapping(document.get("memory"), what="memory"),
    )


__all__ = ["ModelProfile", "RoleModelConfig", "load_model_profile"]
