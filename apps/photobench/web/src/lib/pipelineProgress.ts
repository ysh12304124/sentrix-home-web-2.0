import type { Phase, Run } from "../api/types.ts";
import { isCombinedImport } from "./pipelineTiming.ts";

type StepState = "pending" | "running" | "done" | "stopped";
export interface PipelineProgressView {
  status: string;
  label: string;
  detail: string;
  steps: Array<{ label: string; state: StepState; fraction: number | null }>;
  warning: string | null;
  combined: boolean;
}
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
const done = (status: unknown) => status === "done" || status === "completed";
const stopped = (status: unknown) => ["failed", "cancelled", "interrupted", "stalled", "partial", "completed_with_errors"].includes(String(status));
const stageLabels: Record<string, string> = {
  scanning: "扫描目录", media_staging: "读取媒体", media_processing: "媒体处理",
  event_assignment: "事件归属", event_summarization: "事件总结",
  participant_refresh: "更新事件参与者", family_graph: "家庭图谱", person_insights: "人物画像",
  finalizing: "汇总与构建", completed: "等待完成确认",
};
const finalStages = new Set(["event_summarization", "participant_refresh", "family_graph", "person_insights", "finalizing", "completed"]);

/** 以真实阶段表示整体进度；资产计数仅填充媒体段，不估算剩余时间。 */
export function pipelineProgressView(run: Run, now: number): PipelineProgressView {
  const pipeline: Phase = run.phases?.pipeline_processing ?? {};
  const imported: Phase = run.phases?.photo_import ?? {};
  const combined = pipeline.execution_mode !== "explicit" && isCombinedImport(imported);
  const progress = (combined ? imported : pipeline).progress ?? {};
  const observation = combined ? progress.observation : null;
  const stage = String(observation?.stage ?? progress.stage ?? "");
  const total = count(observation ? observation.total : progress.total);
  const processed = count(observation ? observation.completed : progress.processed);
  const failed = count(observation ? observation.failed : progress.failed);
  const skipped = count(progress.skipped);
  const settled = processed === null ? null : observation ? processed : processed + (failed ?? 0) + (skipped ?? 0);
  let fraction = total !== null && total > 0 && settled !== null ? Math.min(1, settled / total) : null;
  const directories = count(progress.directories_total);
  const directoriesDone = count(progress.directories_done);
  if (combined && directories && directoriesDone !== null) {
    fraction = Math.min(1, (directoriesDone + (directoriesDone < directories ? fraction ?? 0 : 0)) / directories);
  }
  const lastDirectory = !combined || (directories !== null && directoriesDone !== null && directoriesDone >= directories - 1);
  const importEnded = done(imported.status) || imported.status === "partial";
  const returned = combined ? importEnded : progress.request_status === "returned";
  const importRunning = combined && imported.status === "running";
  const success = done(pipeline.status) && !importRunning && (!combined || !stopped(imported.status));
  let status = importRunning ? "running" : pipeline.status ?? (combined ? imported.status : "pending") ?? "pending";
  if (combined && stopped(imported.status)) status = String(imported.status);
  if (!success && !stopped(status) && stopped(run.status)) status = String(run.status);
  const active = status === "running" || (!success && returned && !stopped(status));
  if (active) status = "running";
  const interrupted = stopped(status);
  const mediaEnded = success || returned || (lastDirectory && (finalStages.has(stage) || (total !== null && total > 0 && settled !== null && settled >= total)));
  const finalizeEnded = success || returned;
  const current = finalizeEnded ? 2 : mediaEnded ? 1 : 0;
  const states: StepState[] = [0, 1, 2].map(index => success || index < current ? "done"
    : index === current && interrupted ? "stopped" : index === current && active ? "running" : "pending");
  const label = success ? "流水线完成" : interrupted ? ({ partial: "处理结束，有异常", completed_with_errors: "处理结束，有异常", failed: "处理失败", cancelled: "已取消", interrupted: "已中断", stalled: "处理停滞" }[status] ?? "处理停止")
    : active ? (current === 2 ? "完成确认中" : current === 1 && !finalStages.has(stage) ? "汇总与构建" : stageLabels[stage] ?? "媒体处理中") : "等待处理";
  const detail = observation
    ? `当前目录 ${progress.current_directory ?? "未记录"} · 已处理 ${processed ?? "?"}/${total ?? "?"} · 失败 ${failed ?? "?"}${directories ? ` · 已完成目录 ${directoriesDone ?? "?"}/${directories}` : ""}`
    : `媒体成功 ${processed ?? "?"}/${total ?? "?"} · 失败 ${failed ?? "?"} · 跳过 ${skipped ?? "?"}`;
  const received = typeof progress.last_received_at === "string" ? Date.parse(progress.last_received_at) : NaN;
  const warning = progress.request_error ? `请求异常：${progress.request_error}`
    : combined && progress.availability && progress.availability !== "available" ? `进度查询失败：${progress.query_error ?? progress.availability}；显示上次记录。`
    : active && Number.isFinite(received) && now - received > 10000 ? "进度超过10秒未更新，显示上次记录。" : null;
  return { status: success ? "done" : status, label, detail, combined, warning,
    steps: ["媒体处理", "汇总与构建", "完成确认"].map((label, index) => ({
      label, state: states[index]!, fraction: states[index] === "done" ? 1 : index === 0 ? fraction : null,
    })),
  };
}
