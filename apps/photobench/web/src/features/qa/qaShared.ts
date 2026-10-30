/**
 * 「逐题结果」视图层的就地辅助。1.0 里这些纯函数与逐题模板同处 `App.vue`：
 * `judgeReason`(:607) / `unboundTools`(:2058) / `toolStatusLabel`(:2061) /
 * `terminationDisplayLabel`(:2112) / `hasToolTrace`(:2118)。
 *
 * 2.0 的 `lib/` 没有收它们（`details.ts` 只收了 `normalizedTerminationReason`，
 * `trace.ts` 只收了 `normalizedToolCallIndex` / `itemToolTrace`），所以在这里照搬，
 * 逻辑一字未改；组合它们用的仍是 `lib/` 里的权威实现。
 */
import type { CSSProperties } from "react";

import type { ItemPage, Judge } from "../../api/types.ts";
import { normalizedTerminationReason } from "../../lib/details.ts";
import { executionState, turnTerminationLabel } from "../../lib/format.ts";
import { itemToolTrace, latencySegmentStyle, normalizedToolCallIndex } from "../../lib/trace.ts";
import type { LatencySegment } from "../../lib/trace.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 `:607-610`。`not_applicable` / `no_answer` 视为「无说明」。 */
export function judgeReason(judge: unknown): string {
  const reason = dict(judge).reason;
  if (!reason || (["not_applicable", "no_answer"] as unknown[]).includes(reason)) return "";
  return String(reason);
}

/** 1.0 `:2058-2060`。绑不到模型调用的工具 trace。 */
export function unboundTools(item: unknown): Dict[] {
  return itemToolTrace(item).filter(
    (trace, traceIndex) => normalizedToolCallIndex(item, trace, traceIndex) == null,
  );
}

/** 1.0 `:2061-2064`。 */
export function toolStatusLabel(trace: unknown): string {
  const t = dict(trace);
  const status = String(t.status || "未知");
  return t.reason ? `${status} · ${String(t.reason)}` : status;
}

/** 1.0 `:2112-2116`。 */
export function terminationDisplayLabel(item: unknown): string {
  const raw = normalizedTerminationReason(item);
  if (raw === "complete" || (raw === "-" && executionState(item).key === "complete")) return "正常完成";
  if (raw === "-") return "未记录";
  return turnTerminationLabel({ ...dict(item), termination_reason: raw });
}

/** 1.0 `:2118-2120`。 */
export function hasToolTrace(item: unknown): boolean {
  return (
    Object.prototype.hasOwnProperty.call(item || {}, "tool_trace") ||
    dict(dict(item).timing_breakdown).tool_trace_recorded === true
  );
}

/** 时延色块的内联样式：`lib/trace.ts` 给 `flex` + CSS 变量，这里补上 React 的类型。 */
export function segmentStyle(segment: LatencySegment, segments: LatencySegment[]): CSSProperties {
  const style = latencySegmentStyle(segment, segments);
  return { flex: style.flex, "--segment-share": style["--segment-share"] } as CSSProperties;
}

/**
 * 逐题摘要里模板直接读、但 `RunItemSummary` 未收窄的字段（1.0 无类型，字段都在响应里）。
 */
export interface QaSummaryView {
  display_question?: string;
  conversation_turn_count?: number;
  index: number;
  question?: string;
  judge?: Judge;
  evidence_judge?: Judge;
  ground_truth_count?: number;
  retrieval_precision?: number;
  retrieval_recall?: number;
  retrieval_f1?: number;
  matched_count?: number;
  model_call_count?: number;
  tool_call_count?: number;
}

/** `qaPage.facets`（1.0 逐题筛选下拉的候选值来源 `:3449-3455`）。 */
export interface QaFacets {
  task_types?: string[];
  question_types?: string[];
  tags?: string[];
  angles?: string[];
  difficulties?: string[];
  answerabilities?: string[];
  agent_statuses?: string[];
  attribution_layers?: string[];
}

/** `ItemPage` 里模板用到、但类型未声明的分页 / 计数 / 面元字段。 */
export type QaPageView = ItemPage & {
  unfiltered_total?: unknown;
  has_previous?: boolean;
  has_next?: boolean;
  facets?: QaFacets;
};

/**
 * 人工复核草稿（1.0 `reviewDrafts[qaId]`）。1.0 由 run 详情层（`loadActiveRun` `:2296`）
 * 装载、`saveReviews`(`:2338`) 写回，**不在** `useQaPage` 里；组件按可选 props 接收。
 */
export interface ReviewDraft {
  verdict?: string;
  note?: string;
}
