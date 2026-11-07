/** Compact navigation over the existing saved timeline; references bind only to unique recorded calls. */
import { runtimeTurns, turnTimeline, type Dict, type TraceTimelineEntry } from "../../lib/agentTrace.ts";
import { toolLabel } from "./presentation.ts";
import type { RunItemDetail } from "../../api/types.ts";

export interface JourneyNode {
  id: string; turn: number; title: string; status: string; failed: boolean;
  entry: TraceTimelineEntry; refs: string[];
}
export const object = (value: unknown): Dict => value && typeof value === "object" && !Array.isArray(value) ? value as Dict : {};
export const text = (value: unknown): string => typeof value === "string" ? value : "";
export function atPath(item: unknown, path: string): unknown {
  if (path === "case") return null;
  if (!path) return item;
  return path.replace(/\[(\d+)\]/g, ".$1").split(".").reduce<unknown>((v, key) => v && typeof v === "object" ? (v as Dict)[key] : undefined, item);
}
function sameCall(raw: Dict, call: Dict): boolean {
  if (raw.call_id && call.call_id) return raw.call_id === call.call_id;
  return !!raw.step_id && raw.step_id === call.step_id &&
    String(raw.call_type || "agent") === String(call.call_type || "agent") && raw.attempt === call.attempt;
}
export function journeyNodes(item: RunItemDetail, locations: Record<string, string>): JourneyNode[] {
  const nodes = runtimeTurns(item).flatMap((turn, ordinal) => {
    const ti = Number(turn.index ?? ordinal);
    return turnTimeline(item, ti).filter(e => e.kind !== "event").map((entry): JourneyNode => {
      const raw = entry.call || entry.tool || {};
      const failed = !!raw.error || ["error", "failed", "partial", "denied", "cancelled"].includes(String(raw.status));
      const title = entry.kind === "answer" ? "最终回答" : entry.kind === "tool" ? toolLabel(String(raw.tool || raw.tool_name || "未命名工具")) :
        entry.kind === "planner" ? "任务规划" : entry.kind === "judge" ? "答案核验" : raw.call_type === "claim_verifier" ? "事实核验" : raw.call_type === "tool_internal" ? "工具内模型" : "模型推理";
      const status = entry.kind === "answer" ? (entry.answer?.trim() ? "已记录回答" : "回答未记录") : failed ? "调用异常" :
        ["ok", "success", "complete", "completed"].includes(String(raw.status)) ? "调用完成" : "已记录调用";
      return {id:entry.key, turn:ti, title, status, failed, entry, refs:[]};
    });
  });
  if (text(item.error)) nodes.unshift({
    id: "recorded-execution-error", turn: 0, title: "答题请求失败", status: "已记录错误", failed: true,
    entry: {kind: "event", key: "recorded-execution-error", event: {_recorded_execution: true, error: item.error}}, refs: [],
  });
  for (const [ref, path] of Object.entries(locations)) {
    const match = /^runtime_turns\[(\d+)\]/.exec(path);
    const turn = match ? Number(match[1]) : Number(object(atPath(item, path)).conversation_turn ?? 0);
    const raw = object(atPath(item, path));
    const isDebug = /debug_calls\[\d+\]$/.test(path);
    const isModel = /(?:model_call_metrics|model_calls)\[\d+\]$/.test(path);
    const isTool = /tool_trace\[\d+\]$/.test(path);
    const candidates = nodes.filter(n => n.turn === turn && (
      ref.endsWith(":execution") && n.entry.kind === "event" && n.entry.event?._recorded_execution === true ||
      (isDebug || isModel) && n.entry.call && (isModel && /^model_call_metrics\[(\d+)\]$/.test(path)
        ? n.entry.call._globalCallIndex === Number(/\[(\d+)\]/.exec(path)![1])
        : sameCall(raw, n.entry.call)) ||
      isTool && n.entry.tool && (raw === n.entry.tool || !!raw.tool_call_id && raw.tool_call_id === n.entry.tool.tool_call_id || !!raw.step_id && raw.step_id === n.entry.tool.step_id) ||
      /^runtime_turns\[\d+\]$/.test(path) && n.entry.kind === "answer"
    ));
    if (candidates.length === 1) candidates[0].refs.push(ref);
  }
  return nodes;
}
export function cleanNotes(value: string | string[] | undefined): string[] {
  return (Array.isArray(value) ? value : value ? [value] : []).filter(s => s.trim() && !/^(无|暂无|无额外限制|无特别限制|none|null|n\/a|不适用)[。.]?$/i.test(s.trim()));
}
export function evidenceName(ref: string, nodes: JourneyNode[]): string {
  const node = nodes.find(n => n.refs.includes(ref));
  if (node) return `第${node.turn + 1}轮 · ${node.title}`;
  const gt = /:t(\d+):gt(\d+)$/.exec(ref);
  if (gt) return `第${Number(gt[1])+1}轮 · GT 图片 ${Number(gt[2])+1}`;
  if (ref.endsWith(":execution")) return "答题请求与错误";
  if (ref.endsWith(":task_contract")) return "参考要求";
  if (ref.endsWith(":case")) return "题目与回答";
  if (ref.endsWith(":outcome")) return "最终执行状态";
  const turn = /:turn(\d+)$/.exec(ref);
  if (turn) return `第${Number(turn[1]) + 1}轮回答`;
  if (/:e\d+$/.test(ref)) return "异常记录";
  return "查看来源记录";
}
