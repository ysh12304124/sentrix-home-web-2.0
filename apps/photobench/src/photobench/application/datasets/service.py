"""数据集展示层用例（1.0 ``do_GET`` 的 ``/api/manifests*`` / ``/api/qa-dataset`` /
``/api/albums/*``，``:7685-7723``）。

**错误文案逐字照搬 1.0**，因为前端按它分支：``album_id and qa_set are required``（400）、
``album not found`` / ``qa_set not found`` / ``qa file not found`` / ``not found`` /
``media not found``（404）。1.0 用 ``{"error": ...}`` 回，这一层抛领域错误，
``api/errors.py`` 负责映射成同一个形状。

**目录遍历与文件读取走 ``AlbumSource`` 端口**，不在这一层拼路径——那是
infrastructure 的事，而且穿越拦截已经在那里（``JsonAlbumSource._album_dir``）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from photobench.application.config.service import ConfigService, normalize_service_url
from photobench.domain.datasets.catalog import (
    manifest_summary,
    parse_jsonl,
    qa_set_file,
)
from photobench.domain.datasets.ports import AlbumSource
from photobench.domain.datasets.qa_media import (
    qa_media_refs,
    qa_scope_id_for_album,
    resolve_qa_rows,
)
from photobench.domain.datasets.qa_taxonomy import validate_qa_annotations
from photobench.domain.errors import InvalidRequestError, NotFoundError
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.sentrix.ports import API_PREFIX


class DatasetCatalog:
    """读侧用例。依赖 ``AlbumSource`` / ``JsonHttpClient`` / ``ConfigService`` 三个注入件。"""

    def __init__(self, albums: AlbumSource, http: JsonHttpClient, config: ConfigService) -> None:
        self._albums = albums
        self._http = http
        self._config = config

    # -- manifest ------------------------------------------------------------

    def list_manifests(self) -> dict[str, Any]:
        """1.0 ``OrchestratorRepository.list_manifests``（``:5144``）。

        逐目录读，**一条坏的只跳过它自己**：缺 ``album_id`` 的 manifest 会让
        ``manifest_summary`` 抛 ``KeyError``，那条目录就不进列表。1.0 的
        ``except (OSError, KeyError, json.JSONDecodeError)`` 里另外两种已经由适配器
        归成 ``KeyError``（``JsonAlbumSource.manifest``），所以这里只接 ``KeyError``
        ——**多接一族会把「``faces`` 是 null」这类坏数据从报错变成静默跳过。**
        """
        manifests: list[dict[str, Any]] = []
        for album_id in self._albums.list_album_ids():
            try:
                manifests.append(manifest_summary(self._albums.manifest(album_id)))
            except KeyError:
                continue
        return {"manifests": manifests}

    def get_manifest(self, album_id: str) -> dict[str, Any]:
        """1.0 ``:7678-7685`` + ``get_manifest``（``:5163``）：读不到或空 manifest → 404。"""
        try:
            manifest = self._albums.manifest(album_id)
        except KeyError as exc:
            raise NotFoundError("not found") from exc
        # 1.0 的判据是 `if mf:`——一个空的 manifest 也走 404，所以这里是 `not`。
        if not manifest:
            raise NotFoundError("not found")
        return manifest

    # -- QA 数据集 ------------------------------------------------------------

    def qa_dataset(self, album_id: str, qa_set: str, sentrix_url: str = "") -> dict[str, Any]:
        """1.0 ``:7686-7712``。顺序与文案照搬：参数 → 相册 → qa_set → 文件 → 媒体解析。"""
        if not album_id or not qa_set:
            raise InvalidRequestError("album_id and qa_set are required")
        try:
            manifest = self._albums.manifest(album_id)
        except KeyError as exc:
            raise NotFoundError("album not found") from exc
        relative = qa_set_file(manifest, qa_set)
        if not relative:
            raise NotFoundError("qa_set not found")
        try:
            raw = self._albums.read_file(album_id, relative)
        except KeyError as exc:
            raise NotFoundError("qa file not found") from exc
        # 1.0 这里用的是**裸** load_jsonl，不带 qa_set 级 expected_action 默认值
        # （那是 run 主链的 BenchmarkRun.__init__ 才做的事）。所以不能走
        # AlbumSource.qa_rows——它会多补一个字段，响应形状就变了。
        rows = parse_jsonl(raw.decode("utf-8"))
        definition = (manifest.get("qa_set_versions") or {}).get(qa_set) or {}
        if definition.get("schema_version") == "2.0":
            for row in rows:
                validate_qa_annotations(row)
        rows, media_resolution = self._resolve_qa_media(sentrix_url, album_id, rows)
        return {
            "album_id": album_id,
            "qa_set": qa_set,
            "items": rows,
            "qa_version": definition,
            "media_resolution": media_resolution,
            "media_source": "sentrix",
        }

    def _resolve_qa_media(
        self, sentrix_url: str, album_id: str, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """1.0 ``_resolve_qa_media_rows``（``:1423``）里用 ``request_json`` 的那一层。

        三个提前返回的形状（``no_media`` / ``sentrix_url_missing`` /
        ``sentrix_unavailable``）与 ``resolved`` 那一条**逐字段照搬**——前端按
        ``status`` 决定显示「没有媒体」还是「Sentrix 连不上」，少一个字段它就会
        把一种情况显示成另一种。

        **两处 2.0 的接口差异在这里吸收**（都核过 apps/api）：
        ``/api/memory-spaces`` 与 ``/api/assets`` 的前缀是 ``/api/v1``（1.0 是 ``/api``）；
        资产表的信封是 ``{"items": [...]}``（1.0 是 ``{"assets": [...]}``，
        ``api/schemas/media.py``）。``/assets`` 的上限两边都是 1000。
        """
        refs = qa_media_refs(rows)
        if not refs:
            return rows, {
                "status": "no_media",
                "resolved_count": 0,
                "missing_count": 0,
                "ambiguous_count": 0,
            }
        base = normalize_service_url(sentrix_url or self._config.sentrix_url)
        if not base:
            return rows, {
                "status": "sentrix_url_missing",
                "resolved_count": 0,
                "missing_count": len(refs),
                "ambiguous_count": 0,
            }
        try:
            spaces_data = self._http.request_json(f"{base}{API_PREFIX}/memory-spaces?limit=1000", timeout=20)
            spaces = spaces_data.get("spaces") if isinstance(spaces_data, dict) else spaces_data
            scope_id = qa_scope_id_for_album(spaces or [], album_id)
            query = f"{base}{API_PREFIX}/assets?limit=1000"
            if scope_id:
                query += f"&scope_id={quote(scope_id)}"
            assets_data = self._http.request_json(query, timeout=30)
            # 2.0 的信封是 items；1.0 是 assets。这里只认 2.0 的，因为地址前缀
            # 已经按 2.0 拼了——泛化两种信封只会在「参数指向 1.0 后端」时把
            # 「路径打不中」这个更清楚的现象伪装成「资产表是空的」。
            assets = assets_data.get("items") if isinstance(assets_data, dict) else assets_data
        except Exception as exc:  # noqa: BLE001 —— 1.0 如此：连不上是状态，不是 500
            return rows, {
                "status": "sentrix_unavailable",
                "error": str(exc),
                "resolved_count": 0,
                "missing_count": len(refs),
                "ambiguous_count": 0,
            }
        enriched, counts = resolve_qa_rows(rows, refs, assets or [], f"{base}{API_PREFIX}/assets")
        return enriched, {
            "status": "resolved",
            "scope_id": scope_id,
            **counts,
            "total_refs": len(refs),
        }

    # -- 相册媒体 --------------------------------------------------------------

    def album_media_file(self, album_id: str, collection: str, file_name: str) -> str | None:
        """1.0 ``_resolve_album_media_file``（``:1188``）：交付不了返回 ``None`` → 404。

        ``collection`` 的合法值由路由先判（1.0 也在路由里判），适配器再判一次——
        它是真正的文件系统边界，多一道不是重复。
        """
        return self._albums.media_file(album_id, collection, file_name)
