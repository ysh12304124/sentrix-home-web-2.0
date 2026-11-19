"""语义观察：把一张已导入的 Asset 交给视觉观察器，把结果写回 Observation。

1.0 把这件事拆成两段提交：``_commit_fast_image``（导入即落盘 + 向量）与
``_commit_semantic_image``（VLM 语义 + 事件 + 事实 + 词法）。2.0 保留同一个分界，
只是把语义那一段做成可选的协作者：单张上传走快路径，整册导入（``ImportAlbum``）把它接上。

**观察失败不是导入失败。** 模型不可用、图片读不了、返回的不是 JSON——``VlmObserver``
返回 ``None``，这里就当这次没观察到，Observation 保持空语义。1.0 在
``_commit_semantic_image`` 里也是这么收尾的：异常吞掉，资产留在 ``processed``。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sentrix.domain.media.models import Asset, MediaType, Observation
from sentrix.domain.media.ports import (
    AssetObserver,
    MediaStorage,
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
    ) -> None:
        self._observer = observer
        self._observations = observations
        self._storage = storage

    def prepare(self, asset: Asset) -> ObservationSemantics | None:
        """读取文本原件或准备视觉语义，供串行提交阶段写库。"""
        if asset.media_type is MediaType.TEXT and self._storage is not None:
            try:
                text = self._storage.path_for(asset).read_bytes().decode("utf-8")
            except (OSError, UnicodeError):
                log.warning("文本原件无法读取: asset=%s", asset.id)
                return None
            if text.strip() and "\x00" not in text:
                return ObservationSemantics(source_text=text, confidence=1.0)
            return None
        if asset.media_type is not MediaType.IMAGE or self._observer is None:
            return None
        return self._observer.observe(Path(asset.path))

    def commit(self, observation: Observation, semantics: ObservationSemantics | None) -> Observation | None:
        """第二段：写回。``semantics`` 为空就什么都不写。"""
        if semantics is None:
            return None
        return self._observations.enrich(observation.id, semantics=semantics)

    def observe(self, asset: Asset, observation: Observation) -> Observation | None:
        """两段的复合。**行为与拆分前逐字一致。"""
        return self.commit(observation, self.prepare(asset))
