"""All text sources share persistence, indexing, idempotence and retry semantics."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.application.retrieval.text_memories import IndexTextMemories
from sentrix.domain.media.ports import MediaStorage, ObservationRepository
from sentrix.domain.media.text_memory import TextMemoryItem, TextMemoryRepository, text_asset_id
from sentrix.domain.retrieval.ports import LexicalIndex


class ImportTextMemories:
    def __init__(
        self,
        *,
        repository: TextMemoryRepository,
        observations: ObservationRepository,
        storage: MediaStorage,
        lexical: LexicalIndex,
        index: IndexObservationText | IndexTextMemories,
    ) -> None:
        self.repository, self.observations = repository, observations
        self.storage, self.lexical, self.index = storage, lexical, index

    def execute(self, scope_id: str, rows: Sequence[dict[str, object]]) -> dict[str, object]:
        if not scope_id.strip() or not rows:
            raise ValueError("需要明确相册与非空文本列表")
        items = [TextMemoryItem.parse(row) for row in rows]
        if len({item.external_id for item in items}) != len(items):
            raise ValueError("一次导入不能包含重复文本 ID")
        for item in items:
            existing = self.repository.find(scope_id, item.external_id)
            if existing and (
                existing["content_sha256"] != item.fingerprint
                or json.loads(str(existing.get("source_metadata_json", "{}"))).get("source_type", "unknown")
                != item.metadata["source_type"]
            ):
                raise ValueError(f"文本源内容或来源冲突，请使用记忆修正接口：{item.external_id}")
        imported = skipped = indexed = 0
        errors = []
        records = []
        for item in items:
            existing = self.repository.find(scope_id, item.external_id)
            if existing:
                record = existing
                skipped += 1
            else:
                aid = text_asset_id(scope_id, item.external_id)
                path = self.storage.store(aid, item.external_id + ".txt", item.body.encode())
                record = self.repository.insert(scope_id, item, str(path))
                imported += 1
            records.append(
                {
                    "asset_id": record["asset_id"],
                    "external_id": item.external_id,
                    "deduplicated": existing is not None,
                }
            )
            observation = self.observations.get(str(record["observation_id"]))
            if observation is None:
                raise RuntimeError("文本观察缺失")
            if record["indexed_revision"] == observation.revision and (
                not isinstance(self.index, IndexTextMemories) or self.index.is_indexed(observation)
            ):
                indexed += 1
                continue
            error = ""
            try:
                self.lexical.refresh_from_observation(observation)
                outcome = self.index.index(observation, str(record["event_id"]), force=True)
                if not outcome.indexed:
                    raise RuntimeError(outcome.reason)
                indexed += 1
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                errors.append({"external_id": item.external_id, "error": error})
            self.repository.finish(str(record["asset_id"]), observation.revision, error)
        return {
            "scope_id": scope_id,
            "total": len(items),
            "imported": imported,
            "skipped": skipped,
            "indexed": indexed,
            "errors": errors,
            "items": records,
            "status": "partial" if errors else "completed",
        }
