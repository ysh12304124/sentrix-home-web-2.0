"""Separate text indexing and lexical/vector RRF; visual retrieval is never invoked."""

import math
import re
from collections import Counter
from datetime import datetime

from sentrix.application.retrieval.index_observation_text import TextIndexOutcome
from sentrix.domain.media.models import Observation
from sentrix.domain.retrieval.fusion import weighted_rrf_score
from sentrix.domain.retrieval.ports import TextEmbedder
from sentrix.domain.retrieval.text_memory import TextChunk, TextChunkRepository, TextSearchResult, split_text


def terms(text: str) -> Counter[str]:
    words = re.findall(r"[a-z0-9]+(?:['.-][a-z0-9]+)*|[\u3400-\u9fff]", text.lower())
    return Counter(
        w for w in words if w not in {"the", "a", "an", "is", "in", "to", "of", "for", "and", "i", "my"}
    )


class IndexTextMemories:
    def __init__(self, repository: TextChunkRepository, embedder: TextEmbedder) -> None:
        self.repository, self.embedder = repository, embedder

    def is_indexed(self, observation: Observation) -> bool:
        return self.repository.indexed(observation.id, observation.revision, self.embedder.model_id)

    def index(
        self, observation: Observation, event_id: str | None = None, *, force: bool = False
    ) -> TextIndexOutcome:
        chunks = []
        for start, end, text in split_text(observation.source_text):
            vector = tuple(self.embedder.embed_query(text))
            if (
                len(vector) != self.embedder.dimension
                or not all(math.isfinite(v) for v in vector)
                or not any(vector)
            ):
                raise RuntimeError("Text chunk embedding unavailable or incompatible")
            chunks.append(
                TextChunk(
                    observation.asset_id,
                    observation.id,
                    observation.revision,
                    observation.external_id,
                    str(observation.source_metadata.get("source_type", "unknown")),
                    start,
                    end,
                    text,
                    vector,
                )
            )
        if not chunks:
            raise RuntimeError("Text body is empty")
        self.repository.replace(observation.id, observation.revision, self.embedder.model_id, chunks)
        return TextIndexOutcome(indexed=True, rows=len(chunks))


class RetrieveTextMemories:
    def __init__(self, repository: TextChunkRepository, embedder: TextEmbedder) -> None:
        self.repository, self.embedder = repository, embedder

    def execute(
        self,
        query: str,
        scope_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
        limit: int = 10,
    ) -> TextSearchResult:
        if not scope_id or not query.strip():
            raise ValueError("Text retrieval requires a scope and a query")
        for bound in (after, before):
            if bound:
                datetime.fromisoformat(bound)
        total, indexed = self.repository.coverage(
            scope_id, self.embedder.model_id, source_type=source_type, after=after, before=before
        )
        chunks = self.repository.read(
            scope_id, self.embedder.model_id, source_type=source_type, after=after, before=before
        )
        query_terms = terms(query)
        docs = [terms(c.text) for c in chunks]
        frequencies = Counter(w for d in docs for w in d)
        mean_length = sum(sum(d.values()) for d in docs) / max(1, len(docs))
        lexical = []
        for index, doc in enumerate(docs):
            score = sum(
                math.log(1 + (len(docs) - frequencies[w] + 0.5) / (frequencies[w] + 0.5))
                * doc[w]
                * 2.2
                / (doc[w] + 1.2 * (0.25 + 0.75 * sum(doc.values()) / max(1, mean_length)))
                for w in query_terms
                if doc[w]
            )
            if score:
                lexical.append((index, score))
        vector = tuple(self.embedder.embed_query(query))
        reason = "text_index_incomplete" if indexed < total else ""
        if (
            len(vector) != self.embedder.dimension
            or not all(math.isfinite(v) for v in vector)
            or not any(vector)
        ):
            reason = "text_query_embedding_unavailable"
        ann = []
        if reason != "text_query_embedding_unavailable":
            ann = list(enumerate(self.repository.similarities(chunks, vector, self.embedder.model_id)))
        # Collapse chunks to their source before assigning per-channel ranks.
        ranks: dict[str, dict[str, int]] = {}
        best: dict[str, TextChunk] = {}
        for channel, hits in [("lexical", lexical), ("text_ann", ann)]:
            seen = set()
            for index, _score in sorted(
                hits, key=lambda x: (-x[1], chunks[x[0]].asset_id, chunks[x[0]].start)
            ):
                c = chunks[index]
                if c.asset_id in seen:
                    continue
                seen.add(c.asset_id)
                ranks.setdefault(c.asset_id, {})[channel] = len(seen)
                best.setdefault(c.asset_id, c)
        ordered = sorted(
            ranks, key=lambda aid: (-weighted_rrf_score(ranks[aid], {"lexical": 1.0, "text_ann": 1.0}), aid)
        )[: max(1, min(limit, 50))]
        return TextSearchResult(tuple(best[aid] for aid in ordered), bool(reason), reason)
