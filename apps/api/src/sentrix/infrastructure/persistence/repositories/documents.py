from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.documents.models import DocumentChunk, DocumentElement
from sentrix.domain.retrieval.lexical import query_tokens
from sentrix.infrastructure.persistence.database import Database

# Natural-language document questions have many more English function words than
# the short, structured queries used for image captions.  Counting them would
# make boilerplate beat the actual entity/numeric evidence.
_ENGLISH_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "based",
        "been",
        "before",
        "between",
        "both",
        "by",
        "did",
        "do",
        "does",
        "during",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "please",
        "state",
        "that",
        "the",
        "their",
        "these",
        "this",
        "to",
        "under",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "without",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteDocumentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def active_revision(self, asset_id: str) -> int | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT active_revision FROM documents WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def replace(
        self,
        *,
        asset_id: str,
        format: str,
        revision: int,
        parser_version: str,
        elements: Sequence[DocumentElement],
        chunks: Sequence[DocumentChunk],
    ) -> None:
        now = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO documents("
                "asset_id,format,active_revision,parser_version,status,error,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'',?,?) ON CONFLICT(asset_id) DO UPDATE SET format=excluded.format,"
                " active_revision=excluded.active_revision, parser_version=excluded.parser_version,"
                " status='processed', error='', updated_at=excluded.updated_at",
                (asset_id, format, revision, parser_version, "processed", now, now),
            )
            connection.execute("DELETE FROM document_chunk_fts WHERE document_asset_id = ?", (asset_id,))
            connection.execute("DELETE FROM document_chunks WHERE document_asset_id = ?", (asset_id,))
            connection.execute("DELETE FROM document_elements WHERE document_asset_id = ?", (asset_id,))
            connection.executemany(
                "INSERT INTO document_elements("
                "id,document_asset_id,revision,ordinal,kind,page_no,section_path,text,"
                "source_locator,parser_version,confidence,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        e.id,
                        e.document_asset_id,
                        e.revision,
                        e.ordinal,
                        e.kind,
                        e.page_no,
                        e.section_path,
                        e.text,
                        e.source_locator,
                        e.parser_version,
                        e.confidence,
                        now,
                    )
                    for e in elements
                ],
            )
            connection.executemany(
                "INSERT INTO document_chunks("
                "id,document_asset_id,scope_id,revision,ordinal,text,start_element_id,"
                "end_element_id,section_path,page_start,page_end,token_count,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        c.id,
                        c.document_asset_id,
                        c.scope_id,
                        c.revision,
                        c.ordinal,
                        c.text,
                        c.start_element_id,
                        c.end_element_id,
                        c.section_path,
                        c.page_start,
                        c.page_end,
                        c.token_count,
                        now,
                    )
                    for c in chunks
                ],
            )
            connection.executemany(
                "INSERT INTO document_chunk_fts(chunk_id,document_asset_id,scope_id,text) VALUES(?,?,?,?)",
                [(c.id, c.document_asset_id, c.scope_id, c.text) for c in chunks],
            )

    def fail(self, *, asset_id: str, format: str, error: str, parser_version: str) -> None:
        now = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO documents("
                "asset_id,format,active_revision,parser_version,status,error,created_at,updated_at)"
                " VALUES(?,?,0,?,'failed',?,?,?) ON CONFLICT(asset_id) DO UPDATE SET"
                " status='failed',error=excluded.error,parser_version=excluded.parser_version,"
                " updated_at=excluded.updated_at",
                (asset_id, format, parser_version, error, now, now),
            )

    def chunks_for_document(self, asset_id: str) -> Sequence[DocumentChunk]:
        return self._select("WHERE document_asset_id = ? ORDER BY ordinal", (asset_id,))

    def chunk(self, chunk_id: str) -> DocumentChunk | None:
        rows = self._select("WHERE id = ?", (chunk_id,))
        return rows[0] if rows else None

    def neighbors(self, chunk: DocumentChunk, radius: int = 1) -> Sequence[DocumentChunk]:
        return self._select(
            "WHERE document_asset_id = ? AND revision = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
            (chunk.document_asset_id, chunk.revision, max(0, chunk.ordinal - radius), chunk.ordinal + radius),
        )

    def locator_for_chunk(self, chunk: DocumentChunk) -> str:
        with self._database.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT id, source_locator FROM document_elements WHERE id IN (?,?)",
                    (chunk.start_element_id, chunk.end_element_id),
                )
            )
        by_id = {row["id"]: row["source_locator"] for row in rows}
        start = by_id.get(chunk.start_element_id, "")
        end = by_id.get(chunk.end_element_id, start)
        return start if start == end else f"{start}..{end}"

    def lexical_search(
        self, text: str, *, scope_id: str | None, limit: int
    ) -> Sequence[tuple[DocumentChunk, float]]:
        """Retrieve chunks by distinct query-token coverage.

        FTS5 treats whitespace-separated terms as an implicit ``AND``.  A natural
        language question therefore used to require every stopword and every
        qualifier to occur in one chunk, which made the lexical half of hybrid
        retrieval disappear for long questions.  Image caption retrieval already
        uses independent token lookup and ranks by unique-token coverage; apply
        that same principle here while keeping document storage separate.

        The document FTS table contains raw text, so Chinese bigrams emitted by
        ``query_tokens`` need a substring fallback when unicode61 keeps a whole
        CJK phrase as one token.  The fallback is deliberately document-local.
        """
        tokens = _content_tokens(text)
        if not tokens:
            return ()

        rows_by_id: dict[str, sqlite3.Row] = {}
        token_hits: dict[str, int] = {}
        with self._database.connect() as connection:
            for token in tokens:
                fts_sql = (
                    "SELECT c.* FROM document_chunk_fts "
                    "JOIN document_chunks c ON c.id = document_chunk_fts.chunk_id "
                    "WHERE document_chunk_fts MATCH ?"
                )
                fts_params: list[object] = [f'"{token.replace(chr(34), "")}"']
                if scope_id:
                    fts_sql += " AND c.scope_id = ?"
                    fts_params.append(scope_id)
                try:
                    matched = list(connection.execute(fts_sql, tuple(fts_params)))
                except sqlite3.OperationalError:
                    matched = []
                # unicode61 does not index arbitrary CJK bigrams separately.
                if not matched and _contains_cjk(token):
                    like_sql = "SELECT c.* FROM document_chunks c WHERE c.text LIKE ?"
                    like_params: list[object] = [f"%{token}%"]
                    if scope_id:
                        like_sql += " AND c.scope_id = ?"
                        like_params.append(scope_id)
                    matched = list(connection.execute(like_sql, tuple(like_params)))
                for row in matched:
                    chunk_id = str(row["id"])
                    rows_by_id[chunk_id] = row
                    token_hits[chunk_id] = token_hits.get(chunk_id, 0) + 1
        ordered = sorted(token_hits, key=lambda chunk_id: (-token_hits[chunk_id], chunk_id))
        return tuple(
            (self._row(rows_by_id[chunk_id]), float(token_hits[chunk_id]))
            for chunk_id in ordered[:limit]
        )

    def _select(self, clause: str, params: tuple[object, ...]) -> list[DocumentChunk]:
        with self._database.connect() as connection:
            rows = list(connection.execute(f"SELECT * FROM document_chunks {clause}", params))
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> DocumentChunk:
        return DocumentChunk(
            id=row["id"],
            document_asset_id=row["document_asset_id"],
            scope_id=row["scope_id"],
            revision=int(row["revision"]),
            ordinal=int(row["ordinal"]),
            text=row["text"],
            start_element_id=row["start_element_id"],
            end_element_id=row["end_element_id"],
            section_path=row["section_path"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            token_count=int(row["token_count"]),
        )


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _content_tokens(text: str) -> tuple[str, ...]:
    """Drop whole long questions and English function words from token coverage."""
    return tuple(
        token
        for token in query_tokens(text)
        if not any(character.isspace() for character in token) and token not in _ENGLISH_QUERY_STOPWORDS
    )
