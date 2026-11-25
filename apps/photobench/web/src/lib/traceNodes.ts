import type { RunItemDetail } from "../api/types.ts";
import { debugCallForCall, runtimeTurns, turnModelCalls, turnProgressEvents, turnDebugCalls, type Dict, type TraceTimelineEntry } from "./agentTrace.ts";
import { finiteMetric, fmtMs } from "./format.ts";
import { toolDurationMs } from "./trace.ts";

export interface TraceSource { path: string; value: unknown; kind: "debug" | "metrics" | "event" | "turn" | "legacy" }
const object = (value: unknown): Dict => value && typeof value === "object" && !Array.isArray(value) ? value as Dict : {};
const rows = (value: unknown): Dict[] => Array.isArray(value) ? value.filter(v => v && typeof v === "object") as Dict[] : [];

export function traceNodeTitle(entry: TraceTimelineEntry): string {
  if (entry.kind === "answer") return "本轮回答";
  if (entry.kind === "tool") return String(entry.tool?.tool ?? entry.tool?.tool_name ?? "未知工具");
  if (entry.kind === "planner") return "规划器调用";
  if (entry.kind === "judge") return entry.call?.call_type === "claim_verifier" ? "事实核验调用" : "答案核验调用";
  if (entry.kind === "model") return entry.call?.call_type === "tool_internal" ? "工具内部模型调用" : "Agent 模型调用";
  return String(entry.event?.kind ?? entry.event?.type ?? "运行事件");
}

/** 每个节点仅显示自身时长；模型与关联工具的合计另列，避免重复计时。 */
export function traceNodeDuration(entry: TraceTimelineEntry): number | null {
  if (entry.call) return finiteMetric(entry.call.duration_ms ?? entry.call.total_ms);
  if (entry.tool) return toolDurationMs(entry.tool);
  return finiteMetric(entry.event?.duration_ms ?? object(entry.event?.payload).duration_ms);
}
export function traceDurationLabel(entry: TraceTimelineEntry): string {
  return entry.kind === "answer" ? "回合结果" : traceNodeDuration(entry) == null ? "耗时未记录" : `耗时 ${fmtMs(traceNodeDuration(entry))}`;
}

function sameCall(raw: Dict, call: Dict): boolean {
  if (raw.call_id && call.call_id) return raw.call_id === call.call_id;
  return raw.step_id != null && raw.step_id === call.step_id &&
    String(raw.call_type ?? "agent") === String(call.call_type ?? "agent") &&
    (call.attempt == null || raw.attempt === call.attempt);
}

