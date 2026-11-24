"""图片构建状态的只读盘点与详情。

S0 只回答「这张图现在处于什么状态」：没有构建记录但有旧语义的是 legacy_partial；
有构建但阶段失败/未完成的是 v3_partial；视觉阶段成功且没有失败阶段的是 v3_complete。
不调用任何模型，也不把旧 confidence 当完整度。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.media.models import (
    Asset,
    MediaType,
    MemoryEvidence,
    Observation,
    ObservationBuild,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    MemoryBuildRepository,
    MemoryBuildTaskRepository,
    MemoryEvidenceRepository,
    ObservationRepository,
)


@dataclass(frozen=True, slots=True)
class MemoryBuildAssetState:
    asset_id: str
    file_name: str
    media_kind: str
    derived_kind: str
    parent_asset_id: str
    observation_id: str | None
    observation_revision: int
    available_fields: tuple[str, ...]
    build_states: tuple[tuple[str, str], ...]
    status: str
    pending_tasks: int


@dataclass(frozen=True, slots=True)
class MemoryBuildInventory:
    scope_id: str
    counts: Mapping[str, int]
    assets: tuple[MemoryBuildAssetState, ...]


@dataclass(frozen=True, slots=True)
class MemoryBuildDetail:
    build: ObservationBuild
    evidence: tuple[MemoryEvidence, ...]


class ManageMemoryBuilds:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        builds: MemoryBuildRepository,
        evidence: MemoryEvidenceRepository,
        tasks: MemoryBuildTaskRepository,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._builds = builds
        self._evidence = evidence
        self._tasks = tasks

    def inventory(self, scope_id: str, limit: int = 200) -> MemoryBuildInventory:
        assets = [
            asset
            for asset in self._assets.list_by_scope(scope_id, limit)
            if asset.media_type is MediaType.IMAGE
        ]
        states = tuple(self._state(asset) for asset in assets)
        counts = Counter(state.status for state in states)
        counts["total"] = len(states)
        counts["image"] = sum(1 for state in states if state.media_kind == "image")
        counts["video_keyframe"] = sum(1 for state in states if state.media_kind == "video_keyframe")
        counts["unknown_derived"] = sum(1 for state in states if state.media_kind == "unknown_derived")
        return MemoryBuildInventory(scope_id=scope_id, counts=dict(counts), assets=states)

    def get(self, build_id: str) -> MemoryBuildDetail | None:
        build = self._builds.get(build_id)
        if build is None:
            return None
        return MemoryBuildDetail(build=build, evidence=tuple(self._evidence.list_by_build(build_id)))

    def _state(self, asset: Asset) -> MemoryBuildAssetState:
        media_kind, derived_kind, parent_id = self._media_kind(asset)
        observation = self._latest_observation(asset.id)
        available = self._available_fields(observation)
        builds = self._builds.list_for_asset(asset.id)
        pending = self._tasks.list_for_asset(asset.id, statuses=("queued", "running"))
        return MemoryBuildAssetState(
            asset_id=asset.id,
            file_name=asset.file_name,
            media_kind=media_kind,
            derived_kind=derived_kind,
            parent_asset_id=parent_id,
            observation_id=observation.id if observation else None,
            observation_revision=observation.revision if observation else 0,
            available_fields=available,
            build_states=tuple((build.stage, build.status) for build in builds),
            status=self._classify(available, builds),
            pending_tasks=len(pending),
        )

    def _media_kind(self, asset: Asset) -> tuple[str, str, str]:
        if not asset.derived_kind and not asset.parent_asset_id:
            return "image", "", ""
        if asset.parent_asset_id:
            parent = self._assets.get(asset.parent_asset_id)
            if parent is not None and parent.media_type is MediaType.VIDEO:
                return "video_keyframe", asset.derived_kind or "", parent.id
        return "unknown_derived", asset.derived_kind or "", asset.parent_asset_id or ""

    def _latest_observation(self, asset_id: str) -> Observation | None:
        rows = self._observations.list_by_asset(asset_id)
        if not rows:
            return None
        epoch = datetime.min.replace(tzinfo=UTC)
        return max(rows, key=lambda item: (item.updated_at or item.created_at or epoch, item.revision))

    @staticmethod
    def _available_fields(observation: Observation | None) -> tuple[str, ...]:
        if observation is None:
            return ()
        candidates = (
            ("caption", observation.caption),
            ("place", observation.place),
            ("activity", observation.activity),
            ("event_type", observation.event_type),
            ("ocr_text", observation.ocr_text),
            ("objects", observation.objects),
            ("clothing", observation.clothing),
            ("facts", observation.facts),
            ("source_text", observation.source_text),
        )
        return tuple(name for name, value in candidates if value)

    @staticmethod
    def _classify(
        available: tuple[str, ...],
        builds: Sequence[ObservationBuild],
    ) -> str:
        if builds:
            statuses = {build.stage: build.status for build in builds}
            if any(status in {"partial", "failed"} for status in statuses.values()):
                return "v3_partial"
            if statuses.get("observation") == "succeeded":
                return "v3_complete"
            return "v3_partial"
        return "legacy_partial" if available else "legacy"
