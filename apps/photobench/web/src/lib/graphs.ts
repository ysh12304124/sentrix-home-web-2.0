// 移植自 1.0 `services/photobench/frontend/src/App.vue`（只读，唯一权威）的**图数据构造**：
// 把后端算好的指标摊成模板 `v-for` 要的分层数组。两块：
//
//   - `:32-175`   两个巨型 computed：`memoryValidityArchitecture` / `memoryRecallMatrices`；
//   - `:1810-2055` L0..L4+ 卡片指标、证据链图、单题链路与节点详情弹窗。
//
// **与 1.0 的唯一结构差异**：1.0 的 computed / 函数直接读全局 ref（`activeRun` /
// `keyframeAnalysis` / `memoryEffectiveness` / `manifests` / `qaDetails` / `qaPage` /
// `chainMode`），这里改成显式入参，以便纯函数化并单测。**计算口径一行未动**，包括下面
// 这些看起来可疑但按要求照搬的地方（见文件尾「存疑照搬」）：
//
//   - `recorded()` 把 0 与 null 一律当「未记录」（0 个事件 / 0 次调用都会显示未记录）；
//   - `memoryLayerMetrics` 的关键帧 / 事件集合只统计**当前已展开**的 QA，未展开时 L2
//     回退到全量 `analysisUsefulness.event_context_count`；
//   - `chainDetailStatements` 的 `.slice(0, 12)` 实际不可达（previews 已先 `slice(0, 8)`，
//     上限 2 + 8 + 2 = 12），照搬保留。
//
// `effectiveRunSummary(run)` 在 1.0 里是这些函数的内部调用，这里由调用方先算好、以
// `summary` 传入（它的 122 行属于另一个模块，不在这里重复实现）。

import type {
  KeyframeAnalysis,
  ManifestSummary,
  MemoryEffectiveness,
  MemoryLevel,
  MemoryMatrix,
  Run,
  RunItemDetail,
  RunItemSummary,
  RunSummary,
} from "../api/types";
import { albumLocalUrl, inferMediaType, isVideoMedia, mediaKey } from "../api/media.ts";
import { fmtPct, memoryLayerToolMetric } from "./format.ts";
import { agent2RequirementStatusLabel, getAgent2Requirements } from "./trace.ts";

type Dict = Record<string, unknown>;

/** 1.0 可选链 / `|| {}` 的等价：非对象一律当空对象。 */
function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 的 `(x || [])`：非数组一律当空数组（保留 1.0 对「假值」的判定，不做长度推测）。 */
function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** 上面两件事的组合，给「显然是对象数组」的字段用。 */
function list(value: unknown): Dict[] {
  return arr(value).map(dict);
}

// ── 媒体小工具（1.0 `:646-658` / `:660-693`）─────────────────────────────
// `inferMediaType` / `isVideoMedia` / `albumLocalUrl` / `mediaKey` 已由 `api/media.ts`
// 移植，这里 import 复用。**`decorateMedia` / `itemMedia` 仍是本模块私有副本**：
// 前者在 `media.ts` 的签名是 `(T[] | null | undefined, albumId: string)`（本模块要传
// `readonly unknown[]` 与可选 albumId），后者在 1.0 是 `:660-693` 的**非 gt 分支**
// （权威完整版在 `lib/evidence.ts` 的 `itemMedia(item, albumId, gt)`）——两者都是内联
// 改造过的调用约定，不逐字照搬，故保留现状、不硬拆。计算口径与 `media.ts` / `evidence.ts`
// 逐行一致（`albumId` 缺省时同样回落到 `media_id` 的「相册/文件」两段）。

/** 1.0 `:646-658`。`activeRun.value?.album_id || (parts.length === 2 ? parts[0] : "")` 的 albumId 部分显式传入。 */
function decorateMedia(values: readonly unknown[], albumId?: string): Dict[] {
  return values.map((value) => {
    const img = dict(value);
    if (img.media_url) return img;
    const sourceId = String(img.media_id || img.image_id || img.video_id || "");
    const parts = sourceId.split("/");
    const file = parts.length >= 2 ? parts[parts.length - 1] : String(img.file_name || sourceId);
    const album = albumId || (parts.length === 2 ? parts[0] : "");
    const video = isVideoMedia(img);
    const collection = video ? "videos" : /^faceid_[^/]+\.(?:jpe?g|png|webp)$/i.test(file) ? "faces" : "photos";
    const mediaFile = video && !/\.mp4$/i.test(file) ? `${file}.mp4` : file;
    const local = album && mediaFile
      ? `/api/v1/albums/${encodeURIComponent(album)}/${collection}/${encodeURIComponent(mediaFile)}`
      : "";
    return local ? { ...img, media_type: video ? "video" : img.media_type || "image", media_url: local } : img;
  });
}

