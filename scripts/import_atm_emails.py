"""Import released ATM email memory (never QA answers) into an explicit data directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from sentrix.application.media.import_emails import ImportEmails
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.bootstrap.import_wiring import build_memory_graph
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.models.legacy import BgeSidecarEmbedder
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.emails import SqliteEmailRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.family import SqliteFamilyGraphRepository
from sentrix.infrastructure.persistence.repositories.memory_graph import SqliteMemoryGraphRepository
from sentrix.infrastructure.persistence.repositories.participants import SqliteEventParticipantRepository
from sentrix.infrastructure.persistence.repositories.relationships import SqliteRelationshipRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--embedding-url", default="http://127.0.0.1:8101")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(
        args.data_dir / "sentrix.db", root / "apps/api/src/sentrix/infrastructure/persistence/migrations"
    )
    db.migrate()
    assets, observations = SqliteAssetRepository(db), SqliteObservationRepository(db)
    entities = SqliteEntityRepository(db)
    places = SqliteAssetPlaceRepository(db)
    vectors = SqliteTextVectorRepository(db)
    embedder = BgeSidecarEmbedder(args.embedding_url, timeout_s=90)
    importer = ImportEmails(
        repository=SqliteEmailRepository(db),
        observations=observations,
        storage=FileSystemMediaStorage(args.data_dir / "media"),
        lexical=SqliteLexicalIndex(db, observations, assets=assets, places=places),
        index=IndexObservationText(embedder=embedder, vectors=vectors),
    )
    SqliteSpaceRepository(db).ensure(args.scope_id, "ATM-Bench email validation", "benchmark")
    rows = json.loads(args.source.read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    totals = {"total": len(rows), "imported": 0, "skipped": 0, "indexed": 0, "errors": []}
    for start in range(0, len(rows), 50):
        result = importer.execute(args.scope_id, rows[start : start + 50])
        for key in ("imported", "skipped", "indexed"):
            totals[key] += result[key]
        totals["errors"].extend(result["errors"])
        print(json.dumps({"processed": min(start + 50, len(rows)), **totals}, ensure_ascii=False), flush=True)
    container = SimpleNamespace(
        asset_repository=assets,
        observation_repository=observations,
        event_repository=SqliteEventRepository(db),
        event_participant_repository=SqliteEventParticipantRepository(db),
        entity_repository=entities,
        fact_repository=SqliteFactRepository(db),
        relationship_repository=SqliteRelationshipRepository(db),
        memory_graph=SqliteMemoryGraphRepository(db),
        family_graph_repository=SqliteFamilyGraphRepository(db, entities),
        causal_judge=None,
        vector_repository=None,
    )
    stats = build_memory_graph(container).execute(args.scope_id)
    report = {
        **totals,
        "scope_id": args.scope_id,
        "graph_nodes": stats.node_count,
        "graph_edges": stats.edge_count,
        "status": "partial" if totals["errors"] else "completed",
    }
    (args.data_dir / "email-import-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if totals["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
