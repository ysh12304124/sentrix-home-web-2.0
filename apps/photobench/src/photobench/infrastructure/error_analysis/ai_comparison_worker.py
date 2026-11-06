"""Sequential full-record, multimodal comparison with durable partial results."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from photobench.domain.error_analysis.ai_comparison import (
    PAIR_PROMPT,
    PROMPT_VERSION,
    SUMMARY_PROMPT,
    comparison_evidence_ids,
    validate_comparison,
)
from photobench.domain.runs.judge import judge_chat_url, thinking_kwargs


def run_ai_comparison(
    target: Path,
    options: dict[str, Any],
    secret: dict[str, Any],
    check: Callable[[], None],
    progress: Callable[..., None],
    save: Callable[[Path, Any], None],
) -> None:
    result: dict[str, Any] = {
        "schema_version": 1,
        "model": secret["model"],
        "model_role": options.get("model_role", "judge"),
        "prompt_version": PROMPT_VERSION,
        "scope": {
            "kind": options["scope"],
            "indices": options["indices"],
            "filters": options.get("filters", {}),
            "comparison_filter": options.get("comparison_filter", "all"),
            "scope_count": options.get("scope_count", len(options["indices"])),
        },
        "sources": {
            side: json.loads((target / side / "source.json").read_text()) for side in ("left", "right")
        },
        "requested": len(options["pairs"]),
        "cases": [],
        "requests": [],
        "overview": None,
        "summary_status": "not_started",
        "note": (
            "逐题包含全部已保存字段；未保存轨迹不可重建。GT图片为创建分析时数据集字节，"
            "未核实与原run图片哈希一致。AI结论需复核，引用校验只确认来源。"
        ),
    }

    def persist() -> None:
        result["coverage"] = {
            "valid": sum(c.get("valid") is True for c in result["cases"]),
            "failed": sum(c.get("status") in {"failed", "invalid"} for c in result["cases"]),
            "unmatched": sum(c.get("pairing") != "paired" for c in result["cases"]),
            "unprocessed": result["requested"] - len(result["cases"]),
        }
        save(target / "ai-comparison.json", result)
        progress(processed=len(result["cases"]), stage=result.get("stage", "cases"))

    persist()

    def request(
        client: httpx.Client,
        prompt: str,
        payload: dict[str, Any],
        images: list[tuple[str, dict[str, Any]]],
        name: str,
        allowed: set[str],
        paired: bool = False,
    ) -> dict[str, Any]:
        check()
        text = json.dumps(payload, ensure_ascii=False)
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for side, image in images:
            check()
            data = (target / side / image["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != image["sha256"]:
                raise ValueError("frozen GT image checksum mismatch")
            content.extend(
                [
                    {"type": "text", "text": image["id"]},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image['mime']};base64," + base64.b64encode(data).decode()
                        },
                    },
                ]
            )
        body = {
            "model": secret["model"],
            "temperature": 0,
            "max_tokens": 4096,
            **thinking_kwargs(secret["url"]),
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
        }
        save(target / f"{name}-request.json", body)
        entry: dict[str, Any] = {
            "stage": name,
            "status": "failed",
            "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        started = time.monotonic()
        try:
            response = client.post(
                judge_chat_url(secret["url"]),
                headers={"Authorization": "Bearer " + secret["key"]} if secret.get("key") else {},
                json=body,
            )
            entry["http_status"] = response.status_code
            save(
                target / f"{name}-response.json", {"http_status": response.status_code, "body": response.text}
            )
            response.raise_for_status()
            data = response.json()
            entry["usage"] = data.get("usage")
            choice = data["choices"][0]
            raw = choice["message"]["content"]
            value, error = validate_comparison(raw, allowed, paired=paired)
            if choice.get("finish_reason") == "length":
                error = "output_context_limit"
            entry["status"] = "invalid" if error else "completed"
            return {
                "valid": not error,
                "status": entry["status"],
                "result": value,
                "raw": raw,
                "error": error,
            }
        except httpx.HTTPStatusError:
            entry["error"] = f"HTTP {entry['http_status']}"
            # No retries: a quota/context/authentication error stops this job.
            raise ValueError(
                f"对比模型请求失败 HTTP {entry['http_status']}；已保留原请求和响应，未裁剪或重试"
            ) from None
        except httpx.RequestError as exc:
            entry["error"] = type(exc).__name__
            raise ValueError(f"对比模型连接失败：{type(exc).__name__}；未重试") from None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("对比"):
                raise
            return {"valid": False, "status": "invalid", "result": {}, "error": "invalid_model_response"}
        finally:
            entry["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            result["requests"].append(entry)
            if entry["status"] == "failed":
                if name.startswith("summary"):
                    result.update(summary_status="failed", summary_error=entry.get("error", "request_failed"))
                elif result["cases"]:
                    result["cases"][-1]["error"] = entry.get("error", "request_failed")
            persist()

    with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
        if not secret.get("model"):
            raise ValueError("所选主模型未配置模型名称，请先保存连接配置中的模型名称")
        for pair in options["pairs"]:
            check()
            entry = dict(pair, valid=False, status="failed")
            result["cases"].append(entry)
            if pair["pairing"] != "paired":
                entry.update(status="unmatched", error="重复/空题号或另一轮没有此题，未请求模型")
                persist()
                continue
            payload = {
                "left": json.loads((target / "left" / f"input-{pair['index']}.json").read_text()),
                "right": json.loads((target / "right" / f"input-{pair['right_index']}.json").read_text()),
                "sources": result["sources"],
            }
            allowed: set[str] = set()
            images: list[tuple[str, dict[str, Any]]] = []
            for side in ("left", "right"):
                record = payload[side]
                record["evidence_ids"] = comparison_evidence_ids(record, side)
                allowed.update(record["evidence_ids"].values())
                for image in record["gt_images"]:
                    allowed.add(image["id"])
                    if image["status"] == "attached":
                        images.append((side, image))
            save(target / f"pair-{pair['index']}-evidence.json", payload)
            missing = [
                img["id"]
                for side in ("left", "right")
                for img in payload[side]["gt_images"]
                if img["status"] != "attached"
            ]
            if missing:
                entry.update(status="failed", error="GT图片无法完整读取，未请求模型", missing_images=missing)
            else:
                entry.update(
                    request(
                        client, PAIR_PROMPT, payload, images, f"pair-{pair['index']}", allowed, paired=True
                    )
                )
            persist()
        result["stage"] = "summary"
        result["summary_status"] = "running"
        valid = [c for c in result["cases"] if c.get("valid")]
        if not valid:
            result["summary_status"] = "unavailable"
            persist()
            raise ValueError("没有完成引用校验的逐题对比，无法生成总结")
        # Every valid result participates; hierarchical groups bound each request, never sample/crop.
        level = [{"qa_id": c["qa_id"], "result": c["result"]} for c in valid]
        generation = 0
        while True:
            reduced = []
            for offset in range(0, len(level), 8):
                group = level[offset : offset + 8]
                allowed = {
                    r
                    for node in group
                    for section in ("similarities", "divergences", "evaluation")
                    for claim in node["result"][section]
                    for r in claim["evidence_refs"]
                }
                payload = {
                    "coverage": result["coverage"],
                    "requested": result["requested"],
                    "comparisons": group,
                    "allowed_evidence_refs": sorted(allowed),
                    "sources": result["sources"],
                }
                answer = request(
                    client, SUMMARY_PROMPT, payload, [], f"summary-{generation}-{offset // 8}", allowed
                )
                if not answer["valid"]:
                    result.update(summary_status="invalid", summary_error=answer.get("error"))
                    persist()
                    raise ValueError("总结引用校验未通过，逐题结果已保留")
                reduced.append({"result": answer["result"]})
            if len(reduced) == 1:
                result.update(overview=reduced[0]["result"], summary_status="completed", stage="completed")
                persist()
                break
            level = reduced
            generation += 1
