from __future__ import annotations

import json
from pathlib import Path

from photobench.application.config.service import ConfigService
from photobench.infrastructure.config.files import JsonConfigStore


class _Secrets:
    def read_all(self) -> dict[str, str]:
        return {}

    def read(self, name: str) -> str | None:
        return None

    def write(self, name: str, value: str) -> None:
        return None


def test_save_preserves_service_topology_in_runtime_connection(tmp_path: Path) -> None:
    (tmp_path / "judge_providers.json").write_text(
        json.dumps(
            {
                "default_provider_id": "cloud",
                "providers": {"cloud": {"url": "https://judge.example/v1", "model": "judge"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "vllm_targets.json").write_text(
        json.dumps(
            {
                "default_target_id": "local",
                "targets": {"local": {"model_base_url": "http://127.0.0.1:8100/v1"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime_connection.json").write_text(
        json.dumps(
            {
                "model_base_url": "http://127.0.0.1:8100/v1",
                "endpoint_model": "old",
                "product_build_revision": "product-release",
                "embedding": {"mode": "split_text_image", "text_url": "http://127.0.0.1:8101"},
                "product": [
                    {"id": "sentrix_api", "ports": [8191]},
                    {"id": "photobench", "ports": [8872]},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ConfigService(JsonConfigStore(tmp_path), _Secrets())
    service.persist_runtime_connection(
        {
            "sentrix_url": "http://127.0.0.1:8291",
            "judge_url": "https://judge.example/v1",
            "judge_model": "judge",
            "model_base_url": "http://127.0.0.1:8103/v1",
            "endpoint_model": "qwen35-mixed-3p1",
            "judge_provider_id": "cloud",
        }
    )
    saved = json.loads((tmp_path / "runtime_connection.json").read_text(encoding="utf-8"))
    assert saved["model_base_url"] == "http://127.0.0.1:8103/v1"
    assert saved["endpoint_model"] == "qwen35-mixed-3p1"
    assert saved["product_build_revision"] == "product-release"
    assert service.public_runtime_connection_config()["product_build_revision"] == "product-release"
    assert saved["embedding"]["text_url"] == "http://127.0.0.1:8101"
    assert {"id": "sentrix_api", "ports": [8291]} in saved["product"]
    assert {"id": "photobench", "ports": [8872]} in saved["product"]


def test_embedding_ports_come_from_urls_not_stale_profiles() -> None:
    from photobench.infrastructure.runtime.telemetry_scope import _from_runtime_connection

    scope = _from_runtime_connection(
        {
            "model_base_url": "http://127.0.0.1:8100/v1",
            "endpoint_model": "qwen35-q4km",
            "embedding": {
                "mode": "split_text_image",
                "local": True,
                "text_url": "http://127.0.0.1:8101",
                "image_url": "http://127.0.0.1:8103",
                "profiles": {"split_text_image": {"local": True, "ports": [18101, 18103]}},
            },
            "product": [{"id": "photobench", "ports": [8872]}],
        }
    )
    assert scope is not None
    assert scope["embedding"]["profiles"]["split_text_image"]["ports"] == [8101, 8103]


def test_main_model_ports_do_not_match_embedding_llama_server() -> None:
    from photobench.infrastructure.runtime.telemetry_scope import _from_runtime_connection, matches_service

    scope = _from_runtime_connection(
        {
            "model_base_url": "http://127.0.0.1:8100/v1",
            "endpoint_model": "qwen35-q4km",
            "embedding": {"mode": "split_text_image", "local": True},
        }
    )
    assert scope is not None
    bge_row = {"pid": 1, "identity": "llama-server --embedding", "listening_ports": ["8101"]}
    main_row = {"pid": 2, "identity": "llama-server", "listening_ports": ["8100"]}
    assert matches_service(main_row, scope["main_model"])
    assert not matches_service(bge_row, scope["main_model"])


def test_unregistered_api_uses_diagnostics_and_refuses_process_restart() -> None:
    from fakes_execution import FakeHttpClient

    from photobench.infrastructure.deploy.sentrix_process import ObservedSentrixProcess

    http = FakeHttpClient(
        [
            {"runtime_configuration": {"vlm_base_url": "http://other-host:8100/v1"}},
        ]
    )
    observed = ObservedSentrixProcess(api_url="http://other-host:8291", http=http)
    assert observed.running_endpoint() == "http://other-host:8100/v1"
    outcome = observed.restart(endpoint="http://other-host:8101/v1")
    assert not outcome.ok and not outcome.restarted
    assert outcome.reason == "unregistered_product_service"
