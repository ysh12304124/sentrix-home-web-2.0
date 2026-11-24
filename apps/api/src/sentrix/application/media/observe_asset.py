"""语义观察：把一张已导入的 Asset 交给视觉观察器，把结果写回 Observation。

1.0 把这件事拆成两段提交：``_commit_fast_image``（导入即落盘 + 向量）与
``_commit_semantic_image``（VLM 语义 + 事件 + 事实 + 词法）。2.0 保留同一个分界，
只是把语义那一段做成可选的协作者：单张上传走快路径，整册导入（``ImportAlbum``）把它接上。

**观察失败不是导入失败。** 模型不可用、图片读不了、返回的不是 JSON——``VlmObserver``
返回 ``None``，这里就当这次没观察到，Observation 保持空语义。1.0 在
``_commit_semantic_image`` 里也是这么收尾的：异常吞掉，资产留在 ``processed``。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sentrix.domain.media.models import (
    TEXT_IMPORT_SOURCE,
    Asset,
    MediaType,
    MemoryEvidence,
    Observation,
    ObservationBuild,
)
from sentrix.domain.media.ports import (
    AssetObserver,
    MediaStorage,
    MemoryBuildRepository,
    MemoryEvidenceRepository,
    ObservationRepository,
    ObservationSemantics,
    VlmObserver,
)

log = logging.getLogger(__name__)


class ObserveAsset(AssetObserver):
    def __init__(
        self,
        *,
        observer: VlmObserver | None,
        observations: ObservationRepository,
        storage: MediaStorage | None = None,
        builds: MemoryBuildRepository | None = None,
        evidence: MemoryEvidenceRepository | None = None,
    ) -> None:
        self._observer = observer
        self._observations = observations
        self._storage = storage
        self._builds = builds
        self._evidence = evidence

    def prepare(self, asset: Asset) -> ObservationSemantics | None:
        """读取文本原件或准备视觉语义，供串行提交阶段写库。"""
        return self.prepare_with_faces(asset, None)

    def prepare_with_faces(
        self, asset: Asset, faces: Sequence[object] | None
    ) -> ObservationSemantics | None:
        """带检测项的观察；faces 为 None 时保持旧行为。"""
        if asset.media_type is MediaType.TEXT and self._storage is not None:
            try:
                text = self._storage.path_for(asset).read_bytes().decode("utf-8")
            except (OSError, UnicodeError):
                log.warning("文本原件无法读取: asset=%s", asset.id)
                return None
            if text.strip() and "\x00" not in text:
                return ObservationSemantics(
                    source_text=text,
                    caption="\n".join(
                        part for part in (asset.source_metadata.title, asset.source_metadata.summary) if part
                    ),
                    confidence=1.0,
                )
            return None
        if asset.media_type is not MediaType.IMAGE or self._observer is None:
            return None
        observe_with_faces = getattr(self._observer, "observe_with_faces", None)
        if faces is not None and callable(observe_with_faces):
            return cast(ObservationSemantics | None, observe_with_faces(Path(asset.path), faces))
        return self._observer.observe(Path(asset.path))

    def commit(self, observation: Observation, semantics: ObservationSemantics | None) -> Observation | None:
        """第二段：写回。``semantics`` 为空就什么都不写。"""
        if semantics is None:
            return None
        observed = semantics
        if self._builds is not None and observation.source_type != TEXT_IMPORT_SOURCE:
            build = self._record_build(observation, semantics)
            if self._evidence is not None:
                self._record_evidence(observation, semantics, build)
            observed = replace(semantics, active_build_id=build.id)
        return self._observations.enrich(observation.id, semantics=observed)

    def _record_build(self, observation: Observation, semantics: ObservationSemantics) -> ObservationBuild:
        assert self._observer is not None
        assert self._builds is not None
        payload = json.dumps(dataclasses.asdict(semantics), ensure_ascii=False, sort_keys=True)
        input_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]
        try:
            model_name = self._observer.model_id
        except Exception:  # noqa: BLE001 —— 模型发现失败不能阻断已成功的观察
            model_name = ""
        prompt_hash = getattr(self._observer, "prompt_hash", "")
        protocol = getattr(self._observer, "protocol_version", "image-observation-v1")
        now = datetime.now(UTC)
        build, _ = self._builds.create_or_get(
            ObservationBuild(
                id="",
                scope_id=observation.scope_id,
                asset_id=observation.asset_id,
                observation_id=observation.id,
                observation_revision=observation.revision + 1,
                stage="observation",
                status="succeeded",
                build_key=(
                    f"{observation.scope_id}:{observation.asset_id}:observation:"
                    f"{input_hash}:{model_name}:{prompt_hash}"
                ),
                input_hash=input_hash,
                model_name=model_name,
                protocol_version=protocol,
                prompt_hash=prompt_hash,
                params={
                    "max_side": 1280,
                    "field_states": dict(semantics.field_states),
                },
                input_transform={"exif_transpose": True, "max_side": 1280},
                started_at=now,
                finished_at=now,
            )
        )
        return build

    def _record_evidence(
        self,
        observation: Observation,
        semantics: ObservationSemantics,
        build: ObservationBuild,
    ) -> None:
        assert self._evidence is not None
        assert self._observer is not None
        fields = (
            ("caption", semantics.caption),
            ("short_caption", semantics.short_caption),
            ("detail_caption", semantics.detail_caption),
            ("ocr_text", semantics.ocr_text),
            ("tags", list(semantics.tags)),
            ("entities", [dataclasses.asdict(item) for item in semantics.entities]),
        )
        for name, value in fields:
            if value in (None, "", [], ()):
                continue
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            self._evidence.add(
                MemoryEvidence(
                    id="",
                    scope_id=observation.scope_id,
                    owner_kind="observation",
                    owner_id=observation.id,
                    field_path=f"observations.{name}",
                    role="single_image_memory",
                    value=value,
                    value_hash=hashlib.sha256(encoded.encode()).hexdigest()[:16],
                    source_kind="model",
                    source_modality="image",
                    source_channel="vlm_observation",
                    locator={
                        "build_id": build.id,
                        "model_name": build.model_name,
                        "prompt_hash": build.prompt_hash,
                    },
                    observation_revision=observation.revision + 1,
                    build_id=build.id,
                    status="effective",
                )
            )

    def observe(self, asset: Asset, observation: Observation) -> Observation | None:
        """两段的复合。**行为与拆分前逐字一致。"""
        return self.commit(observation, self.prepare(asset))
