"""Deletion rebuild composition: uses current configured model/embedder and data roots."""

from sentrix.bootstrap.container import Container
from sentrix.infrastructure.persistence.repositories.rebuilds import SqliteRebuildRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex


def build_rebuilder(container: Container) -> SqliteRebuildRepository:
    return SqliteRebuildRepository(
        container.database,
        model=container.chat_model,
        embedder=container.text_embedder,
        data_dir=container.settings.data_dir,
        index=container.vector_index if isinstance(container.vector_index, QdrantVectorIndex) else None,
    )
