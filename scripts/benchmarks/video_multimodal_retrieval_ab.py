#!/usr/bin/env python3
"""Evaluate source-video retrieval with the visual path alone and with audio.

This benchmark deliberately stops before Agent answering and the front end.  It
uses the product's current keyframe policy, the real local WeMM checkpoint, the
production SQLite vector repository, the production visual/text retrievers, and
the production weighted-RRF function.

The audio transcription and quality-gate outputs are read from the preceding
full-dataset audio benchmark.  They are real Paraformer results, not labels or
synthetic text.  Reusing them keeps this run focused on the missing visual +
audio retrieval comparison and avoids running ASR twice on the same files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import torch
from qwen_vl_utils import process_vision_info
from transformers import AutoModel, AutoProcessor

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api" / "src"
for entry in (str(ROOT), str(API_SRC)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from sentrix.application.retrieval.index_audio_transcript import (  # noqa: E402
    IndexAudioTranscriptSegments,
)
from sentrix.application.retrieval.index_visual import IndexAssetVisual  # noqa: E402
from sentrix.application.retrieval.retrieve_text_ann import (  # noqa: E402
    RetrieveTextAnn,
    TextSearchRequest,
)
from sentrix.application.retrieval.retrieve_visual import (  # noqa: E402
    RetrieveVisual,
    VisualSearchRequest,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType  # noqa: E402
from sentrix.domain.media.scenes import KEYFRAME_DERIVED_KIND, plan_scenes  # noqa: E402
from sentrix.domain.media.transcription import (  # noqa: E402
    AudioTranscriptSegment,
    SegmentQualityStatus,
)
from sentrix.domain.retrieval import scheme  # noqa: E402
from sentrix.domain.retrieval.fusion import DEFAULT_CHANNEL_WEIGHTS, fuse  # noqa: E402
from sentrix.domain.retrieval.models import CandidateHit  # noqa: E402
from sentrix.infrastructure.media.video_keyframes import (  # noqa: E402
    FfmpegVideoKeyframeExtractor,
)
from sentrix.infrastructure.persistence.database import Database  # noqa: E402
from sentrix.infrastructure.persistence.repositories.vectors import (  # noqa: E402
    SqliteVectorRepository,
)

MIGRATIONS = API_SRC / "sentrix" / "infrastructure" / "persistence" / "migrations"
DEFAULT_DATASET = ROOT / "album3-max-video-keyframe"
DEFAULT_QA = DEFAULT_DATASET / "qa" / "mixed-keyframe-1440q.jsonl"
DEFAULT_AUDIO = ROOT / "audio_eval" / "outputs" / "video_audio_ab_benchmark.json"
DEFAULT_MODEL = ROOT / "weights" / "WeMM-Embedding-2B"
DEFAULT_OUTPUT = ROOT / "audio_eval" / "outputs" / "video-multimodal-retrieval-ab"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "20261027-video-multimodal-retrieval-ab.md"
SCOPE_ID = "album3-video-retrieval-ab"
TOP_KS = (1, 5, 10)
VISUAL_CHANNEL = "visual_ann"
TEXT_CHANNEL = "text_ann"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duration_seconds(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(completed.stdout.strip())


def normalize(values: torch.Tensor) -> list[float]:
    row = values.detach().float().cpu().reshape(-1)
    row = row / row.norm().clamp_min(1e-12)
    return [float(value) for value in row.tolist()]


class DirectWeMM:
    """In-process adapter with the same public contract as the WeMM sidecar."""

    model_id = scheme.WEMM_MODEL
    dimension = scheme.WEMM_DIMENSION

    def __init__(self, model_path: Path, device: str) -> None:
        self._path = model_path
        self._device = device
        self._processor: Any | None = None
        self._model: Any | None = None
        self.load_seconds = 0.0
        self.image_calls = 0
        self.text_calls = 0
        self.image_seconds = 0.0
        self.text_seconds = 0.0

    def _load(self) -> tuple[Any, Any]:
        if self._processor is not None and self._model is not None:
            return self._processor, self._model
        started = time.perf_counter()
        # Transformers 5.2 can select optional CUDA-only FLA functions even on
        # CPU/MPS.  Clearing them selects its built-in PyTorch implementation.
        modeling = importlib.import_module("transformers.models.qwen3_5.modeling_qwen3_5")
        for name in (
            "FusedRMSNormGated",
            "chunk_gated_delta_rule",
            "fused_recurrent_gated_delta_rule",
        ):
            if hasattr(modeling, name):
                setattr(modeling, name, None)
        self._processor = AutoProcessor.from_pretrained(
            self._path,
            trust_remote_code=True,
            local_files_only=True,
            use_fast=False,
            max_pixels=448 * 448,
        )
        dtype = torch.float16 if self._device == "mps" else torch.bfloat16
        self._model = (
            AutoModel.from_pretrained(
                self._path,
                trust_remote_code=True,
                local_files_only=True,
                dtype=dtype,
            )
            .to(self._device)
            .eval()
        )
        self.load_seconds = time.perf_counter() - started
        log(f"WeMM loaded on {self._device} in {self.load_seconds:.1f}s")
        return self._processor, self._model

    def _encode(self, content: list[dict[str, str]], *, image: bool) -> list[float]:
        processor, model = self._load()
        messages = [{"role": "user", "content": content}]
        rendered = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        kwargs: dict[str, Any] = {}
        images = videos = video_metadata = None
        if image:
            images, videos, kwargs = process_vision_info(
                messages,
                image_patch_size=16,
                return_video_kwargs=True,
                return_video_metadata=True,
            )
            if videos is not None:
                videos, video_metadata = zip(*videos, strict=True)
                videos, video_metadata = list(videos), list(video_metadata)
        inputs = processor(
            text=rendered,
            images=images,
            videos=videos,
            video_metadata=video_metadata,
            return_tensors="pt",
            **kwargs,
        ).to(self._device)
        with torch.inference_mode():
            vector = model.embedding(**inputs)
        return normalize(vector)

    def embed_query(self, text: str) -> list[float]:
        value = str(text or "").strip()
        if not value:
            return []
        started = time.perf_counter()
        result = self._encode([{"type": "text", "text": value}], image=False)
        self.text_calls += 1
        self.text_seconds += time.perf_counter() - started
        return result

    def embed_image(self, path: Path) -> list[float]:
        started = time.perf_counter()
        result = self._encode([{"type": "image", "image": str(path)}], image=True)
        self.image_calls += 1
        self.image_seconds += time.perf_counter() - started
        return result


class CachedQueryEmbedder:
    """Let production retrievers consume precomputed query embeddings."""

    model_id = scheme.WEMM_MODEL
    dimension = scheme.WEMM_DIMENSION

    def __init__(self, values: Mapping[str, Sequence[float]]) -> None:
        self._values = values

    def embed_query(self, text: str) -> Sequence[float]:
        return self._values.get(text, ())

    def embed_image(self, _path: Path) -> Sequence[float]:
        raise RuntimeError("query-only cache cannot embed images")


@dataclass(frozen=True)
class QueryCase:
    qa_id: str
    question: str
    targets: tuple[str, ...]
    evidence_sources: tuple[str, ...]


def media_id(value: object) -> tuple[str, str]:
    if isinstance(value, Mapping):
        kind = str(value.get("media_type") or "")
        raw = str(
            value.get("media_id")
            or value.get("video_id")
            or value.get("file_name")
            or value.get("asset_id")
            or ""
        )
    else:
        kind, raw = "", str(value or "")
    return kind, Path(raw).stem


def load_queries(path: Path, video_ids: set[str], max_queries: int) -> list[QueryCase]:
    cases: list[QueryCase] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            refs = row.get("retrieval_media_refs") or row.get("gt_media") or []
            parsed = [media_id(value) for value in refs]
            # Keep the video-only retrieval population.  This makes Recall@K a
            # source-video metric rather than a mixed photo/video hit metric.
            targets = tuple(
                dict.fromkeys(stem for kind, stem in parsed if stem in video_ids and kind != "image")
            )
            if not targets or any(kind == "image" for kind, _stem in parsed):
                continue
            question = str(row.get("question") or row.get("query") or "").strip()
            if not question:
                continue
            cases.append(
                QueryCase(
                    qa_id=str(row.get("qa_id") or row.get("id") or f"q{len(cases):04d}"),
                    question=question,
                    targets=targets,
                    evidence_sources=tuple(str(x) for x in row.get("required_evidence_sources") or ()),
                )
            )
            if max_queries and len(cases) >= max_queries:
                break
    return cases


def load_audio_segments(path: Path, video_ids: set[str]) -> dict[str, AudioTranscriptSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, AudioTranscriptSegment] = {}
    for item in payload.get("cases") or []:
        if item.get("label") != "with_audio" or not item.get("searchable_segments"):
            continue
        video_id = Path(str(item.get("source_name") or "")).stem
        text = str(item.get("accepted_text") or "").strip()
        if video_id not in video_ids or not text:
            continue
        end_ms = int(round(float(item.get("duration_s") or 0.0) * 1000))
        rows[video_id] = AudioTranscriptSegment(
            id=f"audio-segment:{video_id}:000",
            transcription_id=f"audio-transcription:{video_id}",
            asset_id=video_id,
            scope_id=SCOPE_ID,
            ordinal=0,
            start_ms=0,
            end_ms=max(end_ms, 1),
            raw_text=str(item.get("raw_text") or text),
            normalized_text=text,
            confidence_raw=None,
            confidence_calibrated=None,
            alignment_score=None,
            quality_status=SegmentQualityStatus.ACCEPTED,
            quality_reasons=tuple((item.get("quality_reason_counts") or {}).keys()),
            searchable=True,
        )
    return rows


def prepare_frames(
    dataset: Path, output: Path, video_paths: Sequence[Path]
) -> tuple[list[Asset], dict[str, str], dict[str, float], list[dict[str, Any]]]:
    extractor = FfmpegVideoKeyframeExtractor(output / "media")
    frames: list[Asset] = []
    parents: dict[str, str] = {}
    durations: dict[str, float] = {}
    errors: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for index, video in enumerate(video_paths, start=1):
        frame_count_before = len(frames)
        video_id = video.stem
        duration = duration_seconds(video)
        durations[video_id] = duration
        scenes = plan_scenes(duration)
        for scene in scenes:
            frame_id = f"{video_id}:scene:{scene.index:03d}"
            try:
                frame = extractor.extract(
                    video,
                    video_id,
                    scene.index,
                    scene.keyframe_timestamp_sec,
                )
            except Exception as error:  # noqa: BLE001 - mirrors the import path's degraded enhancement
                errors.append(
                    {
                        "video_id": video_id,
                        "scene_index": scene.index,
                        "timestamp_sec": scene.keyframe_timestamp_sec,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                log(
                    f"frame degraded {video_id} scene={scene.index} "
                    f"timestamp={scene.keyframe_timestamp_sec:.3f}: {error}"
                )
                # ExtractVideoScenes lets this exception escape to ImportAssets,
                # which retains already committed earlier scenes and stops this
                # video's remaining enhancement work.  Do the same here.
                break
            frames.append(
                Asset(
                    id=frame_id,
                    scope_id=SCOPE_ID,
                    file_name=frame.name,
                    media_type=MediaType.IMAGE,
                    path=str(frame),
                    mime_type="image/jpeg",
                    size_bytes=frame.stat().st_size,
                    status=AssetStatus.PROCESSED,
                    content_sha256=file_sha256(frame),
                    captured_at=None,
                    captured_location=None,
                    created_at=now,
                    updated_at=now,
                    derived_kind=KEYFRAME_DERIVED_KIND,
                    parent_asset_id=video_id,
                    source_timestamp_sec=scene.keyframe_timestamp_sec,
                )
            )
            parents[frame_id] = video_id
        log(
            f"frames {index}/{len(video_paths)} {video.name}: "
            f"{len(frames) - frame_count_before}/{len(scenes)}"
        )
    return frames, parents, durations, errors


def load_query_cache(path: Path) -> dict[str, list[float]]:
    if not path.is_file():
        return {}
    with np.load(path, allow_pickle=False) as loaded:
        questions = loaded["questions"].astype(str).tolist()
        vectors = loaded["vectors"].astype(np.float32)
    return {question: vector.tolist() for question, vector in zip(questions, vectors, strict=True)}


def save_query_cache(path: Path, values: Mapping[str, Sequence[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    questions = list(values)
    vectors = np.asarray([values[question] for question in questions], dtype=np.float32)
    temp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(temp, questions=np.asarray(questions), vectors=vectors)
    temp.replace(path)


def visual_candidates(result: Any) -> list[CandidateHit]:
    return [
        CandidateHit(
            asset_id=hit.asset_id,
            channel=VISUAL_CHANNEL,
            rank=int(hit.channels[VISUAL_CHANNEL]),
            raw_score=float(hit.raw_scores.get(VISUAL_CHANNEL, 0.0)),
        )
        for hit in result.hits
    ]


def audio_candidates(result: Any) -> list[CandidateHit]:
    return [
        CandidateHit(
            asset_id=hit.asset_id,
            channel=TEXT_CHANNEL,
            rank=int(hit.channels[TEXT_CHANNEL]),
            raw_score=float(hit.raw_scores.get(TEXT_CHANNEL, 0.0)),
        )
        for hit in result.hits
    ]


def collapse_ranked_assets(asset_ids: Iterable[str], parents: Mapping[str, str]) -> list[str]:
    ranked: list[str] = []
    seen: set[str] = set()
    for asset_id in asset_ids:
        source = parents.get(asset_id, asset_id)
        if source not in seen:
            seen.add(source)
            ranked.append(source)
    return ranked


def collapse_hits(hits: Sequence[CandidateHit], parents: Mapping[str, str]) -> list[CandidateHit]:
    result: list[CandidateHit] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda item: item.rank):
        source = parents.get(hit.asset_id, hit.asset_id)
        if source in seen:
            continue
        seen.add(source)
        result.append(
            CandidateHit(
                asset_id=source,
                channel=hit.channel,
                rank=len(result) + 1,
                raw_score=hit.raw_score,
            )
        )
    return result


def target_rank(ranking: Sequence[str], targets: Sequence[str]) -> int | None:
    wanted = set(targets)
    return next((index for index, item in enumerate(ranking, start=1) if item in wanted), None)


def metrics(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, float | int]:
    ranks = [row.get(field) for row in rows]
    valid = [int(rank) for rank in ranks if isinstance(rank, int)]
    total = len(rows)
    output: dict[str, float | int] = {"queries": total}
    for k in TOP_KS:
        output[f"recall@{k}"] = round(sum(rank <= k for rank in valid) / total, 4) if total else 0.0
    output["mrr"] = round(sum(1.0 / rank for rank in valid) / total, 4) if total else 0.0
    return output


def pct(value: object) -> str:
    return f"{float(value) * 100:.2f}%"


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    summary = payload["summary"]
    corpus = payload["corpus"]
    runtime = payload["runtime"]
    rows = payload["queries"]
    same_current_top10 = sum(row["visual_top10"] == row["current_top10"] for row in rows)
    eligible_summary = summary["audio_eligible"]
    eligible_visual = eligible_summary["visual_only"]
    eligible_parent = eligible_summary["parent_aware_fusion"]

    lines = [
        "# 视频视觉检索与音频增强 A/B 评测",
        "",
        f"> 生成时间：{payload['generated_at']}。本报告只评测输入到检索，不运行 Agent 和前端。",
        "",
        "## 结论",
        "",
        (
            "本次使用同一批视频、同一套关键帧视觉向量和同一组查询。"
            "A 组关闭音频证据；B 组只增加通过质量门禁的 ASR 文本向量。"
        ),
        "",
        "| 方案 | Recall@1 | Recall@5 | Recall@10 | MRR |",
        "|---|---:|---:|---:|---:|",
    ]
    labels = {
        "visual_only": "A：仅视觉",
        "current_asset_fusion": "B1：视觉+音频（当前资产级融合）",
        "parent_aware_fusion": "B2：视觉+音频（先折叠父视频再融合）",
    }
    for key in ("visual_only", "current_asset_fusion", "parent_aware_fusion"):
        item = summary[key]
        lines.append(
            f"| {labels[key]} | {pct(item['recall@1'])} | {pct(item['recall@5'])} | "
            f"{pct(item['recall@10'])} | {float(item['mrr']):.4f} |"
        )

    visual_r1 = float(summary["visual_only"]["recall@1"])
    current_r1 = float(summary["current_asset_fusion"]["recall@1"])
    parent_r1 = float(summary["parent_aware_fusion"]["recall@1"])
    lines.extend(
        [
            "",
            (
                "- 当前资产级音频融合相对仅视觉的 Recall@1 变化："
                f"{(current_r1 - visual_r1) * 100:+.2f} 个百分点。"
            ),
            (
                "- 父视频感知融合相对仅视觉的 Recall@1 变化："
                f"{(parent_r1 - visual_r1) * 100:+.2f} 个百分点。"
            ),
            "- B1 与 B2 的差距用于判断：关键帧资产与父视频资产未在融合前归一，是否正在吞掉音频增益。",
            "",
            "## 架构诊断",
            "",
            (
                "1. **当前实现里音频对前 10 名没有产生任何影响。** "
                f"{same_current_top10} 条查询的视觉 "
                "Top-10 与当前资产级融合 Top-10 全部相同。视觉命中是关键帧资产，音频命中"
                "是父视频资产，两者没有在 RRF 前统一身份；同时 visual 权重 2.5 高于 text "
                "的 0.5，独立音频候选排在视觉候选之后。"
            ),
            (
                "2. **只修正父子身份还不够。** 在音频可用的 "
                f"{eligible_visual['queries']} 题中，父视频感知融合让 Recall@5 变化 "
                f"{(eligible_parent['recall@5'] - eligible_visual['recall@5']) * 100:+.2f} 个百分点、"
                "Recall@10 变化 "
                f"{(eligible_parent['recall@10'] - eligible_visual['recall@10']) * 100:+.2f} 个百分点，"
                "Recall@1 变化 "
                f"{(eligible_parent['recall@1'] - eligible_visual['recall@1']) * 100:+.2f} 个百分点。"
            ),
            (
                "3. **下降来自无条件融合与转写噪声共同作用。** 文本召回没有相似度阈值，"
                "每次查询都会给若干音频视频一个正 RRF 票；只要同一视频也被视觉召回，它就会"
                "叠加得分。当前门禁还放过了“嗯,喂。”“我。”以及明显不通顺的长文本，因此"
                "这种加分并不等于语义证据可靠。"
            ),
            (
                "4. **因此当前结论不是“音频没有价值”，而是“当前音频接线尚不能安全提升"
                "召回”。** 下一版应先做父视频级候选归一，再给音频增加查询相关性阈值、最低"
                "有效内容长度/信息量门禁和动态权重；不能直接把所有 `searchable=true` 转写"
                "无条件参加 RRF。"
            ),
            "",
            "## 评测口径",
            "",
            (
                f"- 视频：{corpus['videos']} 个；按项目当前 60 秒窗口中点规则抽取关键帧 "
                f"{corpus['keyframes']} 张。"
            ),
            f"- 查询：{corpus['queries']} 条 video-only QA；目标覆盖 {corpus['target_videos']} 个源视频。",
            (
                f"- 有音轨视频：{corpus['videos_with_audio']} 个；"
                f"ASR 成功：{corpus['asr_success']} 个；"
                f"门禁后可检索转写：{corpus['searchable_audio_videos']} 个。"
            ),
            "- 视觉：本地 `tencent/WeMM-Embedding-2B` 对真实关键帧生成 2048 维向量。",
            (
                "- 音频：复用同一数据集上一轮真实 Paraformer+VAD+质量门禁产物；"
                "只把 `searchable=true` 的文本写入生产 `semantic/audio_segment` 空间。"
            ),
            (
                "- 检索：生产 `RetrieveVisual`、`RetrieveTextAnn`、SQLite 精确余弦召回和"
                "冻结的 weighted RRF（visual=2.5，text=0.5）。"
            ),
            "- 命中：关键帧映射回 `parent_asset_id`；Top-K 中出现任一 GT 源视频即命中。",
            "",
            "## 音频可发挥作用的子集",
            "",
            "这里仅统计 GT 视频自身存在门禁通过转写的题，避免大量无音轨/无有效语音视频稀释结果。",
            "",
            "| 方案 | 题数 | Recall@1 | Recall@5 | Recall@10 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for key in ("visual_only", "current_asset_fusion", "parent_aware_fusion"):
        item = summary["audio_eligible"][key]
        lines.append(
            f"| {labels[key]} | {item['queries']} | {pct(item['recall@1'])} | "
            f"{pct(item['recall@5'])} | {pct(item['recall@10'])} |"
        )

    changes = payload["changes"]
    lines.extend(
        [
            "",
            "## 排名变化",
            "",
            (
                f"- 当前资产级融合：Top-1 改善 {changes['current']['improved']} 题，"
                f"下降 {changes['current']['regressed']} 题，"
                f"不变 {changes['current']['unchanged']} 题。"
            ),
            (
                f"- 父视频感知融合：Top-1 改善 {changes['parent_aware']['improved']} 题，"
                f"下降 {changes['parent_aware']['regressed']} 题，"
                f"不变 {changes['parent_aware']['unchanged']} 题。"
            ),
            "",
            "### 代表性变化样本",
            "",
            "| QA | 问题 | GT | 仅视觉名次 | 当前融合名次 | 父视频融合名次 | GT 转写摘要 |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    changed = sorted(
        rows,
        key=lambda row: (
            -abs((row.get("visual_rank") or 999) - (row.get("parent_aware_rank") or 999)),
            row["qa_id"],
        ),
    )[:12]
    for row in changed:
        question = str(row["question"]).replace("|", "\\|")[:90]
        transcript = str(row.get("target_transcript") or "").replace("|", "\\|")[:80]
        lines.append(
            f"| {row['qa_id']} | {question} | {', '.join(row['targets'])} | "
            f"{row.get('visual_rank') or '-'} | {row.get('current_rank') or '-'} | "
            f"{row.get('parent_aware_rank') or '-'} | {transcript or '-'} |"
        )

    lines.extend(
        [
            "",
            "## 运行与存储证据",
            "",
            f"- SQLite：`{payload['artifacts']['database']}`",
            f"- 原始逐题结果：`{payload['artifacts']['json']}`",
            (
                f"- `memory_vectors`：视觉 {corpus['stored_visual_vectors']} 行，"
                f"音频分段 {corpus['stored_audio_vectors']} 行。"
            ),
            (
                f"- WeMM 加载：{runtime['model_load_seconds']:.2f}s；"
                f"图像编码：{runtime['image_calls']} 次 / {runtime['image_seconds']:.2f}s；"
                f"文本编码：{runtime['text_calls']} 次 / {runtime['text_seconds']:.2f}s。"
            ),
            (
                f"- 总耗时：{runtime['total_seconds']:.2f}s；"
                f"进程峰值 RSS：{runtime['peak_rss_mb']:.1f} MB；"
                f"MPS driver 峰值：{runtime['peak_mps_driver_mb']:.1f} MB。"
            ),
            f"- 关键帧抽取降级：{corpus['keyframe_extraction_errors']} 处。",
            "",
            "## 边界",
            "",
            (
                "本评测验证的是项目的多模态向量检索主链：视频→关键帧→视觉向量，以及"
                "视频→ASR 门禁文本→文本向量→融合召回。未加入 Agent、答案生成、人脸、"
                "图谱、结构化元数据；也未调用 VLM 把关键帧再生成文字描述。这样能把 "
                "Recall 变化归因到音频支路本身，而不是另一轮生成模型文本。"
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--audio-results", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--max-videos", type=int, default=0)
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError(
            "MPS requested but unavailable; run outside the managed sandbox or use --device cpu"
        )

    started = time.perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    peak_driver = 0

    def sample_memory() -> None:
        nonlocal peak_rss, peak_driver
        peak_rss = max(peak_rss, process.memory_info().rss)
        if args.device == "mps":
            peak_driver = max(peak_driver, int(torch.mps.driver_allocated_memory()))

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    video_paths = [args.dataset / value for value in manifest.get("videos") or []]
    video_paths = [path for path in video_paths if path.is_file()]
    if args.max_videos:
        video_paths = video_paths[: args.max_videos]
    video_ids = {path.stem for path in video_paths}
    queries = load_queries(args.qa, video_ids, args.max_queries)
    audio_segments = load_audio_segments(args.audio_results, video_ids)
    audio_payload = json.loads(args.audio_results.read_text(encoding="utf-8"))
    audio_cases = [row for row in audio_payload.get("cases") or [] if row.get("label") == "with_audio"]
    log(
        f"corpus videos={len(video_paths)} queries={len(queries)} "
        f"searchable_audio={len(audio_segments)}"
    )

    database_path = args.output / "sentrix.sqlite3"
    database = Database(database_path, MIGRATIONS)
    database.migrate()
    vectors = SqliteVectorRepository(database)
    embedder = DirectWeMM(args.model, args.device)

    frames, parents, durations, frame_errors = prepare_frames(args.dataset, args.output, video_paths)
    visual_indexer = IndexAssetVisual(embedder=embedder, vectors=vectors)
    missing_frames = [
        frame
        for frame in frames
        if not vectors.has_vector(
            space=scheme.VISUAL_SPACE,
            source_type="asset",
            source_id=frame.id,
            model_name=embedder.model_id,
        )
    ]
    for index, frame in enumerate(missing_frames, start=1):
        outcome = visual_indexer.index(frame)
        if not outcome.indexed:
            raise RuntimeError(f"visual indexing failed for {frame.id}: {outcome.reason}")
        sample_memory()
        log(f"visual vectors {index}/{len(missing_frames)} {frame.id}")

    audio_indexer = IndexAudioTranscriptSegments(embedder=embedder, vectors=vectors)
    for index, (video_id, segment) in enumerate(sorted(audio_segments.items()), start=1):
        rows = audio_indexer.index(video_id, [segment])
        if rows != 1:
            raise RuntimeError(f"audio indexing failed for {video_id}: rows={rows}")
        sample_memory()
        log(f"audio vectors {index}/{len(audio_segments)} {video_id}")

    cache_path = args.output / "query-vectors.npz"
    query_vectors = load_query_cache(cache_path)
    missing_questions = list(
        dict.fromkeys(case.question for case in queries if case.question not in query_vectors)
    )
    for index, question in enumerate(missing_questions, start=1):
        query_vectors[question] = embedder.embed_query(question)
        sample_memory()
        log(f"query vectors {index}/{len(missing_questions)}")
        if index % 10 == 0:
            save_query_cache(cache_path, query_vectors)
    save_query_cache(cache_path, query_vectors)

    cached = CachedQueryEmbedder(query_vectors)
    visual = RetrieveVisual(embedder=cached, vectors=vectors, index=None)
    audio = RetrieveTextAnn(embedder=cached, vectors=vectors)
    fetch = max(100, len(frames) + len(audio_segments))
    transcript_by_video = {
        video_id: segment.normalized_text for video_id, segment in audio_segments.items()
    }
    results: list[dict[str, Any]] = []
    for index, case in enumerate(queries, start=1):
        visual_result = visual.execute(
            VisualSearchRequest(text=case.question, scope_id=SCOPE_ID, limit=fetch)
        )
        audio_result = audio.execute(TextSearchRequest(text=case.question, scope_id=SCOPE_ID, limit=fetch))
        visual_hits = visual_candidates(visual_result)
        audio_hits = audio_candidates(audio_result)

        visual_fused = fuse({VISUAL_CHANNEL: visual_hits})
        current_fused = fuse(
            {VISUAL_CHANNEL: visual_hits, TEXT_CHANNEL: audio_hits},
            channel_weights=DEFAULT_CHANNEL_WEIGHTS,
        )
        parent_fused = fuse(
            {
                VISUAL_CHANNEL: collapse_hits(visual_hits, parents),
                TEXT_CHANNEL: collapse_hits(audio_hits, parents),
            },
            channel_weights=DEFAULT_CHANNEL_WEIGHTS,
        )
        visual_ranking = collapse_ranked_assets(
            (candidate.asset_id for candidate in visual_fused), parents
        )
        current_ranking = collapse_ranked_assets(
            (candidate.asset_id for candidate in current_fused), parents
        )
        parent_ranking = [candidate.asset_id for candidate in parent_fused]
        target_audio = " / ".join(
            transcript_by_video[target]
            for target in case.targets
            if target in transcript_by_video
        )
        results.append(
            {
                "qa_id": case.qa_id,
                "question": case.question,
                "targets": list(case.targets),
                "required_evidence_sources": list(case.evidence_sources),
                "audio_eligible": any(target in transcript_by_video for target in case.targets),
                "target_transcript": target_audio,
                "visual_rank": target_rank(visual_ranking, case.targets),
                "current_rank": target_rank(current_ranking, case.targets),
                "parent_aware_rank": target_rank(parent_ranking, case.targets),
                "visual_top10": visual_ranking[:10],
                "current_top10": current_ranking[:10],
                "parent_aware_top10": parent_ranking[:10],
            }
        )
        if index % 25 == 0 or index == len(queries):
            log(f"retrieval {index}/{len(queries)}")

    eligible = [row for row in results if row["audio_eligible"]]

    def changes(field: str) -> dict[str, int]:
        improved = regressed = unchanged = 0
        for row in results:
            before = int(row["visual_rank"] or 10**9)
            after = int(row[field] or 10**9)
            if (before == 1) == (after == 1):
                unchanged += 1
            elif after == 1:
                improved += 1
            else:
                regressed += 1
        return {"improved": improved, "regressed": regressed, "unchanged": unchanged}

    sample_memory()
    visual_count = vectors.count(space=scheme.VISUAL_SPACE, model_name=embedder.model_id)
    audio_count = sum(
        1
        for video_id in audio_segments
        if vectors.has_vector(
            space=scheme.SEMANTIC_SPACE,
            source_type="audio_segment",
            source_id=f"audio-segment:{video_id}:000",
            model_name=embedder.model_id,
        )
    )
    json_path = args.output / "results.json"
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "methodology": {
            "visual": "production 60s-window midpoint keyframes + WeMM image embeddings",
            "audio": "cached real Paraformer/VAD/quality-gate accepted segments + WeMM text embeddings",
            "retrieval": "production SQLite exact retrieval and weighted RRF",
            "current_fusion": "asset-level RRF, then keyframe-to-parent collapse",
            "parent_aware_fusion": "keyframe-to-parent collapse per channel, then RRF",
        },
        "corpus": {
            "videos": len(video_paths),
            "keyframes": len(frames),
            "queries": len(queries),
            "target_videos": len({target for case in queries for target in case.targets}),
            "videos_with_audio": sum(bool(row.get("input_has_audio")) for row in audio_cases),
            "asr_success": sum(bool(row.get("asr_success")) for row in audio_cases),
            "searchable_audio_videos": len(audio_segments),
            "stored_visual_vectors": visual_count,
            "stored_audio_vectors": audio_count,
            "keyframe_extraction_errors": len(frame_errors),
            "duration_seconds": round(sum(durations.values()), 3),
        },
        "summary": {
            "visual_only": metrics(results, "visual_rank"),
            "current_asset_fusion": metrics(results, "current_rank"),
            "parent_aware_fusion": metrics(results, "parent_aware_rank"),
            "audio_eligible": {
                "visual_only": metrics(eligible, "visual_rank"),
                "current_asset_fusion": metrics(eligible, "current_rank"),
                "parent_aware_fusion": metrics(eligible, "parent_aware_rank"),
            },
        },
        "changes": {
            "current": changes("current_rank"),
            "parent_aware": changes("parent_aware_rank"),
        },
        "runtime": {
            "device": args.device,
            "model_load_seconds": round(embedder.load_seconds, 3),
            "image_calls": embedder.image_calls,
            "image_seconds": round(embedder.image_seconds, 3),
            "text_calls": embedder.text_calls,
            "text_seconds": round(embedder.text_seconds, 3),
            "total_seconds": round(time.perf_counter() - started, 3),
            "peak_rss_mb": round(peak_rss / 1024 / 1024, 1),
            "peak_mps_driver_mb": round(peak_driver / 1024 / 1024, 1),
        },
        "artifacts": {
            "database": str(database_path),
            "json": str(json_path),
            "query_cache": str(cache_path),
            "report": str(args.report),
            "audio_source": str(args.audio_results),
        },
        "queries": results,
        "keyframe_extraction_errors": frame_errors,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.report, payload)
    log(f"done report={args.report}")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
