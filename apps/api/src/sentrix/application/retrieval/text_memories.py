"""Separate text indexing and lexical/vector RRF; visual retrieval is never invoked."""

import math
import re
from collections import Counter
from datetime import datetime

from sentrix.application.retrieval.index_observation_text import TextIndexOutcome
from sentrix.domain.media.models import Observation
from sentrix.domain.retrieval.fusion import weighted_rrf_score
from sentrix.domain.retrieval.ports import Reranker, TextEmbedder
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
    def __init__(
        self,
        repository: TextChunkRepository,
        embedder: TextEmbedder,
        *,
        reranker: Reranker | None = None,
        rerank_top_k: int = 24,
    ) -> None:
        self.repository, self.embedder = repository, embedder
        self.reranker, self.rerank_top_k = reranker, max(6, rerank_top_k)

    def execute(
        self,
        query: str,
        scope_id: str,
        *,
        source_type: str = "",
        after: str = "",
        before: str = "",
        limit: int = 6,
        question: str = "",
    ) -> TextSearchResult:
        if not scope_id or not query.strip():
            raise ValueError("Text retrieval requires a scope and a query")
        source_type = source_type.strip().lower()
        if source_type in {"email/document", "email_or_document", "all"}:
            source_type = ""
        if source_type and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_type):
            raise ValueError("source_type must be one source tag, for example email; omit to search all text")
        for bound in (after, before):
            if bound:
                datetime.fromisoformat(bound)
        if before and len(before) == 10:
            before += "T23:59:59.999999"
        if after and before and after[:10] > before[:10]:
            raise ValueError("after must not be later than before")
        filters = {"source_type": source_type, "after": after, "before": before}
        self.repository.prepare_sources(scope_id)
        total, indexed = self.repository.coverage(scope_id, self.embedder.model_id, **filters)
        chunks = self.repository.read(scope_id, self.embedder.model_id, **filters)
        lexical = self.repository.lexical(
            " ".join(terms(query)),
            scope_id,
            self.embedder.model_id,
            source_type=source_type,
            after=after,
            before=before,
        )
        query_terms = terms(query)
        docs = [terms(c.text) for c in chunks]
        frequencies = Counter(w for d in docs for w in d)
        # This score only picks a readable passage within each source. Source
        # ordering belongs to FTS/RRF and the optional model reranker below.
        passages = {
            i: sum(math.log(1 + len(docs) / (1 + frequencies[w])) for w in query_terms if doc[w])
            for i, doc in enumerate(docs)
        }
        vector = tuple(self.embedder.embed_query(query))
        reason = "text_index_incomplete" if indexed < total else ""
        valid_vector = (
            len(vector) == self.embedder.dimension and all(math.isfinite(v) for v in vector) and any(vector)
        )
        if not valid_vector:
            reason = ";".join(filter(None, (reason, "text_query_embedding_unavailable")))
        ann = (
            list(enumerate(self.repository.similarities(chunks, vector, self.embedder.model_id)))
            if valid_vector
            else []
        )
        best: dict[str, TextChunk] = {}
        ann_best = dict(ann)
        for i in sorted(passages, key=lambda i: (-passages[i], -ann_best.get(i, 0.0), chunks[i].start)):
            best.setdefault(chunks[i].asset_id, chunks[i])
        ranks: dict[str, dict[str, int]] = {}
        lexical_ids = [aid for aid, _ in lexical if aid in best]
        for rank, aid in enumerate(lexical_ids, 1):
            ranks.setdefault(aid, {})["fts_bm25"] = rank
        ann_ids: list[str] = []
        for i, _ in sorted(ann, key=lambda pair: -pair[1]):
            aid = chunks[i].asset_id
            if aid not in ann_ids:
                ann_ids.append(aid)
                ranks.setdefault(aid, {})["text_ann"] = len(ann_ids)
                if len(ann_ids) == 64:
                    break
        ordered = sorted(
            ranks, key=lambda aid: (-weighted_rrf_score(ranks[aid], {"fts_bm25": 1.0, "text_ann": 1.0}), aid)
        )[:64]
        fused_ids = tuple(ordered)
        relations = tuple(self.repository.related(scope_id, ordered[:3], self.embedder.model_id, **filters))
        expanded = [r.asset_id for r in relations if r.asset_id in best and r.asset_id not in ordered]
        # Reserve a bounded place for expansion in the reranker head; the union
        # is not cut to six until after every admitted source has been scored.
        head_size = max(6, self.rerank_top_k)
        ordered = list(dict.fromkeys([*ordered[: head_size - len(expanded)], *expanded, *ordered]))
        candidates = tuple(best[aid] for aid in ordered)
        by_source: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            if c.asset_id in ordered:
                by_source.setdefault(c.asset_id, []).append(i)
        status = "not_configured"
        if self.reranker is not None and ordered:
            head = ordered[:head_size]
            documents = [
                "Title: "
                + best[aid].title
                + "\nSource date: "
                + best[aid].recorded_at
                + "\n"
                + "\n".join(
                    chunks[i].text
                    for i in sorted(by_source[aid], key=lambda i: (-passages[i], chunks[i].start))[:3]
                )
                for aid in head
            ]
            context = query + (
                "\nOriginal question (including temporal context): " + question if question else ""
            )
            scores = self.reranker.rerank(context, documents)
            if len(scores) == len(head) and all(math.isfinite(s) for s in scores):
                ordered[: len(head)] = [
                    aid for aid, _ in sorted(zip(head, scores, strict=True), key=lambda p: -p[1])
                ]
                status = self.reranker.model_id
            else:
                status = "degraded"
                reason = ";".join(filter(None, (reason, "text_reranker_unavailable")))
        selected = tuple(best[aid] for aid in ordered[: max(1, min(limit, 6))])
        stages = (
            ("fts_bm25", tuple(lexical_ids)),
            ("text_ann", tuple(ann_ids)),
            ("rrf", fused_ids),
            ("expanded", tuple(expanded)),
            ("ranked", tuple(ordered)),
        )
        return TextSearchResult(selected, bool(reason), reason, candidates, status, relations, stages)
