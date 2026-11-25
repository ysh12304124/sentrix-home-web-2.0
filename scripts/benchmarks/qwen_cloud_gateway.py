"""Loopback Qwen adapter for fixed-budget, nonthinking benchmark runs.

Credentials live in QWEN_EVAL_CONFIG; request metadata lives in QWEN_EVAL_LOG.
The adapter preserves messages, tools, generation budgets and response bodies.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def create_app() -> FastAPI:
    config = json.loads(Path(os.environ["QWEN_EVAL_CONFIG"]).read_text())
    model = str(config["model"])
    upstream = str(config["url"]).rstrip("/") + "/chat/completions"
    api_key = str(config["api_key"])
    if not api_key or not upstream.startswith("https://"):
        raise ValueError("HTTPS provider and credential are required")
    log = Path(os.environ["QWEN_EVAL_LOG"])
    semaphore = asyncio.Semaphore(4)
    inflight = 0
    active = 0

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15), trust_env=False) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model": model,
            "active_requests": active,
            "inflight_requests": inflight,
            "enable_thinking": False,
            "credential_configured": True,
            "upstream_reachability": "actual requests only",
        }

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "owned_by": "aliyun",
                    "application_context_budget_tokens": 16384,
                }
            ],
        }

    @app.get("/props")
    def props() -> dict[str, Any]:
        return {
            "model_alias": model,
            "source": "cloud_api",
            "total_slots": 4,
            "capacity_source": "adapter_semaphore",
            "local_gpu": False,
            "default_generation_settings": {"n_ctx": 16384},
            "context_source": "application_budget",
        }

    @app.post("/v1/chat/completions")
    async def complete(request: Request) -> Response:
        nonlocal active, inflight
        body = await request.json()
        if body.get("model") != model or body.get("stream"):
            return JSONResponse(
                {"error": {"message": "model mismatch or streaming requested"}}, status_code=400
            )
        body.pop("chat_template_kwargs", None)
        body.pop("thinking", None)
        body["enable_thinking"] = False
        started = time.time()
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "started_at": started,
            "requested_model": model,
            "enable_thinking": False,
            "max_tokens": body.get("max_tokens"),
            "has_tools": bool(body.get("tools")),
            "has_image": any(
                isinstance(m.get("content"), list)
                and any(c.get("type") == "image_url" for c in m["content"] if isinstance(c, dict))
                for m in body.get("messages", [])
            ),
        }
        inflight += 1
        try:
            async with semaphore:
                record["queue_seconds"] = time.time() - started
                active += 1
                try:
                    response = await app.state.client.post(
                        upstream, json=body, headers={"Authorization": "Bearer " + api_key}
                    )
                finally:
                    active -= 1
            record["http_status"] = response.status_code
            try:
                result = response.json()
                choices = result.get("choices", [])
                record.update(
                    actual_model=result.get("model"),
                    response_id=result.get("id"),
                    usage=result.get("usage"),
                    finish_reasons=[c.get("finish_reason") for c in choices],
                    has_reasoning_content=any(
                        bool(c.get("message", {}).get("reasoning_content")) for c in choices
                    ),
                )
            except ValueError:
                record["invalid_json"] = True
            return Response(response.content, status_code=response.status_code, media_type="application/json")
        except httpx.HTTPError as error:
            record["error_type"] = type(error).__name__
            return JSONResponse(
                {"error": {"message": "cloud transport error", "type": type(error).__name__}}, status_code=502
            )
        finally:
            inflight -= 1
            record["elapsed_seconds"] = time.time() - started
            with log.open("a") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")

    return app
