#!/usr/bin/env python3
"""Benchmark the video ingest/retrieval path with and without an audio track.

The paired arm removes only the audio stream (``-c:v copy -an``), so picture
content, duration, codec, and keyframe plan remain identical.  The benchmark
uses the real ffprobe/ffmpeg adapters, real FunASR service implementation,
quality gate, SQLite evidence/index repositories, and retrieval use cases.

VLM/visual embedding, face recognition, agent answering, and browser-preview
transcoding are intentionally outside this benchmark: they are invariant in
the paired comparison and would obscure the cost and behavior of the new audio
branch.  Keyframe extraction and scene/asset persistence remain enabled.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api" / "src"
for entry in (str(ROOT), str(API_SRC)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import psutil  # noqa: E402

from scripts.maintenance.funasr_sidecar import Service  # noqa: E402
from sentrix.application.media.extract_video_scenes import ExtractVideoScenes  # noqa: E402
from sentrix.application.media.import_assets import ImportAssets, ImportRequest  # noqa: E402
from sentrix.application.media.transcribe_audio import TranscribeAudio  # noqa: E402
from sentrix.application.retrieval.index_audio_transcript import (  # noqa: E402
    IndexAudioTranscriptSegments,
)
from sentrix.application.retrieval.index_observation_text import IndexObservationText  # noqa: E402
from sentrix.application.retrieval.retrieve_text_ann import (  # noqa: E402
    RetrieveTextAnn,
    TextSearchRequest,
)
from sentrix.domain.media.scenes import plan_scenes  # noqa: E402
from sentrix.domain.media.transcription import AsrResult  # noqa: E402
from sentrix.infrastructure.media.asr_sidecar import FunAsrSidecarTranscriber  # noqa: E402
from sentrix.infrastructure.media.storage import FileSystemMediaStorage  # noqa: E402
from sentrix.infrastructure.media.video_keyframes import FfmpegVideoKeyframeExtractor  # noqa: E402
from sentrix.infrastructure.media.video_metadata import FfprobeVideoMetadataReader  # noqa: E402
from sentrix.infrastructure.persistence.database import Database  # noqa: E402
from sentrix.infrastructure.persistence.repositories.asset_places import (  # noqa: E402
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (  # noqa: E402
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.audio_transcriptions import (  # noqa: E402
    SqliteAudioTranscriptionRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository  # noqa: E402
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex  # noqa: E402
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository  # noqa: E402
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository  # noqa: E402
from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository  # noqa: E402

MIGRATIONS = API_SRC / "sentrix" / "infrastructure" / "persistence" / "migrations"
DEFAULT_DATASET = ROOT / "album3-max-video-keyframe" / "videos"


class UnusedImageMetadata:
    def read(self, _path: Path) -> tuple[None, None, None]:
        return None, None, None


class HashingTextEmbedder:
    """Deterministic local text projection used only to verify vector plumbing.

    The ASR model is real.  Replacing WeMM here keeps this A/B benchmark focused
    on the audio branch and avoids loading another 2B model whose work is equal
    on both sides.  Exact retrieval of an indexed segment still has cosine 1.
    """

    model_id = "benchmark-hashing-text-v1"
    dimension = 256

    def embed_query(self, text: str) -> list[float]:
        value = str(text or "").strip()
        if not value:
            return []
        vector = [0.0] * self.dimension
        tokens = [char.lower() for char in value if not char.isspace()]
        if not tokens:
            return []
        for index, token in enumerate(tokens):
            digest = hashlib.blake2b(f"{index % 3}:{token}".encode(), digest_size=8).digest()
            slot = int.from_bytes(digest, "little") % self.dimension
            vector[slot] += -1.0 if digest[0] & 1 else 1.0
        norm = math.sqrt(sum(item * item for item in vector)) or 1.0
        return [item / norm for item in vector]


class DirectFunAsr:
    """Use the production sidecar core in-process and expose domain results."""

    available = True
    load_error = ""

    def __init__(self, service: Service) -> None:
        self._service = service
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, path: Path) -> AsrResult:
        started = time.perf_counter()
        record: dict[str, Any] = {"path": str(path), "success": False}
        try:
            payload = self._service.transcribe(path)
            result = FunAsrSidecarTranscriber._parse(payload)
            record["success"] = True
            record["raw_chars"] = len(result.raw_text)
            record["segments"] = len(result.segments)
            return result
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 3)
            self.calls.append(record)


class ResourceSampler:
    def __init__(self, interval_s: float = 0.1) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.start_rss_mb = 0.0
        self.peak_rss_mb = 0.0
        self.start_mps_allocated_mb = 0.0
        self.peak_mps_allocated_mb = 0.0
        self.peak_mps_driver_mb = 0.0
        self.rss_includes_children = True

    def _rss_mb(self) -> float:
        process = psutil.Process()
        rss = process.memory_info().rss
        try:
            children = process.children(recursive=True)
        except (PermissionError, psutil.AccessDenied):
            # Managed macOS sandboxes can read the current process but deny the
            # sysctl call psutil uses to enumerate every PID.
            self.rss_includes_children = False
            children = ()
        for child in children:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                rss += child.memory_info().rss
        return rss / (1024 * 1024)

    @staticmethod
    def _mps_mb(kind: str) -> float:
        try:
            import torch

            if not torch.backends.mps.is_available():
                return 0.0
            value = (
                torch.mps.current_allocated_memory()
                if kind == "allocated"
                else torch.mps.driver_allocated_memory()
            )
            return float(value) / (1024 * 1024)
        except Exception:
            return 0.0

    def __enter__(self) -> ResourceSampler:
        self.start_rss_mb = self._rss_mb()
        self.peak_rss_mb = self.start_rss_mb
        self.start_mps_allocated_mb = self._mps_mb("allocated")
        self.peak_mps_allocated_mb = self.start_mps_allocated_mb
        self.peak_mps_driver_mb = self._mps_mb("driver")

        def sample() -> None:
            while not self._stop.wait(self._interval_s):
                self.peak_rss_mb = max(self.peak_rss_mb, self._rss_mb())
                self.peak_mps_allocated_mb = max(self.peak_mps_allocated_mb, self._mps_mb("allocated"))
                self.peak_mps_driver_mb = max(self.peak_mps_driver_mb, self._mps_mb("driver"))

        self._thread = threading.Thread(target=sample, name="resource-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.peak_rss_mb = max(self.peak_rss_mb, self._rss_mb())
        self.peak_mps_allocated_mb = max(self.peak_mps_allocated_mb, self._mps_mb("allocated"))
        self.peak_mps_driver_mb = max(self.peak_mps_driver_mb, self._mps_mb("driver"))

    def result(self) -> dict[str, float]:
        return {
            "start_rss_mb": round(self.start_rss_mb, 1),
            "peak_rss_mb": round(self.peak_rss_mb, 1),
            "rss_delta_mb": round(max(0.0, self.peak_rss_mb - self.start_rss_mb), 1),
            "start_mps_allocated_mb": round(self.start_mps_allocated_mb, 1),
            "peak_mps_allocated_mb": round(self.peak_mps_allocated_mb, 1),
            "peak_mps_driver_mb": round(self.peak_mps_driver_mb, 1),
            "rss_scope": "process_tree" if self.rss_includes_children else "main_process",
        }


@dataclass
class Runtime:
    database: Database
    importer: ImportAssets
    assets: SqliteAssetRepository
    observations: SqliteObservationRepository
    transcriptions: SqliteAudioTranscriptionRepository
    vectors: SqliteVectorRepository
    lexical: SqliteLexicalIndex
    retrieval: RetrieveTextAnn
    video: SqliteVideoRepository
    asr: DirectFunAsr


def build_runtime(root: Path, service: Service) -> Runtime:
    database = Database(root / "sentrix.sqlite3", MIGRATIONS)
    database.migrate()
    assets = SqliteAssetRepository(database)
    observations = SqliteObservationRepository(database)
    transcriptions = SqliteAudioTranscriptionRepository(database)
    vectors = SqliteVectorRepository(database)
    places = SqliteAssetPlaceRepository(database)
    events = SqliteEventRepository(database)
    video = SqliteVideoRepository(database)
    lexical = SqliteLexicalIndex(database, observations, assets=assets, places=places)
    embedder = HashingTextEmbedder()
    direct_asr = DirectFunAsr(service)
    audio = TranscribeAudio(
        transcriber=direct_asr,
        transcriptions=transcriptions,
        observations=observations,
        segment_indexer=IndexAudioTranscriptSegments(embedder=embedder, vectors=vectors).index,
    )
    text_index = IndexObservationText(embedder=embedder, vectors=vectors)

    def index_text(observation: Any, event_id: str | None) -> int:
        return text_index.index(observation, event_id).rows

    common: dict[str, Any] = {
        "assets": assets,
        "observations": observations,
        "storage": FileSystemMediaStorage(root / "media"),
        "metadata": UnusedImageMetadata(),
        "text_indexer": index_text,
        "lexical_index": lexical,
        "spaces": SqliteSpaceRepository(database),
        "video_metadata": FfprobeVideoMetadataReader(),
        # Preview transcoding is delivery work, not part of the retrieval path.
        "video_transcoder": None,
        "video_repository": video,
        "audio_transcriber": audio,
        "event_attacher": lambda event_id, observation: events.attach(event_id, observation.id),
    }
    keyframe_importer = ImportAssets(**common)
    extractor = ExtractVideoScenes(
        assets=assets,
        events=events,
        places=places,
        video_repository=video,
        keyframes=FfmpegVideoKeyframeExtractor(root / "media"),
        process_image=lambda asset, event_id: keyframe_importer.process_image(
            asset, forced_event_id=event_id
        ),
    )
    importer = ImportAssets(**common, video_scene_extractor=extractor.execute)
    return Runtime(
        database=database,
        importer=importer,
        assets=assets,
        observations=observations,
        transcriptions=transcriptions,
        vectors=vectors,
        lexical=lexical,
        retrieval=RetrieveTextAnn(embedder=embedder, vectors=vectors),
        video=video,
        asr=direct_asr,
    )


def probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    return {
        "duration_s": float(payload["format"].get("duration") or 0),
        "size_bytes": int(payload["format"].get("size") or path.stat().st_size),
        "video_codec": str(video.get("codec_name") or ""),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def make_muted(source: Path, target: Path) -> float:
    started = time.perf_counter()
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise RuntimeError(completed.stderr.strip() or "failed to create muted pair")
    return round(time.perf_counter() - started, 3)


def _query_text(segments: list[Any]) -> str:
    for segment in segments:
        if segment.searchable and segment.normalized_text:
            return segment.normalized_text
    return ""


def run_case(
    runtime: Runtime,
    *,
    source: Path,
    label: str,
    scope_id: str,
    source_name: str,
    generation_seconds: float = 0.0,
) -> dict[str, Any]:
    metadata = probe(source)
    content = source.read_bytes()
    call_offset = len(runtime.asr.calls)
    started = time.perf_counter()
    with ResourceSampler() as resources:
        result = runtime.importer.execute(
            ImportRequest(
                scope_id=scope_id,
                file_name=f"{Path(source_name).stem}__{label}.mp4",
                content_type="video/mp4",
                content=content,
            )
        )
    elapsed = time.perf_counter() - started
    asr_call = runtime.asr.calls[call_offset] if len(runtime.asr.calls) > call_offset else {}
    transcription = runtime.transcriptions.latest_for_asset(result.asset_id)
    segments = (
        list(runtime.transcriptions.list_segments(transcription.id)) if transcription is not None else []
    )
    query = _query_text(segments)
    lexical_hit = False
    vector_hit = False
    vector_start_ms: int | None = None
    vector_end_ms: int | None = None
    if query:
        lexical_hit = any(
            asset_id == result.asset_id
            for asset_id, _score in runtime.lexical.search_fts(query, scope_id=scope_id, limit=20)
        )
        vector_result = runtime.retrieval.execute(TextSearchRequest(text=query, scope_id=scope_id, limit=20))
        vector_match = next((item for item in vector_result.hits if item.asset_id == result.asset_id), None)
        if vector_match is not None:
            vector_hit = True
            vector_start_ms = vector_match.start_ms
            vector_end_ms = vector_match.end_ms

    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for segment in segments:
        status_counts[segment.quality_status.value] = status_counts.get(segment.quality_status.value, 0) + 1
        for reason in segment.quality_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    with runtime.database.connect() as connection:
        vector_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_vectors WHERE source_type = 'audio_segment' "
                "AND json_extract(metadata_json, '$.asset_id') = ?",
                (result.asset_id,),
            ).fetchone()[0]
        )
        fts_transcript_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM observation_search_terms WHERE asset_id = ? "
                "AND field_type = 'transcript'",
                (result.asset_id,),
            ).fetchone()[0]
        )

    timings = dict(result.timings)
    return {
        "source_name": source_name,
        "label": label,
        "scope_id": scope_id,
        "input_has_audio": metadata["has_audio"],
        "duration_s": round(metadata["duration_s"], 3),
        "size_bytes": metadata["size_bytes"],
        "video_codec": metadata["video_codec"],
        "muted_generation_seconds": generation_seconds,
        "asset_id": result.asset_id,
        "video_probed": result.video_probed,
        "planned_scenes": len(plan_scenes(metadata["duration_s"])),
        "video_scenes": result.video_scenes,
        "derived_keyframes": len(runtime.video.list_derived(result.asset_id)),
        "audio_transcription_status": result.audio_transcription_status,
        "asr_seconds": asr_call.get("seconds"),
        "asr_success": asr_call.get("success", False),
        "asr_error": asr_call.get("error", ""),
        "raw_text": transcription.raw_text if transcription is not None else "",
        "accepted_text": transcription.accepted_text if transcription is not None else "",
        "raw_chars": len(transcription.raw_text) if transcription is not None else 0,
        "accepted_chars": len(transcription.accepted_text) if transcription is not None else 0,
        "segment_count": len(segments),
        "searchable_segments": sum(bool(item.searchable) for item in segments),
        "quality_status_counts": status_counts,
        "quality_reason_counts": reason_counts,
        "audio_vector_rows": vector_rows,
        "fts_transcript_rows": fts_transcript_rows,
        "retrieval_query": query,
        "lexical_retrievable": lexical_hit,
        "vector_retrievable": vector_hit,
        "vector_start_ms": vector_start_ms,
        "vector_end_ms": vector_end_ms,
        "total_seconds": round(elapsed, 3),
        "pipeline_timings": timings,
        "resources": resources.result(),
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    selected = [row for row in rows if row["label"] == label]
    total_s = [float(row["total_seconds"]) for row in selected]
    asr_s = [float(row["asr_seconds"]) for row in selected if row["asr_seconds"] is not None]
    return {
        "label": label,
        "videos": len(selected),
        "media_duration_s": round(sum(float(row["duration_s"]) for row in selected), 3),
        "wall_seconds": round(sum(total_s), 3),
        "mean_seconds": round(sum(total_s) / len(total_s), 3) if total_s else 0.0,
        "p50_seconds": round(percentile(total_s, 0.50), 3),
        "p95_seconds": round(percentile(total_s, 0.95), 3),
        "asr_seconds": round(sum(asr_s), 3),
        "asr_success": sum(bool(row["asr_success"]) for row in selected),
        "completed": sum(row["audio_transcription_status"] == "completed" for row in selected),
        "review": sum(row["audio_transcription_status"] == "review" for row in selected),
        "failed": sum(row["audio_transcription_status"] == "failed" for row in selected),
        "nonempty_raw": sum(bool(row["raw_text"].strip()) for row in selected),
        "nonempty_accepted": sum(bool(row["accepted_text"].strip()) for row in selected),
        "segments": sum(int(row["segment_count"]) for row in selected),
        "searchable_segments": sum(int(row["searchable_segments"]) for row in selected),
        "audio_vector_rows": sum(int(row["audio_vector_rows"]) for row in selected),
        "fts_transcript_rows": sum(int(row["fts_transcript_rows"]) for row in selected),
        "lexical_retrievable": sum(bool(row["lexical_retrievable"]) for row in selected),
        "vector_retrievable": sum(bool(row["vector_retrievable"]) for row in selected),
        "video_scenes": sum(int(row["video_scenes"]) for row in selected),
        "planned_scenes": sum(int(row.get("planned_scenes", 0)) for row in selected),
        "derived_keyframes": sum(int(row["derived_keyframes"]) for row in selected),
        "peak_rss_mb": round(max((row["resources"]["peak_rss_mb"] for row in selected), default=0), 1),
        "peak_mps_allocated_mb": round(
            max((row["resources"]["peak_mps_allocated_mb"] for row in selected), default=0), 1
        ),
        "peak_mps_driver_mb": round(
            max((row["resources"]["peak_mps_driver_mb"] for row in selected), default=0), 1
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    summaries = {item["label"]: item for item in report["summaries"]}
    audio = summaries.get("with_audio", {})
    muted = summaries.get("paired_no_audio", {})
    natural = summaries.get("natural_no_audio", {})

    def ratio(value: int, total: int) -> str:
        return f"{value}/{total} ({value / total * 100:.1f}%)" if total else "0/0"

    lines = [
        "# 视频音轨 ASR 链路 A/B Benchmark",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 口径",
        "",
        "- 配对组使用同一个源视频；无音轨版本通过 `ffmpeg -c:v copy -an` 生成，画面不重编码。",
        "- 实跑：视频探测、源视频入库、音轨抽取、FunASR（Paraformer + VAD + 标点）、"
        "质量门禁、原始证据/可信文本/时间戳存储、FTS 与文本向量检索、场景规划与关键帧抽取。",
        "- 不跑：前端、Agent、VLM、视觉嵌入、人脸、浏览器预览转码。文本向量用确定性 "
        "256 维适配器验证存储与召回接线；因此本报告不评价 WeMM 向量语义质量。",
        "- 数据没有逐字稿，不能计算 CER；本报告评价链路成功率、门禁结果、可检索性、耗时和资源占用。",
        "",
        "## 核心结果",
        "",
        "| 指标 | 同源有音轨 | 同源去音轨 | 天然无音轨 |",
        "|---|---:|---:|---:|",
    ]
    for title, key, formatter in (
        ("视频数", "videos", lambda value, _row: str(value)),
        ("媒体总时长（秒）", "media_duration_s", lambda value, _row: f"{value:.1f}"),
        ("流水线总墙钟（秒）", "wall_seconds", lambda value, _row: f"{value:.1f}"),
        ("单视频平均（秒）", "mean_seconds", lambda value, _row: f"{value:.2f}"),
        ("单视频 P95（秒）", "p95_seconds", lambda value, _row: f"{value:.2f}"),
        (
            "ASR 成功",
            "asr_success",
            lambda value, row: ratio(int(value), int(row.get("videos", 0))),
        ),
        (
            "产生可信文本",
            "nonempty_accepted",
            lambda value, row: ratio(int(value), int(row.get("videos", 0))),
        ),
        (
            "FTS 可检索",
            "lexical_retrievable",
            lambda value, row: ratio(int(value), int(row.get("videos", 0))),
        ),
        (
            "向量可检索",
            "vector_retrievable",
            lambda value, row: ratio(int(value), int(row.get("videos", 0))),
        ),
        ("音频分段向量行", "audio_vector_rows", lambda value, _row: str(value)),
        (
            "计划场景 / 响应场景 / 关键帧",
            "planned_scenes",
            lambda value, row: f"{value} / {row.get('video_scenes', 0)} / {row.get('derived_keyframes', 0)}",
        ),
        ("峰值 RSS（MB）", "peak_rss_mb", lambda value, _row: f"{value:.1f}"),
        ("峰值 MPS allocated（MB）", "peak_mps_allocated_mb", lambda value, _row: f"{value:.1f}"),
        ("峰值 MPS driver（MB）", "peak_mps_driver_mb", lambda value, _row: f"{value:.1f}"),
    ):
        cells = []
        for row in (audio, muted, natural):
            value = row.get(key, 0)
            cells.append(formatter(value, row))
        lines.append(f"| {title} | {' | '.join(cells)} |")

    paired_names = sorted(
        set(row["source_name"] for row in report["cases"] if row["label"] == "with_audio")
        & set(row["source_name"] for row in report["cases"] if row["label"] == "paired_no_audio")
    )
    deltas: list[float] = []
    for name in paired_names:
        left = next(
            row for row in report["cases"] if row["source_name"] == name and row["label"] == "with_audio"
        )
        right = next(
            row for row in report["cases"] if row["source_name"] == name and row["label"] == "paired_no_audio"
        )
        deltas.append(float(left["total_seconds"]) - float(right["total_seconds"]))
    lines.extend(
        [
            "",
            "## 配对差值",
            "",
            f"- 有效配对：{len(deltas)}。",
            f"- 有音轨相对去音轨的平均额外耗时：{sum(deltas) / len(deltas):.3f} 秒/视频。"
            if deltas
            else "- 尚无有效配对。",
            f"- 额外耗时 P50 / P95：{percentile(deltas, 0.50):.3f} / {percentile(deltas, 0.95):.3f} 秒。"
            if deltas
            else "",
            "",
            "## 样例",
            "",
            "| 视频 | 状态 | ASR 秒 | 门禁后文本 | 可检索 |",
            "|---|---|---:|---|---|",
        ]
    )
    examples = [
        row
        for row in report["cases"]
        if row["label"] == "with_audio" and (row["raw_text"] or row["asr_error"])
    ][:10]
    for row in examples:
        text = (row["accepted_text"] or row["raw_text"] or row["asr_error"]).replace("|", "\\|")
        if len(text) > 100:
            text = text[:97] + "..."
        searchable = "FTS+向量" if row["lexical_retrievable"] and row["vector_retrievable"] else "否"
        lines.append(
            f"| {row['source_name']} | {row['audio_transcription_status']} | "
            f"{float(row['asr_seconds'] or 0):.2f} | {text} | {searchable} |"
        )

    lines.extend(
        [
            "",
            "## 解释与限制",
            "",
            "- 无音轨视频当前会尝试 ASR，ffmpeg 返回无音频流后，系统保存一条 `failed` "
            "转写记录；源视频、场景和关键帧仍会成功入库。",
            "- `completed` 只表示推理与门禁流程完成，不代表逐字正确。Paraformer 通常不返回"
            "稳定声学置信度，通过结构门禁的段会带 `no_acoustic_score`。",
            "- 若背景音乐、外语或弱语音产生了通顺但错误的文本，第一版结构门禁仍可能放行；"
            "应结合本报告逐条样例做人工污染审计。",
            "- RSS 优先统计 benchmark 进程树；若操作系统禁止枚举子进程，则退化为主进程 RSS，"
            "具体口径见原始 JSON 的 `rss_scope`。MPS 指标来自 PyTorch 当前进程。RSS 与 "
            "MPS driver 有共享/映射内存，不能直接相加当作总内存。",
            "",
            f"原始结果：`{report['json_path']}`",
            "",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def effective_devices(service: Service) -> dict[str, str]:
    devices: dict[str, str] = {}
    for name in ("model", "vad_model", "punc_model"):
        component = getattr(service.model, name, None)
        try:
            devices[name] = str(next(component.parameters()).device)
        except (AttributeError, StopIteration, TypeError):
            continue
    return devices


def save_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    report["summaries"] = [
        summarize(report["cases"], label) for label in ("with_audio", "paired_no_audio", "natural_no_audio")
    ]
    try:
        report["json_path"] = str(json_path.relative_to(ROOT))
    except ValueError:
        report["json_path"] = str(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=0, help="Limit each source group; 0 means all")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--model", default="paraformer-zh")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=ROOT / "audio_eval" / "outputs" / "video_audio_ab_benchmark.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=ROOT / "docs" / "reports" / "20261027-video-audio-ab-benchmark.md",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    files = sorted(args.dataset.glob("*.mp4"))
    inventory = [(path, probe(path)) for path in files]
    with_audio = [path for path, info in inventory if info["has_audio"]]
    natural_no_audio = [path for path, info in inventory if not info["has_audio"]]
    if args.limit > 0:
        with_audio = with_audio[: args.limit]
        natural_no_audio = natural_no_audio[: args.limit]

    owned_work_dir = args.work_dir is None
    work_dir = (
        Path(tempfile.mkdtemp(prefix="sentrix-video-audio-ab-"))
        if owned_work_dir
        else args.work_dir.resolve()
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    print(
        json.dumps(
            {
                "event": "benchmark_start",
                "paired_sources": len(with_audio),
                "natural_no_audio": len(natural_no_audio),
                "work_dir": str(work_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    model_started = time.perf_counter()
    service = Service(
        model_name=args.model,
        vad_model=args.vad_model,
        punc_model=args.punc_model,
        device=args.device,
    )
    model_load_seconds = round(time.perf_counter() - model_started, 3)
    runtime = build_runtime(work_dir, service)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset.resolve()),
        "methodology": {
            "paired_muting": "ffmpeg -map 0:v:0 -c:v copy -an",
            "visual_content_identical": True,
            "preview_transcoding": False,
            "vlm_and_visual_embedding": False,
            "text_vector_adapter": "benchmark-hashing-text-v1/256d",
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "requested_device": args.device,
            "effective_devices": effective_devices(service),
            "model": args.model,
            "vad_model": args.vad_model,
            "punc_model": args.punc_model,
            "model_version": service.model_version,
            "model_config_hash": service.config_hash,
            "model_load_seconds": model_load_seconds,
        },
        "cases": [],
        "summaries": [],
        "json_path": str(args.json_output),
    }

    completed = 0
    total = len(with_audio) * 2 + len(natural_no_audio)
    try:
        for source in with_audio:
            original = run_case(
                runtime,
                source=source,
                label="with_audio",
                scope_id="benchmark-with-audio",
                source_name=source.name,
            )
            report["cases"].append(original)
            completed += 1
            print(
                json.dumps(
                    {
                        "event": "case_complete",
                        "completed": completed,
                        "total": total,
                        "file": source.name,
                        "label": "with_audio",
                        "seconds": original["total_seconds"],
                        "asr_seconds": original["asr_seconds"],
                        "status": original["audio_transcription_status"],
                        "accepted_chars": original["accepted_chars"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

            muted = work_dir / "muted-input" / source.name
            muted.parent.mkdir(parents=True, exist_ok=True)
            generation_seconds = make_muted(source, muted)
            muted_result = run_case(
                runtime,
                source=muted,
                label="paired_no_audio",
                scope_id="benchmark-paired-no-audio",
                source_name=source.name,
                generation_seconds=generation_seconds,
            )
            report["cases"].append(muted_result)
            muted.unlink(missing_ok=True)
            completed += 1
            print(
                json.dumps(
                    {
                        "event": "case_complete",
                        "completed": completed,
                        "total": total,
                        "file": source.name,
                        "label": "paired_no_audio",
                        "seconds": muted_result["total_seconds"],
                        "asr_seconds": muted_result["asr_seconds"],
                        "status": muted_result["audio_transcription_status"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            save_report(report, args.json_output, args.markdown_output)

        for source in natural_no_audio:
            result = run_case(
                runtime,
                source=source,
                label="natural_no_audio",
                scope_id="benchmark-natural-no-audio",
                source_name=source.name,
            )
            report["cases"].append(result)
            completed += 1
            print(
                json.dumps(
                    {
                        "event": "case_complete",
                        "completed": completed,
                        "total": total,
                        "file": source.name,
                        "label": "natural_no_audio",
                        "seconds": result["total_seconds"],
                        "asr_seconds": result["asr_seconds"],
                        "status": result["audio_transcription_status"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            save_report(report, args.json_output, args.markdown_output)
    finally:
        save_report(report, args.json_output, args.markdown_output)
        if owned_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(
        json.dumps(
            {
                "event": "benchmark_complete",
                "cases": completed,
                "json": str(args.json_output),
                "markdown": str(args.markdown_output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
