"""Atomic, revision-aware text chunk index; deleted or stale evidence is never searchable."""

import json
import re
import sqlite3
from collections.abc import Sequence
from threading import RLock

import numpy as np

from sentrix.domain.retrieval.text_memory import TextChunk, TextRelation
from sentrix.domain.retrieval.text_relations import source_link_keys
from sentrix.infrastructure.persistence.database import Database


class SqliteTextChunkRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._lock = RLock()
        self._matrix_key: tuple[object, ...] = ()
        self._matrix: np.ndarray | None = None

    @staticmethod
    def _refresh_source(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
        conn.execute("DELETE FROM text_source_fts WHERE observation_id=?", (row["id"],))
        conn.execute("DELETE FROM text_source_projection WHERE observation_id=?", (row["id"],))
        conn.execute("INSERT INTO text_source_projection VALUES(?,?)", (row["id"], row["revision"]))
        subject = re.search(r"^Subject:\s*(.+)$", row["source_text"], re.I | re.M)
        title = (subject[1] + "\n" if subject else "") + (row["caption"] or "")
        conn.execute("INSERT INTO text_source_fts VALUES(?,?,?)", (row["id"], title, row["source_text"]))
        keys = source_link_keys(row["source_text"], json.loads(row["source_metadata_json"] or "{}"))
        conn.executemany(
            "INSERT INTO text_source_link_keys VALUES(?,?,?)", [(row["id"], kind, key) for kind, key in keys]
        )

    def prepare_sources(self, scope_id: str) -> None:
        with self._database.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT o.* FROM observations o JOIN assets a ON a.id=o.asset_id "
                "LEFT JOIN text_source_projection p ON p.observation_id=o.id "
                "WHERE o.scope_id=? AND length(o.source_text)>0 "
                "AND a.media_type='text' "
                "AND (p.revision IS NULL OR p.revision!=o.revision)",
                (scope_id,),
            ).fetchall()
            for row in rows:
                self._refresh_source(conn, row)

    def lexical(
        self,
        query: str,
        scope_id: str,
        model_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
        limit: int = 64,
    ) -> Sequence[tuple[str, float]]:
        tokens = re.findall(r"[\w]+", query, re.UNICODE)[:40]
        if not tokens:
            return ()
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT o.asset_id,bm25(text_source_fts,0.0,4.0,1.0) AS relevance "
                "FROM text_source_fts JOIN observations o ON o.id=text_source_fts.observation_id "
                "JOIN text_source_projection p ON p.observation_id=o.id AND p.revision=o.revision "
                "WHERE text_source_fts MATCH ? AND o.scope_id=? "
                "AND (?='' OR json_extract(o.source_metadata_json,'$.source_type')=?) "
                "AND (?='' OR julianday(o.captured_at)>=julianday(?)) "
                "AND (?='' OR julianday(o.captured_at)<=julianday(?)) "
                "AND EXISTS(SELECT 1 FROM text_memory_chunks c WHERE c.observation_id=o.id "
                "AND c.revision=o.revision AND c.model_id=?) ORDER BY relevance,o.asset_id LIMIT ?",
                (match, scope_id, source_type, source_type, after, after, before, before, model_id, limit),
            ).fetchall()
        return tuple((str(r[0]), -float(r[1])) for r in rows)

    def related(
        self,
        scope_id: str,
        seeds: Sequence[str],
        model_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
    ) -> Sequence[TextRelation]:
        result: list[TextRelation] = []
        seen = set(seeds)
        with self._database.connect() as conn:
            for seed in seeds[:3]:
                rows = conn.execute(
                    "SELECT o.asset_id,k.kind FROM text_source_link_keys k "
                    "JOIN observations o ON o.id=k.observation_id "
                    "JOIN text_source_projection p ON p.observation_id=o.id AND p.revision=o.revision "
                    "JOIN text_source_link_keys s ON s.kind=k.kind AND s.link_key=k.link_key "
                    "JOIN observations origin ON origin.id=s.observation_id "
                    "JOIN text_source_projection sp ON sp.observation_id=origin.id "
                    "AND sp.revision=origin.revision "
                    "WHERE origin.asset_id=? AND origin.scope_id=? AND o.scope_id=? "
                    "AND (?='' OR json_extract(o.source_metadata_json,'$.source_type')=?) "
                    "AND (?='' OR julianday(o.captured_at)>=julianday(?)) "
                    "AND (?='' OR julianday(o.captured_at)<=julianday(?)) "
                    "AND (k.kind NOT IN ('subject','entity') OR "
                    "(abs(julianday(o.captured_at)-julianday(origin.captured_at))<=30 "
                    "AND (SELECT count(*) FROM text_source_link_keys n "
                    "JOIN observations no ON no.id=n.observation_id "
                    "WHERE n.kind=k.kind AND n.link_key=k.link_key AND no.scope_id=? "
                    "AND abs(julianday(no.captured_at)-julianday(origin.captured_at))<=30)<=8)) "
                    "AND EXISTS(SELECT 1 FROM text_memory_chunks c WHERE c.observation_id=o.id "
                    "AND c.revision=o.revision AND c.model_id=?) "
                    "ORDER BY CASE k.kind WHEN 'subject' THEN 1 WHEN 'entity' THEN 2 ELSE 0 END,"
                    "o.asset_id LIMIT 64",
                    (
                        seed,
                        scope_id,
                        scope_id,
                        source_type,
                        source_type,
                        after,
                        after,
                        before,
                        before,
                        scope_id,
                        model_id,
                    ),
                ).fetchall()
                added = 0
                for row in rows:
                    if row[0] not in seen:
                        seen.add(row[0])
                        result.append(TextRelation(seed, str(row[0]), str(row[1])))
                        added += 1
                        if added == 2:
                            break
        return tuple(result)

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
            source = conn.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
            self._refresh_source(conn, source)
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
                "o.asset_id,o.external_id,o.source_metadata_json,o.caption,o.captured_at "
                "FROM text_memory_chunks c "
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
                title=r["caption"],
                recorded_at=r["captured_at"] or "",
            )
            for r in rows
        )
