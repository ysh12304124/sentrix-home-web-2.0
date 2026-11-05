import type { Phase } from "../api/types.ts";
import { fmtSeconds } from "./format.ts";

type Values = Record<string, unknown>;
const record = (value: unknown): Values => value && typeof value === "object" ? value as Values : {};
const seconds = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

export function pipelineDuration(value: number | null): string {
  if (value === null) return "未记录";
  if (value < 60) return fmtSeconds(value);
  const tenths = Math.round(value * 10);
  const hours = Math.floor(tenths / 36000);
  const minutes = Math.floor(tenths % 36000 / 600);
  const remainder = tenths % 600 / 10;
  return `${hours ? `${hours}小时` : ""}${minutes ? `${minutes}分` : ""}${remainder ? `${remainder}秒` : ""}`;
}

/** 起止时间覆盖整次请求；旧 total_seconds 可能仅为后端处理时间。 */
export function phaseElapsed(phase: Phase = {}, recordedSeconds: unknown = phase.total_seconds): { seconds: number | null; label: string } {
  const start = typeof phase.started_at === "string" ? Date.parse(phase.started_at) : NaN;
  const end = typeof phase.finished_at === "string" ? Date.parse(phase.finished_at) : NaN;
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
    return { seconds: (end - start) / 1000, label: "实际总耗时" };
  }
  return { seconds: seconds(recordedSeconds), label: phase.status === "running" ? "已用时间" : "记录耗时" };
}

export function isCombinedImport(phase: Phase = {}): boolean {
  if (phase.execution_mode === "explicit") return false;
  const timings = record(phase.timings);
  return ["prepare", "finish", "summarize", "post_total"].some(key => timings[key] != null)
    || record(phase.progress).current_directory != null;
}

export interface PipelineTimingRow {
  key: string;
  label: string;
  value: string;
  basis: string;
  description: string;
  share: string | null;
  nested: boolean;
}

export interface PipelineTimingGroup {
  key: string;
  title: string;
  summary?: string;
  note: string;
  rows: PipelineTimingRow[];
  collapsed?: boolean;
}

export interface PipelineTimingView {
  overview: Array<{ label: string; value: string; note: string }>;
  groups: PipelineTimingGroup[];
  combined: boolean;
}

type Definition = [key: string, label: string, basis: string, description: string, nested?: boolean];

