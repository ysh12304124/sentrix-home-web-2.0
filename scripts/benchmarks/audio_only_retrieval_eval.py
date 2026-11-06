#!/usr/bin/env python3
"""Evaluate the already-built album3 index using only audio text vectors."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api" / "src"
for entry in (str(ROOT), str(API_SRC)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmarks.video_multimodal_retrieval_ab import (  # noqa: E402
    DEFAULT_AUDIO,
    DEFAULT_DATASET,
    DEFAULT_QA,
    MIGRATIONS,
    SCOPE_ID,
    CachedQueryEmbedder,
    load_audio_segments,
    load_queries,
    load_query_cache,
    target_rank,
)
from sentrix.application.retrieval.retrieve_text_ann import (  # noqa: E402
    RetrieveTextAnn,
    TextSearchRequest,
)
from sentrix.domain.retrieval import scheme  # noqa: E402
from sentrix.infrastructure.persistence.database import Database  # noqa: E402
from sentrix.infrastructure.persistence.repositories.vectors import (  # noqa: E402
    SqliteVectorRepository,
)

DEFAULT_INDEX = ROOT / "audio_eval" / "outputs" / "video-multimodal-retrieval-ab"
DEFAULT_OUTPUT = DEFAULT_INDEX / "audio-only-results.json"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "20261027-audio-only-retrieval.md"
TOP_KS = (1, 5, 10)


def metrics(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    total = len(rows)
    ranks = [int(row["audio_rank"]) for row in rows if isinstance(row.get("audio_rank"), int)]
    output: dict[str, float | int] = {"queries": total}
    for k in TOP_KS:
        output[f"recall@{k}"] = round(sum(rank <= k for rank in ranks) / total, 4) if total else 0.0
    output["mrr"] = round(sum(1.0 / rank for rank in ranks) / total, 4) if total else 0.0
    output["not_retrievable"] = total - len(ranks)
    return output


def pct(value: object) -> str:
    return f"{float(value) * 100:.2f}%"


def write_report(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    all_rows = summary["all_queries"]
    eligible = summary["audio_eligible"]
    random = summary["random_baseline_audio_eligible"]
    sources = payload["corpus"]["required_evidence_sources"]
    source_summary = "，".join(f"{name} {count} 条" for name, count in sources.items())
    lines = [
        "# 纯音频检索召回率评测",
        "",
        f"> 生成时间：{payload['generated_at']}。关闭视觉通道，只运行生产 `RetrieveTextAnn`。",
        "",
        "## 结果",
        "",
        "| 评测范围 | 查询数 | Recall@1 | Recall@5 | Recall@10 | MRR |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| 全部 video-only QA | {all_rows['queries']} | {pct(all_rows['recall@1'])} | "
            f"{pct(all_rows['recall@5'])} | {pct(all_rows['recall@10'])} | {all_rows['mrr']:.4f} |"
        ),
        (
            f"| GT 视频有门禁通过转写 | {eligible['queries']} | {pct(eligible['recall@1'])} | "
            f"{pct(eligible['recall@5'])} | {pct(eligible['recall@10'])} | {eligible['mrr']:.4f} |"
        ),
        (
            f"| 22 个音频候选随机排序期望 | {eligible['queries']} | "
            f"{pct(random['recall@1'])} | {pct(random['recall@5'])} | "
            f"{pct(random['recall@10'])} | - |"
        ),
        "",
        "## 解释",
        "",
        (
            f"- 全部 {all_rows['queries']} 题中，只有 {eligible['queries']} 题的 GT 视频本身有"
            "门禁通过转写；其余目标不在音频索引中，所以纯音频理论上不可能召回。"
        ),
        (
            "- 应主要看“GT 视频有转写”子集。若它只略高于随机基线，说明转写文本与查询语义"
            "关系弱；这时调高融合权重只会放大噪声。"
        ),
        (
            "- 本评测使用上一轮真实 Paraformer+VAD+质量门禁产物，以及同一个 WeMM 2048 维"
            "文本空间和 SQLite 精确余弦检索，没有使用文件名、日期、地点或视觉信息。"
        ),
        (
            f"- 这 {all_rows['queries']} 条 QA 中没有一条把音频/转写标成必需证据："
            f"{source_summary}。因此本结果能证明“当前音频"
            "文本不能帮助现有视频 QA”，但不能替代专门的语音内容检索集。要评价音频模态"
            "本身，还需基于人工核验转写增加“视频里说了什么/谁提到了某句话”一类查询。"
        ),
        "",
        "## 代表性命中",
        "",
        "| QA | GT | 音频名次 | 问题 | GT 转写 |",
        "|---|---|---:|---|---|",
    ]
    ranked = sorted(
        (row for row in payload["queries"] if row["audio_eligible"]),
        key=lambda row: (row["audio_rank"] or 10**9, row["qa_id"]),
    )
    for row in ranked[:10]:
        question = row["question"].replace("|", "\\|")[:90]
        transcript = row["target_transcript"].replace("|", "\\|")[:100]
        lines.append(
            f"| {row['qa_id']} | {', '.join(row['targets'])} | {row['audio_rank'] or '-'} | "
            f"{question} | {transcript} |"
        )
    lines.extend(
        [
            "",
            "## 代表性漏召回或低排名",
            "",
            "| QA | GT | 音频名次 | 问题 | GT 转写 |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in sorted(
        (row for row in payload["queries"] if row["audio_eligible"]),
        key=lambda row: (-(row["audio_rank"] or 10**9), row["qa_id"]),
    )[:10]:
        question = row["question"].replace("|", "\\|")[:90]
        transcript = row["target_transcript"].replace("|", "\\|")[:100]
        lines.append(
            f"| {row['qa_id']} | {', '.join(row['targets'])} | {row['audio_rank'] or '-'} | "
            f"{question} | {transcript} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--audio-results", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    video_ids = {Path(value).stem for value in manifest.get("videos") or []}
    queries = load_queries(args.qa, video_ids, 0)
    audio_segments = load_audio_segments(args.audio_results, video_ids)
    query_vectors = load_query_cache(args.index / "query-vectors.npz")
    missing = [case.qa_id for case in queries if case.question not in query_vectors]
    if missing:
        raise RuntimeError(f"query vector cache missing {len(missing)} cases")

    database = Database(args.index / "sentrix.sqlite3", MIGRATIONS)
    vectors = SqliteVectorRepository(database)
    stored_audio = vectors.count(space=scheme.SEMANTIC_SPACE, model_name=scheme.WEMM_MODEL)
    if stored_audio != len(audio_segments):
        raise RuntimeError(
            f"audio vector count mismatch: database={stored_audio}, expected={len(audio_segments)}"
        )
    retriever = RetrieveTextAnn(embedder=CachedQueryEmbedder(query_vectors), vectors=vectors)
    rows: list[dict[str, Any]] = []
    for case in queries:
        result = retriever.execute(
            TextSearchRequest(text=case.question, scope_id=SCOPE_ID, limit=len(audio_segments))
        )
        ranking = [hit.asset_id for hit in result.hits]
        target_transcript = " / ".join(
            audio_segments[target].normalized_text
            for target in case.targets
            if target in audio_segments
        )
        rows.append(
            {
                "qa_id": case.qa_id,
                "question": case.question,
                "targets": list(case.targets),
                "required_evidence_sources": list(case.evidence_sources),
                "audio_eligible": any(target in audio_segments for target in case.targets),
                "target_transcript": target_transcript,
                "audio_rank": target_rank(ranking, case.targets),
                "audio_top10": ranking[:10],
            }
        )

    eligible = [row for row in rows if row["audio_eligible"]]
    candidate_count = len(audio_segments)
    evidence_counts = Counter(
        source for case in queries for source in case.evidence_sources
    )
    random_baseline = {
        "queries": len(eligible),
        **{
            f"recall@{k}": round(min(k, candidate_count) / candidate_count, 4)
            for k in TOP_KS
        },
    }
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus": {
            "queries": len(queries),
            "audio_candidates": candidate_count,
            "audio_eligible_queries": len(eligible),
            "model": scheme.WEMM_MODEL,
            "dimension": scheme.WEMM_DIMENSION,
            "required_evidence_sources": dict(evidence_counts.most_common()),
        },
        "summary": {
            "all_queries": metrics(rows),
            "audio_eligible": metrics(eligible),
            "random_baseline_audio_eligible": random_baseline,
        },
        "queries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.report, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
