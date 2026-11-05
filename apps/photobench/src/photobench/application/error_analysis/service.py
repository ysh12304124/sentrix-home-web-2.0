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
                "max_cases",
                "resume_job",
                "summary_only",
            )
            if k in payload
        }
        if kind == "ai":
            provider = str(payload.get("provider_id") or "")
            if not provider or provider not in self.config.judge_providers:
                raise ValueError("select an analysis provider explicitly")
            pid, url, model, key = self.config.resolve_judge_provider(provider)
            options["secret"] = {"provider_id": pid, "url": url, "model": model, "key": key}
        return self.backend.submit(run_id, kind, options)
