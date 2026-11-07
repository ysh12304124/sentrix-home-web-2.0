"""Analysis orchestration and explicit provider selection; facts stay immutable."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from photobench.application.config.service import ConfigService
from photobench.domain.error_analysis.ports import AnalysisBackend

log = logging.getLogger(__name__)


class ErrorAnalysis:
    def __init__(self, backend: AnalysisBackend, config: ConfigService) -> None:
        self.backend = backend
        self.config = config

    def auto(self, run_id: str) -> None:
        try:
            self.backend.submit(run_id, "rules", {})
        except Exception:
            log.exception("Automatic analysis enqueue failed: %s", run_id)  # noqa: BLE001

    def create(self, run_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "rules")
        options = {
            k: payload[k]
            for k in (
                "parent_job",
                "left_job",
                "right_job",
                "indices",
                "scope",
                "mode",
                "filters",
                "resume_job",
                "summary_only",
                "model_role",
                "comparison_filter",
                "scoring_profile",
                "scoring_batch",
            )
            if k in payload
        }
        if kind in {"ai", "ai_compare"} and payload.get("model_role"):
            role = str(payload["model_role"])
            if role == "judge":
                secret = {
                    "provider_id": "judge",
                    "url": self.config.judge_url,
                    "model": self.config.judge_model,
                    "key": self.config.judge_api_key,
                }
            elif role == "main":
                secret = {
                    "provider_id": "main",
                    "url": self.config.model_base_url,
                    "model": self.config.public_runtime_connection_config().get("endpoint_model", ""),
                    "key": "",
                }
            else:
                raise ValueError("model_role must be judge or main")
            if not secret["url"]:
                raise ValueError("所选分析模型未配置连接地址")
            options["secret"] = secret
        elif kind in {"ai", "ai_compare"}:
            provider = str(payload.get("provider_id") or "")
            if not provider or provider not in self.config.judge_providers:
                raise ValueError("select an analysis provider explicitly")
            pid, url, model, key = self.config.resolve_judge_provider(provider)
            options["secret"] = {"provider_id": pid, "url": url, "model": model, "key": key}
        return self.backend.submit(run_id, kind, options)