/** 只投影已有计时，不用 worker 累计时间推算关键路径或补齐历史。 */
export function pipelineTimingView(phase: Phase = {}, imported: Phase = {}): PipelineTimingView {
  const combined = isCombinedImport(imported);
  const explicit = phase.execution_mode === "explicit" || imported.execution_mode === "explicit";
  const source = combined ? imported : phase;
  const timings = record(source.timings);
  const waiting = source.status === "running" || source.status === "pending";
  const total = explicit ? seconds(timings.total) : null;
  const overview: PipelineTimingView["overview"] = [];
  const groups: PipelineTimingGroup[] = [];
  const rows = (definitions: Definition[], includeMissing = explicit): PipelineTimingRow[] =>
    definitions.filter(([key]) => includeMissing || timings[key] != null).map(([key, label, basis, description, nested]) => {
      const spanStage = key.startsWith("source_memory_") || key.startsWith("video_");
      const duration = seconds(timings[spanStage ? key + "_wall" : key]);
      const cumulative = spanStage ? seconds(timings[key]) : null;
      if (spanStage) {
        basis = "整段实际耗时";
        if (cumulative !== null) description += ' 任务累计 ' + pipelineDuration(cumulative) + '（辅助统计）。';
      }
      const comparable = !nested && basis === "整段实际耗时" && duration !== null && total !== null && total > 0 && duration <= total;
      return {
        key, label, value: duration === null && waiting ? "待回传" : pipelineDuration(duration),
        basis, description, nested: Boolean(nested),
        share: comparable ? duration > 0 && duration / total * 100 < 0.1 ? "<0.1%" : `${(duration / total * 100).toFixed(1)}%` : null,
      };
    });

  const actualSummary = (duration: number | null): string => {
    if (duration === null) return waiting ? "待回传" : "未记录";
    const share = total !== null && total > 0 && duration <= total
      ? duration > 0 && duration / total * 100 < 0.1 ? "<0.1%" : `${(duration / total * 100).toFixed(1)}%` : null;
    return `实际经过 ${pipelineDuration(duration)}${share ? ` · 占后端 ${share}` : ""}`;
  };

  if (explicit || Object.keys(timings).length || combined) {
    const elapsed = phaseElapsed(source);
    overview.push({ label: combined ? "历史合并请求耗时" : elapsed.label,
      value: pipelineDuration(elapsed.seconds),
      note: combined ? "旧请求同时包含登记与处理，无法还原独立阶段墙钟。"
        : elapsed.label === "记录耗时" ? "未保存完整阶段起止，保留原记录口径。" : "阶段起止包含请求、处理与等待。" });
    if (explicit) overview.push({ label: "后端处理耗时", value: total === null && waiting ? "待回传" : pipelineDuration(total),
      note: "下方占比以此为分母；与实际总耗时的差额未细分。" });
  }

  if (explicit || ["source_memory_prepare", "source_memory_commit", "pipeline_core", "video_total", "video_keyframe_extraction"]
    .some(key => timings[key] != null)) {
    const core = rows([["pipeline_core", "媒体处理与事件总结", "整段实际耗时", "包含下列处理步骤；并发推理与串行提交会重叠。"]])[0];
    groups.push({ key: "core", title: "媒体处理与事件总结",
      summary: core ? actualSummary(seconds(timings.pipeline_core)) : undefined,
      note: "本段从开始处理媒体到事件总结结束，按实际经过时间计时。子阶段按最早开始至最晚结束计时，包含期间等待，占比以后端总耗时为分母。子阶段可重叠，占比不可相加；历史未采集首尾时标为未记录。",
      rows: rows([
        ["source_memory_prepare", "源媒体模型准备", "并行任务累计", "视觉向量、画面理解、人脸检测；多张媒体并行准备。"],
        ["source_memory_commit", "源媒体记忆提交", "串行累计", "地点、人脸归类、事件归属、文本向量、实体、事实与检索索引。"],
        ["video_total", "视频场景与关键帧处理", "串行累计", "含场景规划、抽帧、关键帧登记和记忆构建；细分见下表。"],
        ["event_summarize", "事件总结", "整段实际耗时", "媒体处理结束后的事件摘要。"],
      ]),
    });
    groups.push({ key: "video", title: "视频处理细分（仅视频）", collapsed: true,
      summary: actualSummary(seconds(timings.video_total_wall)),
      note: "这些阶段包含在「视频场景与关键帧处理」中；跨视频、关键帧取最早开始至最晚结束，占比以后端总耗时为分母，各项不可相加。",
      rows: rows([
        ["video_scene_planning", "视频场景规划", "串行累计", "读取视频信息并确定场景时间窗。"],
        ["video_scene_write", "视频场景登记", "串行累计", "创建或复用场景事件。"],
        ["video_keyframe_extraction", "关键帧提取", "串行累计", "ffmpeg 抽帧。"],
        ["video_keyframe_asset_write", "关键帧登记", "串行累计", "保存关键帧资产及其源视频关联。"],
        ["video_keyframe_memory", "关键帧记忆构建", "串行累计", "关键帧的画面理解、文字、人脸、向量与记忆处理。"],
      ]),
    });
    const finalizeParts = ["participant_projection", "memory_graph", "family_graph", "person_insights"].map(key => seconds(timings[key]));
    const finalizeSeconds = finalizeParts.every(value => value !== null)
      ? finalizeParts.reduce<number>((sum, value) => sum + (value as number), 0) : null;
    groups.push({ key: "finalize", title: "图谱与人物收尾", summary: actualSummary(finalizeSeconds),
      note: "核心处理结束后依次执行；汇总下方四个独立阶段的实际耗时，占比同样以后端处理耗时为分母。因果边判定已包含在记忆图构建中，不重复相加。",
      rows: rows([
        ["participant_projection", "参与者投影", "整段实际耗时", "将已确认人物关联到事件，并刷新参与者摘要。"],
        ["memory_graph", "记忆图构建", "整段实际耗时", "重建结构化记忆节点与关系，包含因果边判定。"],
        ["causal_graph", "其中：因果边判定", "包含项", "筛选规则信号并执行文本模型判定。", true],
        ["family_graph", "家庭图谱", "整段实际耗时", "推断家庭关系。"],
        ["person_insights", "人物画像", "整段实际耗时", "生成、刷新人物画像。"],
      ]),
    });
  } else {
    groups.push({ key: "legacy", title: "历史处理计时", note: "按原接口保存的口径展示；各条记录的来源范围见说明。",
      rows: rows([
        ["prepare", "等待模型准备", "主线程等待累计", "旧请求等待各个推理任务返回的时间，不等于模型推理累计。"],
        ["finish", "源媒体记忆提交与视频处理", "串行累计", "包括归属、记忆写入及视频处理，旧记录没有进一步拆分。"],
        ["summarize", "事件总结", "批末实际耗时", "旧合并请求的事件摘要。"],
        ["event_summarize", "事件总结", "批末实际耗时", "事件摘要。"],
        ["participant_refresh", "参与者刷新", "收尾实际耗时", "刷新已确认人物到事件的关联。"],
        ["memory_graph", "记忆图构建", "收尾实际耗时", "结构化记忆节点与关系。"],
        ["causal_graph", "其中：因果边判定", "包含项", "包含在记忆图构建中。", true],
        ["family_graph", "家庭图谱", "收尾实际耗时", "家庭关系推断。"],
        ["person_insights", "人物画像", "收尾实际耗时", "人物画像生成与刷新。"],
        ["other", "其余调度与处理", "旧接口差额", "旧接口记录的核心处理总计扣除已计时步骤后的差额。"],
      ], false),
    });
  }

  const metrics = record(phase.pipeline_metrics);
  const statistics = record(metrics.stage_timings);
  const metricRows: PipelineTimingRow[] = [];
  const add = (key: string, label: string, value: string, basis: string, description: string) =>
    metricRows.push({ key, label, value, basis, description, share: null, nested: false });
  const imageCount = seconds(metrics.image_count);
  const wall = phaseElapsed(phase).seconds;
  if (imageCount && wall !== null) add("image_average", "每张图片折算耗时", pipelineDuration(wall / imageCount), "吞吐折算",
    `记录阶段耗时 ÷ ${imageCount} 张图片；不是单图实际处理时延。`);
  else if (seconds(phase.average_seconds_per_photo) !== null) add("image_average", "每张图片折算耗时",
    pipelineDuration(seconds(phase.average_seconds_per_photo)), "历史统计", "历史记录未保存图片数，保留原均值。");
  if (seconds(metrics.effective_workers) !== null) add("workers", "实际图片并发", `${metrics.effective_workers} 路`, "并发配置",
    `配置 ${metrics.configured_workers ?? "未记录"} · 模型并发上限 ${metrics.vllm_max_num_seqs ?? "未记录"}`);
  if (seconds(metrics.event_summary_call_count) !== null) add("summary_calls", "事件总结调用", `${metrics.event_summary_call_count} 次`, "调用统计",
    `事件 ${metrics.event_count ?? "未记录"} 个 · 批次耗时 ${pipelineDuration(seconds(metrics.event_summary_wall_seconds))}`);
  for (const [key, label] of [
    ["vlm_image_description_seconds", "VLM 图片描述"], ["face_detection_seconds", "人脸检测"],
    ["image_clip_seconds", "图片向量"], ["face_clustering_seconds", "人脸归类"],
    ["event_clustering_seconds", "事件聚类"], ["text_embedding_seconds", "文本向量"],
  ]) {
    const stat = record(statistics[key!]);
    if (!Object.keys(stat).length) continue;
    add(key!, label!, pipelineDuration(seconds(stat.mean_seconds)), "单次均值",
      `${stat.count ?? "未记录"} 次 · 累计 ${pipelineDuration(seconds(stat.sum_seconds))} · P95 ${pipelineDuration(seconds(stat.p95_seconds))}`);
  }
  if (metricRows.length) groups.push({ key: "statistics", title: "调用与并发统计", note: "统计项不作为额外执行步骤，也不与上面的耗时重复相加。", rows: metricRows });
  return { overview, groups: groups.filter(group => group.rows.length), combined };
}