/** 1.0 `:660-693` 的**非 gt 分支**（`itemLayerChain` 只用这一支）。 */
function itemMedia(item: unknown, albumId?: string): Dict[] {
  const it = dict(item);
  // 交付口径（E）：模型「召回 / 回答来源」= 模型显式交付的图（selected / predicted）。
  const predictedMedia = list(it.predicted_media);
  if (predictedMedia.length) return decorateMedia(predictedMedia, albumId);
  const predictedImages = list(it.predicted_images);
  if (predictedImages.length) return decorateMedia(predictedImages, albumId);
  if (arr(it.predicted_file_names).length) {
    return arr(it.predicted_file_names).map((file_name) => ({
      file_name,
      media_type: inferMediaType(file_name),
      media_url: albumLocalUrl(albumId || "", file_name, ""),
    }));
  }
  const evidenceMedia = list(it.evidence_source_media);
  if (evidenceMedia.length) return decorateMedia(evidenceMedia, albumId);
  const evidenceImages = list(it.evidence_source_images);
  if (evidenceImages.length) return decorateMedia(evidenceImages, albumId);
  if (arr(it.evidence_source_file_names).length) {
    return decorateMedia(
      arr(it.evidence_source_file_names).map((file_name) => ({
        file_name,
        media_type: inferMediaType(file_name),
        media_url: albumLocalUrl(albumId || "", file_name, ""),
      })),
      albumId,
    );
  }
  // 候选只在没有交付证据时作为有界代表窗口展示（1.0 的候选窗口是 6）。
  const candidateMedia = list(it.retrieved_candidate_media);
  if (candidateMedia.length) return decorateMedia(candidateMedia.slice(0, 6), albumId);
  const candidateImages = list(it.retrieved_candidate_images);
  if (candidateImages.length) return decorateMedia(candidateImages.slice(0, 6), albumId);
  return arr(it.retrieved_file_names)
    .slice(0, 6)
    .map((file_name) => ({
      file_name,
      media_type: inferMediaType(file_name),
      media_url: albumLocalUrl(albumId || "", file_name, ""),
    }));
}

// Agent2 小工具（`getAgent2Trace` / `getAgent2Requirements` / `agent2RequirementStatusLabel`）
// 的权威实现在 `lib/trace.ts`（1.0 `:1718-1765`），这里 import 复用。

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :1810-1853 —— memoryLayerMetrics
// ══════════════════════════════════════════════════════════════════════════

export interface MemoryLayerCard {
  key: string;
  name: string;
  color: string;
  metric: string;
  detail: string;
  evidence: string;
  note: string;
}

export interface CallRate {
  calls: number;
  okRate: number | null;
}

export interface MemoryLayerMetricsView {
  layers: MemoryLayerCard[];
  rawMediaCount: number;
  keyframes: number;
  expandedCount: number;
  completed: number;
  total: number;
  visual: CallRate;
  ocr: CallRate;
  summaryCalls: CallRate;
  analysisProcessing: Dict;
}

export type QaDetailMap = Record<string | number, RunItemDetail | undefined>;

export interface MemoryLayerMetricsInput {
  run: Run | null | undefined;
  manifests: readonly ManifestSummary[];
  qaDetails: QaDetailMap;
  qaPageItems: readonly RunItemSummary[];
  memoryEffectiveness: MemoryEffectiveness | null | undefined;
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  /** = `effectiveRunSummary(run)`。1.0 在函数内部现算，这里由调用方传入。 */
  summary: RunSummary | null | undefined;
}