/** 返回保存对象本身及其路径，绝不把指标与调试对象的合并投影冒充原文。 */
export function traceNodeSources(item: RunItemDetail, turnIndex: number, entry: TraceTimelineEntry): TraceSource[] {
  const savedTurns = rows(item.runtime_turns);
  const position = savedTurns.findIndex((turn, index) => Number(turn.index ?? index) === turnIndex);
  const turn = position >= 0 ? savedTurns[position] : runtimeTurns(item).find((t, i) => Number(t.index ?? i) === turnIndex);
  const base = position >= 0 ? `runtime_turns[${position}]` : "$";
  const sources: TraceSource[] = [];
  const addUnique = (records: Dict[], path: string, call: Dict, kind: TraceSource["kind"]) => {
    const found = records.map((value, index) => ({ value, index })).filter(({ value }) => sameCall(value, call));
    if (found.length === 1) sources.push({ path: `${path}[${found[0].index}]`, value: found[0].value, kind });
  };
  if (entry.call) {
    const debug = typeof entry.call._debugCallIndex === "number" ?
      turnDebugCalls(turn)[entry.call._debugCallIndex] : debugCallForCall(item, { ...entry.call, conversation_turn: turnIndex });
    const bundle = object(turn?.trace_bundle);
    const debugIndex = rows(bundle.debug_calls).indexOf(debug!);
    if (debugIndex >= 0) sources.push({ path: `${base}.trace_bundle.debug_calls[${debugIndex}]`, value: debug, kind: "debug" });
    const globalIndex = entry.call._globalCallIndex;
    if (typeof globalIndex === "number" && rows(item.model_call_metrics)[globalIndex]) {
      sources.push({ path: `model_call_metrics[${globalIndex}]`, value: rows(item.model_call_metrics)[globalIndex], kind: "metrics" });
    } else {
      const metrics = rows(item.model_call_metrics);
      const found = metrics.map((value, index) => ({ value, index })).filter(({ value }) => Number(value.conversation_turn ?? 0) === turnIndex && sameCall(value, entry.call!));
      if (found.length === 1) sources.push({ path: `model_call_metrics[${found[0].index}]`, value: found[0].value, kind: "metrics" });
    }
    addUnique(rows(bundle.model_calls), `${base}.trace_bundle.model_calls`, entry.call, "metrics");
    if (!sources.length) {
      const legacyIndex = entry.key.startsWith(`legacy-${turnIndex}-`) ? Number(entry.key.split("-").at(-1)) : -1;
      const legacy = rows(turn?.debug_trace);
      if (legacyIndex >= 0 && legacy[legacyIndex]) sources.push({ path: `${base}.debug_trace[${legacyIndex}]`, value: legacy[legacyIndex], kind: "legacy" });
      const execution = rows(item.execution_trace);
      const matches = execution.map((value, index) => ({ value, index })).filter(({ value }) => Number(value.conversation_turn ?? 0) === turnIndex && sameCall(value, entry.call!));
      if (matches.length === 1) sources.push({ path: `execution_trace[${matches[0].index}]`, value: matches[0].value, kind: "legacy" });
    }
  } else if (entry.tool) {
    const index = rows(item.tool_trace).indexOf(entry.tool);
    if (index >= 0) sources.push({ path: `tool_trace[${index}]`, value: entry.tool, kind: "debug" });
    const id = entry.tool.tool_call_id ?? entry.tool.step_id;
    turnProgressEvents(turn).forEach((event, index) => {
      const payload = object(event.payload);
      if (id != null && (payload.tool_call_id ?? payload.step_id) === id) sources.push({ path: `${base}.trace[${index}]`, value: event, kind: "event" });
    });
  } else if (entry.kind === "answer") {
    sources.push({ path: base, value: position >= 0 ? savedTurns[position] : item, kind: "turn" });
  } else if (entry.event) {
    const events = turnProgressEvents(turn);
    const index = events.indexOf(entry.event);
    if (index >= 0) sources.push({ path: `${base}.trace[${index}]`, value: entry.event, kind: "event" });
  }
  return sources;
}

/** 比较完整消息对象，包含未知字段、空值和原参数字符串。 */
export function sameTraceValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (a == null || b == null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const left = Object.keys(a), right = Object.keys(b);
  return left.length === right.length && left.every(key => Object.hasOwn(b, key) && sameTraceValue((a as Dict)[key], (b as Dict)[key]));
}

export function repeatedMessagePrefix(item: RunItemDetail, turnIndex: number, call: Dict): { count: number; previous: Dict | null } {
  const calls = turnModelCalls(item, turnIndex).filter(c => c.call_type === call.call_type);
  const turn = runtimeTurns(item).find((t, i) => Number(t.index ?? i) === turnIndex);
  const debugIndex = turnDebugCalls(turn).indexOf(call);
  const matches = calls.map((c, index) => ({ c, index })).filter(({ c }) =>
    debugIndex >= 0 ? c._debugCallIndex === debugIndex : call.call_id ? c.call_id === call.call_id : sameCall(c, call));
  const index = matches.length === 1 ? matches[0].index : -1;
  const previous = index > 0 ? calls[index - 1] : null;
  const messages = Array.isArray(call.messages) ? call.messages : [];
  const earlier = Array.isArray(previous?.messages) ? previous.messages : [];
  let count = 0;
  while (count < Math.min(messages.length, earlier.length) && sameTraceValue(messages[count], earlier[count])) count++;
  return { count, previous };
}
