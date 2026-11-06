"""``/api/config`` 与 judge 提示词的读写（1.0 ``do_GET`` ``:7536`` / ``:7719``、
``do_POST`` ``:7775`` / ``:7785`` / ``:7822``）。

S9b-1 的 ``GET /api/config`` 响应体比 1.0 少三项（``judge_prompt`` /
``custom_judge_prompt`` / ``evidence_judge_enabled``）；S9b-3 补齐，并把 judge 提示词的
三条读写路由接上——它们是 judge 域在 HTTP 层唯一的面。

**``judge_api_key`` 的写入也接上了**：它写 ``.env.local``（``ConfigService``），不再是
S9b-1 那条「尚未接线」的 400。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from photobench.api.deps import get_container, json_body
from photobench.api.metrics_schema import RuntimeConfigResponse
from photobench.domain.errors import InvalidRequestError
from photobench.domain.evaluation.profiles import PROFILE_PROMPT_KINDS as JUDGE_PROMPT_KINDS

router = APIRouter(tags=["config"])

#: 1.0 ``:7786`` / ``:7791`` 的两条长度校验。50000 是给「整段替换提示词」留的余量：
#: 默认提示词本身约 3.5 KB，而判分器每道题都要把它发一次。
MAX_JUDGE_PROMPT_LENGTH = 50000


@router.get("/config", response_model=RuntimeConfigResponse, response_model_exclude_unset=True)
def get_config(request: Request) -> dict[str, Any]:
    """1.0 ``:7536``。"""
    container = get_container(request)
    return {
        **container.config.public_config(),
        "build_revision": container.build_revision,
        "metrics_contract_version": 2,
    }


@router.post("/config", response_model=RuntimeConfigResponse, response_model_exclude_unset=True)
async def save_config(request: Request) -> dict[str, Any]:
    """1.0 ``:7822``：落非密钥的连接参数；带 ``judge_api_key`` 时同时写 ``.env.local``。

    成功时的响应体是 1.0 的 ``{"saved": True, "runtime_config": ...}``。
    """
    payload = await json_body(request)
    saved = await run_in_threadpool(get_container(request).config.persist_runtime_connection, payload)
    return {"saved": True, "runtime_config": saved}


@router.get("/judge-prompts")
def list_judge_prompts(request: Request) -> dict[str, Any]:
    """1.0 ``:7719``：三类提示词的默认值、当前自定义值与存储路径。"""
    return get_container(request).config.public_judge_prompts()


@router.post("/judge-prompt")
async def save_answer_quality_prompt(request: Request) -> dict[str, Any]:
    """1.0 ``:7775``：旧版单提示词入口，只改 ``answer_quality``。"""
    payload = await json_body(request)
    prompt = str(payload.get("system_prompt") or "").strip()
    if not prompt:
        raise InvalidRequestError("system_prompt is required")
    if len(prompt) > MAX_JUDGE_PROMPT_LENGTH:
        raise InvalidRequestError("system_prompt is too long")
    await run_in_threadpool(get_container(request).config.save_custom_judge_prompt, prompt)
    return {"status": "ok"}


@router.post("/judge-prompts")
async def save_judge_prompt(request: Request) -> dict[str, Any]:
    """1.0 ``:7785``：按 ``kind`` 改一类提示词。

    **这里不接受空串**（1.0 的文案：「empty string to restore default is not allowed
    here」）——恢复默认走 ``DELETE`` 语义上的空值写入，而这条路由是「保存我改的这一版」。
    """
    payload = await json_body(request)
    kind = str(payload.get("kind") or "").strip()
    prompt = str(payload.get("system_prompt") or "").strip()
    if kind not in JUDGE_PROMPT_KINDS:
        raise InvalidRequestError(f"kind must be one of {sorted(JUDGE_PROMPT_KINDS)}")
    if not prompt:
        raise InvalidRequestError(
            "system_prompt is required (empty string to restore default is not allowed here)"
        )
    if len(prompt) > MAX_JUDGE_PROMPT_LENGTH:
        raise InvalidRequestError("system_prompt is too long")
    config = get_container(request).config
    await run_in_threadpool(config.save_custom_judge_prompt, prompt, kind)
    return {
        "status": "ok",
        "kind": kind,
        "custom": (await run_in_threadpool(config.load_custom_judge_prompts)).get(kind),
    }