/** 1.0 `:1810-1853`。 */
export function memoryLayerMetrics(input: MemoryLayerMetricsInput): MemoryLayerMetricsView {
  const run: Run = input.run || ({} as Run);
  const summary = dict(input.summary);
  const manifest = dict(input.manifests.find((item) => item.album_id === run.album_id));
  const photoCount = Number(manifest.photo_count || 0);
  const videoCount = Number(manifest.video_count || 0);
  const rawCount = photoCount + videoCount;
  const progress = dict(dict(dict(run.phases).qa_eval).progress);
  const completed = Number(progress.completed ?? summary.completed ?? run.item_count ?? 0);
  const total = Number(progress.total ?? summary.total ?? run.qa_count ?? 0);
  const agent2 = dict(summary.agent2_trace);
  const requirementCounts = dict(agent2.requirement_status_counts);
  const requirementTotal = Object.values(requirementCounts).reduce<number>((sum, value) => sum + Number(value || 0), 0);
  const satisfiedRequirements = Number(requirementCounts.satisfied || 0);
  // 优先用 memory-effectiveness 接口实测的调用次数（新老 run 都有）；汇总缺 tool_performance 时回退 0。
  const meCalls = (kind: string): CallRate => {
    const row = dict(dict(input.memoryEffectiveness?.details)[kind]);
    const total = Number(row.total || 0);
    return { calls: total, okRate: total ? Number(row.effective || 0) / total : null };
  };
  const visual = meCalls("visual_calls");
  const ocr = meCalls("ocr_calls");
  const summaryCalls = memoryLayerToolMetric(summary, "get_core_memory");
  const analysis = input.keyframeAnalysis?.status === "ready" ? input.keyframeAnalysis : null;
  const analysisAssets = dict(analysis?.assets);
  const analysisProcessing = dict(analysis?.processing);
  const analysisUsefulness = dict(analysis?.usefulness);
  const expanded = Object.values(input.qaDetails).filter(Boolean) as RunItemDetail[];
  const previewRows = expanded.flatMap((item) =>
    list((item as unknown as Dict).tool_trace).flatMap((trace) => list(dict(trace.observation).preview)),
  );
  const keyframes = new Set(
    previewRows.filter((row) => row.media_kind === "video_keyframe").map((row) => row.asset_id || row.file_name).filter(Boolean),
  );
  const events = new Set(previewRows.map((row) => dict(row.event_context).id).filter(Boolean));
  const eventEvidenceRows = previewRows.filter((row) => dict(row.event_context).id).length;
  const qaSampleCount = expanded.length || (input.qaPageItems || []).length;
  const summaryTaskSampleCount = (input.qaPageItems || []).filter((item) => item.task_type === "T5_event_summary").length;
  const sceneCount: unknown = dict(analysis?.temporal).scene_count;
  const layers: MemoryLayerCard[] = [
    {
      key: "L0", name: "原始流", color: "raw",
      metric: rawCount ? `${rawCount} 个媒体` : "待读取",
      detail: `${photoCount} 图 · ${videoCount} 视频`,
      evidence: analysisAssets.keyframes == null
        ? "等待全量分析"
        : `${analysisAssets.source_videos} 个源视频 → ${analysisAssets.keyframes} 帧；保留 parent_asset_id 与时间戳`,
      note: "关键帧是视频派生媒体，仍归 L0",
    },
    {
      key: "L1", name: "场景图", color: "scene",
      metric: visual.okRate == null ? "未记录" : `视觉 ${fmtPct(visual.okRate)}`,
      detail: `观察 ${visual.calls} 次 · OCR ${ocr.calls} 次`,
      evidence: `视觉成功 ${visual.calls ? fmtPct(visual.okRate) : "-"} · OCR 调用 ${ocr.calls} 次 · 场景 ${sceneCount == null ? "-" : String(sceneCount)} 个`,
      note: "画面、文字、人物、动作、空间关系",
    },
    {
      key: "L2", name: "事件层", color: "event",
      metric: events.size
        ? `${events.size} 个事件`
        : analysisUsefulness.event_context_count
          ? `${analysisUsefulness.event_context_count} 个事件上下文`
          : "待展开",
      detail: `${eventEvidenceRows} 条证据带事件上下文`,
      evidence: `全量 QA 轨迹带回 ${String(analysisUsefulness.event_context_count ?? 0)} 个事件上下文；当前展开 ${events.size} 个`,
      note: "同一事件聚合多个媒体与时间地点",
    },
    {
      key: "L3", name: "目标/规则", color: "goal",
      metric: requirementTotal ? `${satisfiedRequirements}/${requirementTotal} 满足` : "未记录",
      detail: `规划 ${Number(agent2.planner_decision_count || 0)} 题 · 目标证据需求`,
      evidence: requirementTotal
        ? `证据账本满足 ${satisfiedRequirements} / ${requirementTotal}；规划决策 ${Number(agent2.planner_decision_count || 0)} 次`
        : "当前 run 未记录目标账本",
      note: "时间、事件、地点、人物、场景等查询要求",
    },
    {
      key: "L4+", name: "总结/回答", color: "summary",
      metric: summaryCalls.calls ? `总结访问 ${summaryCalls.calls} 次` : "按题回答",
      detail: `当前页总结题 ${summaryTaskSampleCount} · 已完成 ${completed}/${total || "-"}`,
      evidence: `Judge 均分 ${summary.answer_quality_mean ?? "-"} / 2 · exact ${
        summary.exact_accuracy == null ? "-" : fmtPct(summary.exact_accuracy)
      } · 已完成 ${completed}/${total || "-"}`,
      note: "事件/相册级递归摘要与最终回答",
    },
  ];
  return {
    layers,
    rawMediaCount: rawCount,
    keyframes: keyframes.size,
    expandedCount: qaSampleCount,
    completed,
    total,
    visual,
    ocr,
    summaryCalls,
    analysisProcessing,
  };
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :1855-1919 —— memoryEvidenceGraph（节点带 transition）
// ══════════════════════════════════════════════════════════════════════════

export interface GraphNode {
  kind: string;
  value: string;
  evidence: string;
}

export interface GraphLayer {
  key: string;
  name: string;
  color: string;
  stage: string;
  nodes: GraphNode[];
  transition: string;
}

export interface MemoryEvidenceGraphInput {
  run: Run | null | undefined;
  qaDetails: QaDetailMap;
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  /** `memoryLayerMetrics(...)` 的结果。1.0 用的是 `memoryLayerView.value`。 */
  view: MemoryLayerMetricsView;
  summary: RunSummary | null | undefined;
}

/** 1.0 `:1855-1919`。 */
export function memoryEvidenceGraph(input: MemoryEvidenceGraphInput): GraphLayer[] {
  const view = input.view || { layers: [] as MemoryLayerCard[] };
  const analysis = input.keyframeAnalysis?.status === "ready" ? input.keyframeAnalysis : null;
  const assets = dict(analysis?.assets);
  const temporal = dict(analysis?.temporal);
  const usefulness = dict(analysis?.usefulness);
  const run: Run = input.run || ({} as Run);
  const rawAssets = Number(view.rawMediaCount || dict(run.input_integrity).files_checked || 0);
  const keyframeCount = assets.keyframes == null ? null : Number(assets.keyframes);
  const visualCalls = Number(view.visual?.calls || 0);
  const ocrCalls = Number(view.ocr?.calls || 0);
  const sceneCount = Number(temporal.scene_count || 0);
  const eventCount = Number(usefulness.event_context_count || 0);
  const requirements = getAgent2Requirements(Object.values(input.qaDetails).find(Boolean) || {});
  const requirementCounts = dict(dict(input.summary).agent2_trace).requirement_status_counts;
  const requirementTotal = Object.values(dict(requirementCounts)).reduce<number>((sum, value) => sum + Number(value || 0), 0);
  const completed = view.completed || 0;
  const total = view.total || 0;
  const recorded = (value: unknown, fallback = "未记录"): string =>
    value == null || value === 0 ? fallback : String(value);
  const sourceEvidence = rawAssets
    ? `${rawAssets} 个原始媒体 · asset_id / file_name · 输入完整性校验`
    : "接口未返回原始媒体数量，暂不能证明记忆已入库";
  return [
    {
      key: "L0", name: "原始流", color: "raw", stage: "输入与派生媒体",
      nodes: [
        { kind: "原始媒体", value: recorded(rawAssets), evidence: sourceEvidence },
        {
          kind: "关键帧",
          value: keyframeCount == null ? "未记录" : String(keyframeCount),
          evidence: keyframeCount == null ? "待获取 parent_asset_id" : "parent_asset_id · source_timestamp_sec",
        },
      ],
      transition: "asset_id + 时间戳 → 场景观察",
    },
    {
      key: "L1", name: "场景图", color: "scene", stage: "视觉、文字、人物、动作",
      nodes: [
        { kind: "视觉观察", value: recorded(visualCalls), evidence: "inspect_photo → observation.preview" },
        { kind: "文字 / OCR", value: recorded(ocrCalls), evidence: "read_photo_text → text + media_id" },
        { kind: "场景节点", value: recorded(sceneCount), evidence: "source_scene_index · 人物/动作/空间关系" },
      ],
      transition: "观察 + OCR + 时间地点 → 事件上下文",
    },
    {
      key: "L2", name: "事件层", color: "event", stage: "跨媒体聚合",
      nodes: [
        { kind: "事件上下文", value: recorded(eventCount), evidence: "event_context.id · related_media_ids" },
        { kind: "时间 / 地点", value: eventCount ? "已挂接" : "未记录", evidence: "event_context 的时间、地点、实体字段" },
      ],
      transition: "事件 ID + 媒体引用 → 查询证据需求",
    },
    {
      key: "L3", name: "目标 / 规则", color: "goal", stage: "通用查询槽位",
      nodes: [
        { kind: "查询槽位", value: "时间 · 事件 · 地点 · 人物 · 场景", evidence: "query_anchors → evidence_refs" },
        {
          kind: "证据账本",
          value: requirementTotal ? `${requirementTotal} 项` : requirements.length ? `${requirements.length} 项` : "未记录",
          evidence: requirementTotal ? "满足状态 + provenance_refs" : "本 run 未返回 Agent 证据账本",
        },
      ],
      transition: "evidence_refs + provenance → 总结与回答",
    },
    {
      key: "L4+", name: "总结 / 回答", color: "summary", stage: "可验证输出",
      nodes: [
        { kind: "回答", value: total ? `${completed}/${total}` : "未记录", evidence: "final_answer · answer_claims" },
        { kind: "证据核验", value: input.run ? "Judge" : "未记录", evidence: "judge / evidence_judge → 分数与理由" },
      ],
      transition: "输出保留证据链，可回溯到 L0",
    },
  ];
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :1921-1955 —— effectiveEvidenceGraph（当前展开的那一条 QA）
// ══════════════════════════════════════════════════════════════════════════

/** 1.0 `:1921-1955`。 */
export function effectiveEvidenceGraph(item: RunItemDetail | null | undefined): GraphLayer[] {
  const it = dict(item);
  const traces = list(it.tool_trace);
  const previews = traces.flatMap((trace) => list(dict(trace.observation).preview));
  const candidates = list(it.retrieved_candidate_media || it.retrieved_candidate_images);
  const evidence = list(it.evidence_source_media || it.evidence_source_images);
  const requirements = getAgent2Requirements(item);
  const answer = it.final_answer || it.answer;
  const recorded = (value: unknown, fallback = "未展开 QA"): string =>
    value == null || value === 0 ? fallback : String(value);
  return [
    {
      key: "L0", name: "问题 / 原始证据", color: "raw", stage: "本题输入范围",
      nodes: [
        {
          kind: "问题",
          value: it.question ? "1 条" : "未展开 QA",
          evidence: it.question ? String(it.question) : "请先展开一条 QA",
        },
        { kind: "GT 媒体", value: recorded(arr(it.gt_media).length, "未记录"), evidence: "gt_media · file_name / asset_id" },
      ],
      transition: "问题槽位 → 工具查询",
    },
    {
      key: "L1", name: "工具 / 检索", color: "scene", stage: "测评时实际调用",
      nodes: [
        { kind: "工具调用", value: recorded(traces.length, "未记录"), evidence: "tool_trace · name · arguments" },
        { kind: "候选媒体", value: recorded(candidates.length, "未记录"), evidence: "retrieved_candidate_media · result_set_id" },
        { kind: "观察", value: recorded(previews.length, "未记录"), evidence: "observation.preview · evidence_summary" },
      ],
      transition: "候选 + 观察 → 事件与证据",
    },
    {
      key: "L2", name: "事件 / 记忆", color: "event", stage: "有效记忆上下文",
      nodes: [
        {
          kind: "事件上下文",
          value: recorded(new Set(previews.map((row) => dict(row.event_context).id).filter(Boolean)).size, "未记录"),
          evidence: "event_context.id · title · summary",
        },
        {
          kind: "时间 / 地点",
          value: previews.some((row) => row.captured_at || row.place || row.location_context) ? "已返回" : "未记录",
          evidence: "captured_at · place · location_context",
        },
      ],
      transition: "事件引用 → 证据需求",
    },
    {
      key: "L3", name: "目标 / 证据", color: "goal", stage: "本题判定依据",
      nodes: [
        { kind: "查询锚点", value: it.query_anchors ? "已记录" : "未记录", evidence: it.query_anchors ? String(it.query_anchors) : "query_anchors 未返回" },
        {
          kind: "证据账本",
          value: recorded(requirements.length, "未记录"),
          evidence: requirements.length
            ? requirements.map((req) => `${req.id}:${agent2RequirementStatusLabel(req.status)}`).join(" · ")
            : "evidence_refs 未返回",
        },
        { kind: "实际证据", value: recorded(evidence.length, "未记录"), evidence: "evidence_source_media · answer_evidence" },
      ],
      transition: "证据引用 → 回答与 Judge",
    },
    {
      key: "L4+", name: "回答 / 评判", color: "summary", stage: "测评结果",
      nodes: [
        { kind: "回答", value: answer ? "已形成" : "未形成", evidence: answer ? String(answer) : "final_answer 未返回" },
        { kind: "声明", value: recorded(arr(it.answer_claims).length, "未记录"), evidence: "answer_claims · claim evidence" },
        {
          kind: "Judge",
          value: dict(it.judge).score == null ? "未记录" : `${dict(it.judge).score} 分`,
          evidence: dict(it.judge).reason ? String(dict(it.judge).reason) : "judge.reason 未返回",
        },
      ],
      transition: "结果可回溯到原始媒体",
    },
  ];
}

/** 1.0 `:1957`。`chainMode` 二选一。 */
export function activeEvidenceGraphView(
  chainMode: string | null | undefined,
  memoryGraph: GraphLayer[],
  effectiveGraph: GraphLayer[],
): GraphLayer[] {
  return chainMode === "effective" ? effectiveGraph : memoryGraph;
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :1958-2015 —— 节点详情弹窗：媒体去重、语句拼装、逐题记录
// ══════════════════════════════════════════════════════════════════════════

export interface ChainMedia {
  media_type?: string;
  media_id?: string;
  asset_id?: string;
  file_name?: string;
  media_url?: string;
  [key: string]: unknown;
}

export interface ChainStatement {
  label: string;
  value: string;
}

/** 1.0 `:1958-1970`。媒体去重（按 `mediaKey`）后截断到 **12**。 */
export function chainDetailMedia(item: unknown, previews: readonly unknown[], albumId?: string): ChainMedia[] {
  const it = dict(item);
  const values: unknown[] = [
    ...arr(it.gt_media),
    ...arr(it.evidence_source_media),
    ...arr(it.predicted_media),
    ...previews.map((row) => ({
      ...dict(row),
      file_name: dict(row).file_name || dict(row).media_id,
      media_type: dict(row).media_kind === "video" ? "video" : "image",
    })),
  ];
  const unique = new Map<string, Dict>();
  for (const media of decorateMedia(values, albumId)) {
    const key = mediaKey(media);
    if (key && !unique.has(key)) unique.set(key, media);
  }
  return [...unique.values()].slice(0, 12) as ChainMedia[];
}

/** 1.0 `:1971-1983`。语句拼装后截断到 **12**。 */
export function chainDetailStatements(
  item: unknown,
  previews: readonly unknown[],
  layer: Pick<GraphLayer, "key">,
  node: Pick<GraphNode, "kind" | "evidence"> | null | undefined,
): ChainStatement[] {
  const it = dict(item);
  const statements: ChainStatement[] = [];
  if (it.question) statements.push({ label: "用户问题", value: String(it.question) });
  if (node?.evidence) statements.push({ label: `${layer.key} · ${node.kind}`, value: node.evidence });
  for (const row of previews.slice(0, 8)) {
    const r = dict(row);
    const text = r.evidence_summary || dict(r.event_context).summary || r.description || r.text;
    if (text) {
      statements.push({
        label: `${r.media_id || r.file_name || r.asset_id || "媒体"} · 观察`,
        value: String(text),
      });
    }
  }
  if (it.answer) statements.push({ label: "模型回答", value: String(it.answer) });
  if (dict(it.judge).reason) statements.push({ label: "Judge 理由", value: String(dict(it.judge).reason) });
  return statements.slice(0, 12);
}

export interface ChainTraceRow {
  label: string;
  value: string;
}

export interface ChainRecord {
  key: string;
  index: number;
  question: string;
  media: ChainMedia[];
  statements: ChainStatement[];
  traceRows: ChainTraceRow[];
  reasoning: string;
}

export interface ChainDetail {
  mode: string;
  layer: GraphLayer;
  node: GraphNode;
  records: ChainRecord[];
  page: number;
  pageSize: number;
  total: number;
}

export interface ChainDetailInput {
  mode: string;
  layer: GraphLayer;
  node: GraphNode;
  /** = 1.0 `qaPage.value`（只需这四项）。 */
  page: { items: readonly RunItemSummary[]; page: number; page_size: number; total: number };
  details: QaDetailMap;
  albumId?: string;
}

/**
 * 1.0 `:1984-2015` `openChainNode` 的**纯部分**：`await Promise.allSettled(loadQaDetail)`
 * 的网络副作用留在调用方，这里只吃已经加载好的 `details`。
 */
export function chainDetailFor(input: ChainDetailInput): ChainDetail {
  const pageItems = input.page.items || [];
  const indices = pageItems.map((summary) => summary.index).filter((index) => index != null);
  const records = indices
    .map((index) => {
      const item = input.details[index];
      if (!item) return null;
      const it = item as unknown as Dict;
      const traces = list(it.tool_trace);
      const previews = traces.flatMap((trace) => list(dict(trace.observation).preview));
      const traceRows: ChainTraceRow[] = traces.slice(0, 12).map((trace, traceIndex) => ({
        label: `${traceIndex + 1}. ${String(trace.tool_name || trace.name || trace.tool || "工具调用")}`,
        value: JSON.stringify(trace, null, 2),
      }));
      return {
        key: `${it.qa_id || "qa"}-${index}`,
        index,
        question:
          (it.question as string) ||
          pageItems.find((summary) => summary.index === index)?.question ||
          "未记录问题",
        media: chainDetailMedia(item, previews, input.albumId),
        statements: chainDetailStatements(item, previews, input.layer, input.node),
        traceRows,
        reasoning: JSON.stringify(
          { agent2_trace: it.agent2_trace, execution_trace: it.execution_trace, answer_grounding: it.answer_grounding },
          null,
          2,
        ),
      };
    })
    .filter(Boolean) as ChainRecord[];
  return {
    mode: input.mode,
    layer: input.layer,
    node: input.node,
    records,
    page: input.page.page,
    pageSize: input.page.page_size,
    total: input.page.total,
  };
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :2017-2037 —— itemLayerChain（单题五层证据链，pass / fail / na）
// ══════════════════════════════════════════════════════════════════════════

export interface ItemLayerChainNode {
  key: string;
  name: string;
  status: "pass" | "fail" | "na";
  detail: string;
  evidence: string;
}

/** 1.0 `:2017-2037`。 */
export function itemLayerChain(item: RunItemDetail | null | undefined, albumId?: string): ItemLayerChainNode[] {
  const it = dict(item);
  const traces = list(it.tool_trace);
  const previews = traces.flatMap((trace) => list(dict(trace.observation).preview));
  const keyframes = new Set(
    previews.filter((row) => row.media_kind === "video_keyframe").map((row) => row.asset_id || row.file_name).filter(Boolean),
  );
  const events = new Set(previews.map((row) => dict(row.event_context).id).filter(Boolean));
  const observations = previews.length;
  const requirements = getAgent2Requirements(item);
  const satisfied = requirements.filter((req) => req.status === "satisfied").length;
  const media = itemMedia(item, albumId);
  const mediaNames = media.slice(0, 2).map((m) => m.file_name || m.asset_id || m.media_id).filter(Boolean) as string[];
  const previewNames = previews.slice(0, 2).map((row) => row.asset_id || row.file_name || row.media_id).filter(Boolean) as string[];
  const eventNames = [...events].slice(0, 2) as string[];
  const requirementNames = requirements.slice(0, 2).map((req) => req.id).filter(Boolean) as string[];
  const answer = it.final_answer || it.answer;
  return [
    {
      key: "L0", name: "原始流",
      status: media.length ? "pass" : "na",
      detail: `${media.length} 媒体${keyframes.size ? ` · 关键帧 ${keyframes.size}` : ""}`,
      evidence: mediaNames.join("、") || "无 asset_id / media_id",
    },
    {
      key: "L1", name: "场景图",
      status: observations ? "pass" : "na",
      detail: observations ? `${observations} 条视觉/文字观察` : "未返回观察",
      evidence: previewNames.join("、") || "无 observation.preview",
    },
    {
      key: "L2", name: "事件",
      status: events.size ? "pass" : "na",
      detail: events.size ? `${events.size} 个事件上下文` : "未带回事件上下文",
      evidence: eventNames.join("、") || "无 event_context.id",
    },
    {
      key: "L3", name: "目标",
      status: requirements.length ? (satisfied === requirements.length ? "pass" : "fail") : "na",
      detail: requirements.length ? `${satisfied}/${requirements.length} 项证据需求` : "未记录规划",
      evidence: requirementNames.join("、") || "无 evidence_refs",
    },
    {
      key: "L4+", name: "回答",
      status: answer ? "pass" : "fail",
      detail: answer ? "已形成回答" : "未形成回答",
      evidence: answer
        ? `answer_claims ${arr(it.answer_claims).length || 0} · Judge ${
            dict(it.judge).score == null ? "-" : String(dict(it.judge).score)
          }`
        : "无 final_answer",
    },
  ];
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :32-87 —— memoryValidityArchitecture（五层「记忆有效性分层」）
// ══════════════════════════════════════════════════════════════════════════

export interface ValidityNode {
  label: string;
  value: string | number;
  note: string;
}

export interface ValidityLayer {
  key: string;
  name: string;
  stage: string;
  nodes: ValidityNode[];
}

export interface MemoryValidityArchitectureInput {
  memoryEffectiveness: MemoryEffectiveness | null | undefined;
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  run: Run | null | undefined;
  /** = `effectiveRunSummary(run)`（1.0 内部现算）。 */
  summary: RunSummary | null | undefined;
}

/** 1.0 `:32-87`。 */
export function memoryValidityArchitecture(input: MemoryValidityArchitectureInput): ValidityLayer[] {
  const levels: MemoryLevel[] = input.memoryEffectiveness?.levels || [];
  const level = (key: string): MemoryLevel => levels.find((item) => item.key === key) || {};
  const raw = level("L0");
  const scene = level("L1");
  const events = level("L2");
  const rules = level("L3");
  const answers = level("L4+");
  const details = input.memoryEffectiveness?.details || {};
  const analysis = input.keyframeAnalysis?.status === "ready" ? input.keyframeAnalysis : null;
  const runSummary = input.summary;
  const visual = dict(details.visual_calls);
  const ocr = dict(details.ocr_calls);
  const correctness = dict(details.answer_correctness);
  const ratio = (value: unknown, total: unknown): string => (total ? fmtPct(Number(value || 0) / Number(total)) : "-");
  const retrieval = dict(analysis?.retrieval);
  return [
    {
      key: "L0", name: "原始媒体与检索", stage: "记忆库规模 → 测评检索结果",
      nodes: [
        { label: "原始媒体创建量", value: raw.created || 0, note: "记忆库输入规模，不代表测评有效" },
        {
          label: "原始图片交付 Recall",
          value: runSummary?.image_retrieval_recall_micro == null ? "未计算" : fmtPct(runSummary.image_retrieval_recall_micro),
          note: `${fmtPct(runSummary?.image_retrieval_precision_micro)} Precision · 按模型使用（交付）口径；工具召回维度见下方两列矩阵`,
        },
        {
          label: "关键帧父级 Recall",
          value: analysis
            ? ratio(retrieval.video_gt_targets_hit_by_keyframe, retrieval.video_gt_targets)
            : "未计算",
          note: analysis
            ? `${Number(retrieval.video_gt_targets_hit_by_keyframe || 0)}/${Number(retrieval.video_gt_targets || 0)} 个 GT 源视频被覆盖`
            : "关键帧接口尚未返回",
        },
        {
          label: "关键帧返回 Precision",
          value: analysis ? ratio(retrieval.predicted_relevant_keyframes, retrieval.predicted_keyframes) : "未计算",
          note: analysis
            ? `${Number(retrieval.predicted_relevant_keyframes || 0)}/${Number(retrieval.predicted_keyframes || 0)} 个返回关键帧与 GT 父视频一致`
            : "关键帧接口尚未返回",
        },
      ],
    },
    {
      key: "L1", name: "场景解析记忆", stage: "视觉 / OCR → 场景节点",
      nodes: [
        {
          label: "解析轨迹覆盖率",
          value: ratio(scene.effective, scene.created),
          note: `${scene.effective || 0}/${scene.created || 0} 个解析结果在测评轨迹出现；不是语义正确率`,
        },
        {
          label: "视觉工具成功率",
          value: ratio(visual.effective, visual.total),
          note: `${Number(visual.effective || 0)}/${Number(visual.total || 0)} 次 inspect_photo 成功`,
        },
        {
          label: "OCR 工具成功率",
          value: ratio(ocr.effective, ocr.total),
          note: `${Number(ocr.effective || 0)}/${Number(ocr.total || 0)} 次 read_photo_text 成功`,
        },
      ],
    },
    {
      key: "L2", name: "事件记忆", stage: "场景 → 时间 / 地点 / 事件",
      nodes: [
        {
          label: "事件图谱覆盖率",
          value: ratio(events.effective, events.created),
          note: `${events.effective || 0}/${events.created || 0} 个事件在测评工具图谱中出现；不是事实正确率`,
        },
        {
          label: "跨媒体关联",
          value: input.memoryEffectiveness?.data_quality?.graph_ready ? "已校验" : "不完整",
          note: "事件 ID → 媒体 → 时间 / 地点字段",
        },
      ],
    },
    {
      key: "L3", name: "证据规则", stage: "事件记忆 → 目标证据",
      nodes: [
        {
          label: "证据闭合率（代理）",
          value: ratio(rules.effective, rules.created),
          note: `${rules.effective || 0}/${rules.created || 0} 项需求满足；仅表示证据链闭合`,
        },
        { label: "未闭合需求", value: rules.ineffective || 0, note: "open / running / failed，需继续形成证据" },
      ],
    },
    {
      key: "L4+", name: "回答结果", stage: "证据 → 最终回答 Judge",
      nodes: [
        {
          label: "Agent 完整闭环率",
          value: ratio(answers.effective, answers.created),
          note: `${answers.effective || 0}/${answers.created || 0} 条回答 complete`,
        },
        {
          label: "Exact Accuracy",
          value: ratio(correctness.correct, answers.created),
          note: `正确 ${Number(correctness.correct || 0)} · 部分 ${Number(correctness.partial || 0)} · 错误 ${Number(correctness.incorrect || 0)}`,
        },
      ],
    },
  ];
}

// ══════════════════════════════════════════════════════════════════════════
// 1.0 :88-175 —— memoryRecallMatrices（工具召回 / 模型使用两张 2×3 命中矩阵）
// ══════════════════════════════════════════════════════════════════════════

export interface RecallNode {
  label: string;
  value: string | number;
  note: string;
}

export interface RecallGroup {
  label: string;
  total: number;
  note: string;
  nodes: RecallNode[];
}

export interface RecallLayer {
  key: string;
  name: string;
  stage: string;
  nodes: RecallNode[];
  groups?: RecallGroup[];
}

export interface RecallMatrix {
  key: string;
  title: string;
  note: string;
  eligible: number;
  hit: number;
  miss: number;
  layers: RecallLayer[];
}

/** 1.0 `:88-175`。`memoryAttribution` 在 1.0 是 `memoryEffectiveness.details.memory_answer_attribution`。 */
export function memoryRecallMatrices(
  memoryEffectiveness: MemoryEffectiveness | null | undefined,
): RecallMatrix[] {
  const details = memoryEffectiveness?.details || {};
  const attribution = dict(details.memory_answer_attribution);
  const delivery = dict(details.delivery_statistics);
  const correctness = dict(details.answer_correctness);
  const judgedTotal =
    Number(correctness.correct || 0) + Number(correctness.partial || 0) + Number(correctness.incorrect || 0);
  const pct = (value: unknown, total: unknown): string => (total ? fmtPct(Number(value) / Number(total)) : "-");
  const DIMS = [
    {
      key: "tool",
      title: "工具召回命中矩阵",
      note: "工具召回命中：GT 媒体进入检索 / 工具返回集合（视频按关键帧父级覆盖）",
      hitLabel: "工具召回命中",
      missLabel: "工具召回未命中",
      stageL1: "GT 是否进入检索候选",
    },
    {
      key: "usage",
      title: "模型使用命中矩阵",
      note: "模型使用命中：GT 媒体被模型显式用于回答（严格交付口径，与逐题指标一致）",
      hitLabel: "模型使用命中",
      missLabel: "模型使用未命中",
      stageL1: "GT 是否被模型用于回答",
    },
  ];
  return DIMS.map((dim) => {
    const scope = dict(attribution[dim.key]);
    const matrix: MemoryMatrix = dict(scope.matrix) as MemoryMatrix;
    const hit = Number(scope.hit_items || 0);
    const miss = Number(scope.miss_items || 0);
    const eligible = Number(scope.eligible || 0);
    const videoDelivery = dict(delivery.video_keyframe);
    const videoTotal = Number(videoDelivery.eligible_items || 0);
    const videoNode = (candidate: boolean): RecallNode => {
      const count = candidate ? videoDelivery.candidate_hit_items : videoDelivery.delivered_items;
      const rate = candidate ? videoDelivery.candidate_hit_rate : videoDelivery.delivery_rate;
      return {
        label: candidate ? "候选中找到对应视频关键帧" : "最终交付正确视频素材",
        value: count == null ? "未记录" : `${Number(count)}/${videoTotal} 题`,
        note: rate == null ? "缺少记录，无法计算" :
          `${fmtPct(rate)} · ${candidate ? "未找到" : "未交付"} ${videoTotal - Number(count)} 题`,
      };
    };
    const hitWrong = Number(matrix.hit_incorrect || 0);
    const missWrong = Number(matrix.miss_incorrect || 0);
    return {
      key: dim.key,
      title: dim.title,
      note: dim.note,
      eligible,
      hit,
      miss,
      layers: [
        {
          key: "L0", name: "评测题目范围", stage: "回答结果与 GT 媒体对齐",
          nodes: [
            {
              label: "全量已评分题",
              value: pct(judgedTotal, judgedTotal),
              note: `${judgedTotal} 题；正确 ${Number(correctness.correct || 0)} · 部分 ${Number(correctness.partial || 0)} · 错误 ${Number(correctness.incorrect || 0)}`,
            },
            {
              label: "可归因题占比",
              value: pct(eligible, judgedTotal),
              note: `${eligible}/${judgedTotal} 题有 GT 媒体；不可回答题不进归因`,
            },
          ],
        },
        {
          key: "L1", name: "命中结果", stage: dim.stageL1,
          nodes: [
            { label: dim.hitLabel, value: "已找到", note: `${hit}/${eligible} 题` },
            { label: dim.missLabel, value: "未找到", note: `${miss}/${eligible} 题` },
          ],
        },
        {
          key: "L2", name: "回答评分结果", stage: "在命中 / 未命中分支内看 Judge",
          nodes: [
            {
              label: "命中 → 正确",
              value: matrix.hit_correct || 0,
              note: `${pct(matrix.hit_correct, eligible)} 总体 · ${pct(matrix.hit_correct, hit)} 命中分支`,
            },
            {
              label: "命中 → 部分",
              value: matrix.hit_partial || 0,
              note: `${pct(matrix.hit_partial, eligible)} 总体 · ${pct(matrix.hit_partial, hit)} 命中分支`,
            },
            {
              label: "命中 → 错误",
              value: hitWrong,
              note: `${pct(hitWrong, eligible)} 总体 · ${pct(hitWrong, hit)} 命中分支`,
            },
            {
              label: "未命中 → 正确",
              value: matrix.miss_correct || 0,
              note: `${pct(matrix.miss_correct, eligible)} 总体 · ${pct(matrix.miss_correct, miss)} 未命中分支`,
            },
            {
              label: "未命中 → 部分",
              value: matrix.miss_partial || 0,
              note: `${pct(matrix.miss_partial, eligible)} 总体 · ${pct(matrix.miss_partial, miss)} 未命中分支`,
            },
            {
              label: "未命中 → 错误",
              value: missWrong,
              note: `${pct(missWrong, eligible)} 总体 · ${pct(missWrong, miss)} 未命中分支`,
            },
          ],
          groups: [
            {
              label: dim.hitLabel, total: hit, note: "命中分支",
              nodes: [
                { label: "回答正确", value: pct(matrix.hit_correct, eligible), note: `${pct(matrix.hit_correct, hit)} 本分支；${matrix.hit_correct || 0}/${eligible} 全部` },
                { label: "部分正确", value: pct(matrix.hit_partial, eligible), note: `${pct(matrix.hit_partial, hit)} 本分支；${matrix.hit_partial || 0}/${eligible} 全部` },
                { label: "回答错误", value: pct(hitWrong, eligible), note: `${pct(hitWrong, hit)} 本分支；${hitWrong}/${eligible} 全部` },
              ],
            },
            {
              label: dim.missLabel, total: miss, note: "未命中分支",
              nodes: [
                { label: "回答正确", value: pct(matrix.miss_correct, eligible), note: `${pct(matrix.miss_correct, miss)} 本分支；${matrix.miss_correct || 0}/${eligible} 全部` },
                { label: "部分正确", value: pct(matrix.miss_partial, eligible), note: `${pct(matrix.miss_partial, miss)} 本分支；${matrix.miss_partial || 0}/${eligible} 全部` },
                { label: "回答错误", value: pct(missWrong, eligible), note: `${pct(missWrong, miss)} 本分支；${missWrong}/${eligible} 全部` },
              ],
            },
          ],
        },
        {
          key: "L3", name: "视频召回与交付", stage: "仅看本次视频题，不使用 Judge 分数",
          nodes: [videoNode(true), videoNode(false)],
        },
      ],
    };
  });
}
