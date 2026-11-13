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

function agentDuration(qa: Phase, now: number): number | null {
  const recorded = seconds(qa.agent_phase_wall_ms);
  if (recorded !== null) return recorded / 1000;
  const start = typeof qa.agent_phase_started_at === "string" ? Date.parse(qa.agent_phase_started_at) : NaN;
  const finish = typeof qa.agent_phase_finished_at === "string" ? Date.parse(qa.agent_phase_finished_at) : NaN;
  if (!Number.isFinite(start)) return null;
  if (Number.isFinite(finish)) return finish >= start ? (finish - start) / 1000 : null;
  return qa.status === "running" ? Math.max(0, (now - start) / 1000) : null;
}

/** 只投影已有计时，不用 worker 累计时间推算关键路径或补齐历史。 */
export function pipelineTimingView(
  phase: Phase = {}, imported: Phase = {},
  options: { qa?: Phase; graphEnabled?: boolean; now?: number } = {},
): PipelineTimingView {
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

  if (explicit || ["source_memory_prepare", "source_memory_commit", "pipeline_core", "video_total", "video_total_wall",
    "video_keyframe_extraction", "video_keyframe_memory"]
    .some(key => timings[key] != null)) {
    const agentSeconds = options.qa ? agentDuration(options.qa, options.now ?? Date.now()) : null;
    const pipelineWall = phaseElapsed(phase);
    const hasVideoStage = ["video_total", "video_total_wall", "video_keyframe_extraction", "video_keyframe_memory"]
      .some(key => timings[key] != null);
    const flowRows = rows([
      ...(hasVideoStage ? [[
        "video_total", "媒体/关键帧处理", "基础记忆子阶段",
        "视频场景规划、抽帧、关键帧资产写入和关键帧记忆构建；耗时已包含在下方基础记忆构建总墙钟内，不再相加。",
      ] as Definition] : []),
      ["pipeline_core", "基础记忆构建（含关键帧）", "整段实际耗时", "源媒体记忆、关键帧处理与事件总结的整段流水线窗口；这是全流程的主计时，不能再与上面的子阶段相加。"],
      ["memory_graph", "图结构构建", "整段实际耗时", "普通图片、视频关键帧、语义/因果边及图写入的合计。"],
    ]);
    if (options.graphEnabled === false) {
      const graph = flowRows.find(row => row.key === "memory_graph");
      if (graph) { graph.value = "已关闭"; graph.share = null; }
    }
    if (options.qa) flowRows.push({
      key: "qa_agent", label: "检索与推理（Agent）",
      value: agentSeconds === null
        ? options.qa.status === "pending" ? "待开始" : options.qa.status === "running" ? "待回传" : "未记录"
        : pipelineDuration(agentSeconds),
      basis: "QA 阶段实际经过", description: "包含检索工具、模型回答及其等待；Judge 评分另行执行，不计入此行。",
      share: null, nested: false,
    });
    const memoryBuildSeconds = seconds(timings.pipeline_core);
    const graphSeconds = options.graphEnabled === false ? 0 : seconds(timings.memory_graph) ?? 0;
    const flowBuildSeconds = memoryBuildSeconds === null ? null : memoryBuildSeconds + graphSeconds;
    const flowTotal = flowBuildSeconds !== null && agentSeconds !== null
      ? `记忆构建＋Agent 合计 ${pipelineDuration(flowBuildSeconds + agentSeconds)}`
      : flowBuildSeconds !== null
        ? `记忆构建合计 ${pipelineDuration(flowBuildSeconds)}`
        : pipelineWall.label === "实际总耗时" && agentSeconds !== null
          ? `流水线＋Agent 合计 ${pipelineDuration(pipelineWall.seconds! + agentSeconds)}`
          : undefined;
    groups.push({
      key: "memory_flow", title: "记忆构建全流程", summary: flowTotal,
      note: "媒体/关键帧处理是基础记忆构建的子阶段，已包含在基础记忆构建主值中；图结构构建随后执行，检索与推理属于 QA Agent 阶段。总计只相加独立阶段墙钟；明细行的阶段跨度和任务累计可能重叠，不再相加。",
      rows: flowRows,
    });
    const core = rows([["pipeline_core", "基础记忆构建明细", "整段实际耗时", "包含下列源媒体处理与事件总结步骤；子阶段可能重叠。"]])[0];
    groups.push({ key: "core", title: "基础记忆构建明细",
      summary: core ? actualSummary(seconds(timings.pipeline_core)) : undefined,
      note: "本段是基础记忆构建的诊断明细；媒体/关键帧处理已在上方全流程列出。主值是各环节的阶段跨度，括号中的任务累计是所有任务执行时间；两者都不是可直接相加的独立阶段，历史未采集首尾时标为未记录。",
      rows: rows([
        ["source_memory_prepare", "源媒体模型准备", "并行阶段跨度", "多张媒体同时执行视觉向量、画面理解和人脸检测；主值是首个任务开始到最后任务结束的跨度，任务累计是所有 worker 执行时间总和。", true],
        ["source_memory_commit", "源媒体记忆提交", "顺序提交跨度", "按媒体顺序落库以保证事件归属；主值包含提交之间等待并行结果的跨度，任务累计是实际提交操作总和。", true],
        ["event_summarize", "事件总结", "事件总结阶段墙钟", "媒体处理结束后按事件执行摘要生成。", true],
      ]),
    });
    const finalizeParts = ["participant_projection", "memory_graph", "family_graph", "person_insights"].map(key => seconds(timings[key]));
    const finalizeSeconds = seconds(timings.finalize_wall) ?? (finalizeParts.every(value => value !== null)
      ? finalizeParts.reduce<number>((sum, value) => sum + (value as number), 0) : null);
    groups.push({ key: "finalize", title: "图谱与人物收尾", summary: actualSummary(finalizeSeconds),
      note: "核心处理后的收尾阶段实际耗时；其中图结构构建已在上方全流程列出，不在此重复展示。",
      rows: rows([
        ["participant_projection", "参与者投影", "整段实际耗时", "将已确认人物关联到事件，并刷新参与者摘要。"],
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
