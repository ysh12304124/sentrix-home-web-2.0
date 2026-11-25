"""Resolve the same model selection for a retest and an interactive conversation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from photobench.application.config.service import (
    ConfigService,
    normalize_model_base_url,
    normalize_service_url,
)
from photobench.application.run_execution.service import (
    DEFAULT_TURN_TIMEOUT,
    REASONING_TURN_TIMEOUT,
    RunRequest,
    freeze_runtime_request,
)
from photobench.domain.errors import InvalidRequestError


class InteractiveModels:
    def __init__(
        self,
        config: ConfigService,
        revision: str,
        budgets: Mapping[str, Any],
        diagnostics: Callable[[], dict[str, Any]],
    ) -> None:
        self.config, self.revision, self.budgets = config, revision, budgets
        self.diagnostics = diagnostics

    def turn_timeout(self, reasoning: bool) -> int:
        return max(
            int(self.budgets.get("turn_timeout") or DEFAULT_TURN_TIMEOUT),
            REASONING_TURN_TIMEOUT if reasoning else DEFAULT_TURN_TIMEOUT,
        )

    def _deployed_endpoint(
        self, connection: Mapping[str, Any], effective: Mapping[str, Any]
    ) -> tuple[str, str]:
        base = str(connection.get("model_base_url") or self.config.model_base_url)
        endpoint_model = str(connection.get("endpoint_model") or "")
        actual = str(effective.get("vlm_base_url") or "")
        if actual:
            if actual.rstrip("/") != base.rstrip("/"):
                endpoint_model = ""
            base = actual
        return base, endpoint_model

    def deployed_endpoint(self) -> tuple[str, str]:
        """Read the endpoint used by an empty interactive model selection."""
        observed = self.diagnostics()
        return self._deployed_endpoint(
            self.config.public_runtime_connection_config(), observed.get("runtime_configuration") or {}
        )

    def resolve(self, payload: Mapping[str, Any], **inputs: Any) -> RunRequest:
        reasoning = payload.get("reasoning", True)
        if not isinstance(reasoning, bool):
            raise InvalidRequestError("reasoning must be a boolean")
        selection = payload.get("model") or {}
        if not isinstance(selection, dict):
            raise InvalidRequestError("model must be an object")
        connection = self.config.public_runtime_connection_config()
        observed = self.diagnostics()
        effective = observed.get("runtime_configuration") or {}
        if observed.get("build_revision"):
            connection["product_build_revision"] = observed["build_revision"]
        connection["product_settings"] = {
            key: effective[key]
            for key in (connection.get("product_settings") or self.budgets.get("product_settings") or {})
            if key in effective
        }
        profile = str(selection.get("profile") or "__current__")
        manager = ""
        base = str(connection.get("model_base_url") or self.config.model_base_url)
        endpoint_model = str(connection.get("endpoint_model") or "")
        if not selection:
            base, endpoint_model = self._deployed_endpoint(connection, effective)
        if selection.get("target_id"):
            _, target = self.config.resolve_vllm_target(str(selection["target_id"]))
            base = str(target.get("model_base_url") or "")
            manager = str(target.get("manager_url") or "")
            endpoint_model = str(selection.get("endpoint_model") or "")
        if selection.get("base_url"):
            base = normalize_model_base_url(str(selection["base_url"]))
            manager = normalize_service_url(str(selection.get("manager_url") or ""))
            endpoint_model = str(selection.get("endpoint_model") or "")
        if profile != "__current__" and not manager:
            manager = str(connection.get("vllm_manager_url") or "")
        if profile != "__current__" and not manager:
            raise InvalidRequestError("所选模型需要配置模型 Manager 地址")
        if not base:
            raise InvalidRequestError("请先配置模型服务地址")
        if profile != "__current__":
            endpoint_model = ""
        connection.update(model_base_url=base, endpoint_model=endpoint_model)
        request = RunRequest(
            **inputs,
            sentrix_url=self.config.sentrix_url,
            model_profile=profile,
            model_base_url=base,
            manager_url=manager,
            endpoint_model=endpoint_model,
            switch_model=True,
            qa_concurrency=1,
            reasoning=reasoning,
            turn_timeout=self.turn_timeout(reasoning),
        )
        return freeze_runtime_request(request, connection, self.revision)
