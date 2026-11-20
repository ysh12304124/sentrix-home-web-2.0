"""Atomic, revision-aware text chunk index; deleted or stale evidence is never searchable."""

import json
from collections.abc import Sequence
from threading import RLock

import numpy as np

from sentrix.domain.retrieval.text_memory import TextChunk
from sentrix.infrastructure.persistence.database import Database


class SqliteTextChunkRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._lock = RLock()
        self._matrix_key: tuple[object, ...] = ()
        self._matrix: np.ndarray | None = None

    def similarities(
        self, chunks: Sequence[TextChunk], vector: Sequence[float], model_id: str
    ) -> Sequence[float]:
        if not chunks:
            return ()
        # One bounded cached matrix, shared by concurrent text queries. Its key
        # contains every live revision; edits/deletions cannot reuse stale rows.
        key = (model_id, tuple((c.observation_id, c.start, c.revision) for c in chunks))
        with self._lock:
            if key != self._matrix_key or self._matrix is None:
                if len(chunks) * len(vector) * 4 > 256 * 1024 * 1024:
                    raise RuntimeError(
                        "Text vector working set exceeds 256 MiB; narrow the source/time range"
                    )
                with self._database.connect() as conn:
                    rows = conn.execute(
                        "SELECT observation_id,chunk_start,revision,vector_json FROM text_memory_chunks "
                        "WHERE model_id=? AND observation_id IN (SELECT id FROM observations WHERE scope_id="
                        "(SELECT scope_id FROM observations WHERE id=?))",
                        (model_id, chunks[0].observation_id),
                    ).fetchall()
                lookup = {(r[0], r[1], r[2]): r[3] for r in rows}
                vectors = []
                for c in chunks:
                    raw = lookup.get((c.observation_id, c.start, c.revision))
                    if raw is None:
                        raise RuntimeError("Text index changed during retrieval; retry")
                    values = json.loads(raw)
                    if len(values) != len(vector):
                        raise RuntimeError("Text index dimension mismatch; rebuild required")
                    vectors.append(np.asarray(values, dtype=np.float32))
                matrix = np.stack(vectors)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                if not np.isfinite(matrix).all() or (norms == 0).any():
                    raise RuntimeError("Invalid text index vectors; rebuild required")
                self._matrix = matrix / norms
                self._matrix_key = key
            query = np.asarray(vector, dtype=np.float32)
            query /= np.linalg.norm(query)
            return tuple(float(v) for v in self._matrix @ query)

    def replace(self, observation_id: str, revision: int, model_id: str, chunks: Sequence[TextChunk]) -> None:
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision FROM observations WHERE id=?", (observation_id,)).fetchone()
            if row is None or row[0] != revision:
                raise RuntimeError("Text evidence changed during indexing; retry with the current revision")
            conn.execute("DELETE FROM text_memory_chunks WHERE observation_id=?", (observation_id,))
            conn.executemany(
                "INSERT INTO text_memory_chunks VALUES(?,?,?,?,?,?,?)",
                [
                    (observation_id, c.start, c.end, revision, model_id, c.text, json.dumps(c.vector))
                    for c in chunks
                ],
            )
        with self._lock:
            self._matrix_key = ()
            self._matrix = None

    def indexed(self, observation_id: str, revision: int, model_id: str) -> bool:
        with self._database.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM text_memory_chunks "
                    "WHERE observation_id=? AND revision=? AND model_id=? LIMIT 1",
                    (observation_id, revision, model_id),
                ).fetchone()
                is not None
            )

    def coverage(
        self, scope_id: str, model_id: str, *, source_type: str = "", after: str = "", before: str = ""
    ) -> tuple[int, int]:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT count(*),COALESCE(sum(EXISTS(SELECT 1 FROM text_memory_chunks c "
                "WHERE c.observation_id=o.id AND c.revision=o.revision AND c.model_id=?)),0) "
                "FROM observations o JOIN assets a ON a.id=o.asset_id "
                "WHERE o.scope_id=? AND a.media_type='text' "
                "AND (?='' OR json_extract(o.source_metadata_json,'$.source_type')=?) "
                "AND (?='' OR julianday(o.captured_at)>=julianday(?)) "
                "AND (?='' OR julianday(o.captured_at)<=julianday(?))",
                (model_id, scope_id, source_type, source_type, after, after, before, before),
            ).fetchone()
        return int(row[0]), int(row[1])

    def read(
        self, scope_id: str, model_id: str, *, source_type: str = "", after: str = "", before: str = ""
    ) -> Sequence[TextChunk]:
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT c.observation_id,c.chunk_start,c.chunk_end,c.revision,c.text,"
                "o.asset_id,o.external_id,o.source_metadata_json FROM text_memory_chunks c "
                "JOIN observations o ON o.id=c.observation_id AND o.revision=c.revision "
                "JOIN assets a ON a.id=o.asset_id WHERE o.scope_id=? AND a.media_type='text' "
                "AND c.model_id=? AND (?='' OR json_extract(o.source_metadata_json,'$.source_type')=?) "
                "AND (?='' OR julianday(o.captured_at)>=julianday(?)) "
                "AND (?='' OR julianday(o.captured_at)<=julianday(?)) "
                "ORDER BY o.asset_id,c.chunk_start",
                (scope_id, model_id, source_type, source_type, after, after, before, before),
            ).fetchall()
        return tuple(
            TextChunk(
                asset_id=r["asset_id"],
                observation_id=r["observation_id"],
                revision=r["revision"],
                external_id=r["external_id"],
                source_type=json.loads(r["source_metadata_json"]).get("source_type", "unknown"),
                start=r["chunk_start"],
                end=r["chunk_end"],
                text=r["text"],
            )
            for r in rows
        )
