// 移植自 1.0 `services/photobench/frontend/src/App.vue`（只读，唯一权威）中
// trace / 时延 / 调试轨迹的解析与格式化（1.0 :1340-1809）。
//
// 口径纪律：1.0 里 `null` / `NaN` / `0` / `""` / `undefined` 在每个分支的判定各不相同，
// 保留1.0字段读取；2.0按同轮调用身份和独立Agent墙钟解释，不补造原始数据。
//
// 共享的纯格式化函数（`callType` / `callTypeLabel` / `fmtMs` / `average`）已由
// `lib/format.ts` 移植（1.0 :334 / :1402 / :1444 / :1447），这里 import 复用，
// 不重复定义，避免两份 callType 各自漂移。
//
// 1.0 的辅助函数 `conversationTurns` 不在本切片（:1340-1809）内，但上面的函数依赖它。
// 其权威实现在 `lib/evidence.ts`（1.0 :611），这里 import 复用，不在本模块复制。
//
// `itemTimingBreakdown`（1.0 :2122-2133）本就是被本模块的时延归因消费的，权威实现已移入
// 本模块（见下方「时延归因」段）；`lib/details.ts` 不再持有它，依赖由此单向
// `details.ts → trace.ts`，原先 trace ↔ details 的双向 import 消除。

import type { MediaLike } from "../api/media.ts";
import { conversationTurns } from "./evidence.ts";
import { average, callType, callTypeLabel, finiteMetric, fmtMs } from "./format";

type Dict = Record<string, unknown>;

/** 1.0 的可选链语义：非对象一律当空对象，取属性得 undefined。 */
function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 的回退链用 `||`，参与比较的都是字符串字段；非字符串折成空串。 */
function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** 1.0 `Array.isArray(x) ? x.filter(o => o && typeof o === "object") : []`。 */
function objectList(value: unknown): Dict[] {
  return Array.isArray(value)
    ? value.filter((item): item is Dict => typeof item === "object" && item !== null)
    : [];
}

// ---------------------------------------------------------------------------
// 返回形状
// ---------------------------------------------------------------------------

export interface AgentLoopGroup {
  turnIndex: number;
  turn: Dict;
  calls: Dict[];
}

export interface ToolLatencySegment {
  kind: string;
  label: string;
  ms: number;
}

export interface LatencySegment {
  kind: string;
  label: string;
  shortLabel?: string;
  ms: number;
  trace?: Dict;
  call?: Dict;
}

export interface AgentLoopTiming {
  modelMs: number | null;
  ttftMs: number | null;
  generationMs: number | null;
  toolMs: number;
  totalMs: number | null;
  tools: Dict[];
}

export interface ItemLlmSummary {
  call_count: number;
  streamed_count: number;
  ttft_ms_avg: number | null;
  total_ms_sum: number;
  prompt_tokens_total: number;
  completion_tokens_total: number;
  tokens_per_second_avg: number | null;
}

export interface LatencySegmentStyle {
  flex: string;
  "--segment-share": string;
}

export interface AttributionSummary {
  primary: string;
  failed: string;
}

// ---------------------------------------------------------------------------
// 1.0 :1335-1348 访问器
// ---------------------------------------------------------------------------

/** 1.0 `:1335-1339` */
export function itemCallMetrics(item: unknown): Dict[] {
  return objectList(dict(item).model_call_metrics);
}

/** 1.0 `:1340-1344` */
export function itemExecutionTrace(item: unknown): Dict[] {
  return objectList(dict(item).execution_trace);
}

/** 1.0 `:1345-1348` —— 只接受非负整数（含数字字符串），否则回 `fallback`。 */
export function conversationTurnNumber(value: unknown, fallback = 0): number {
  const turn = Number(value);
  return Number.isInteger(turn) && turn >= 0 ? turn : fallback;
}

// ---------------------------------------------------------------------------
// 1.0 :1349-1382 调用树归并
// ---------------------------------------------------------------------------

/** 1.0 `:1349-1380` —— 已存调用 + 为失败 trace 步补出的 `_traceOnly` 合成调用，按轮分组。 */
export function agentLoopGroups(item: unknown): AgentLoopGroup[] {
  const savedCalls: Dict[] = itemCallMetrics(item)
    .map((call, globalIndex) => ({ ...call, _globalCallIndex: globalIndex }))
    .filter((call) => callType(call) !== "tool_internal");
  const traceModels = itemExecutionTrace(item).filter((step) =>
    ["model", "writer", "judge"].includes(String(step.stage || step.type || "")),
  );
  const turns = conversationTurns(item as MediaLike | null | undefined);
  const knownTurns = new Set<number>();
  savedCalls.forEach((call) => knownTurns.add(conversationTurnNumber(call.conversation_turn)));
  traceModels.forEach((step) => knownTurns.add(conversationTurnNumber(step.conversation_turn)));
  turns.forEach((_, index) => knownTurns.add(index));
  if (!knownTurns.size) knownTurns.add(0);
  const metricKeys = new Set(
    savedCalls.map((call) => `${conversationTurnNumber(call.conversation_turn)}:${str(call.step_id)}`),
  );
  const traceOnlyCalls: Dict[] = traceModels
    .filter((step) => {
      if (String(step.call_type || "") === "tool_internal") return false;
      const key = `${conversationTurnNumber(step.conversation_turn)}:${str(step.step_id)}`;
      return (
        (step.status === "error" || step.status === "failed" || step.parse_status === "failed") &&
        !metricKeys.has(key)
      );
    })
    .map((step, index) => {
      const turnIndex = conversationTurnNumber(step.conversation_turn);
      const turn = turns[turnIndex] || {};
      return {
        ...step,
        conversation_turn: turnIndex,
        call_type: step.call_type || "agent",
        status: step.status || "error",
        turn_outcome: step.turn_outcome || turn.turn_outcome || "model_error",
        next_step: step.next_step || turn.next_step,
        call_observation: step.call_observation || {
          kind: step.call_type || "agent",
          purpose: "模型调用在生成性能指标前失败",
          trigger: turn.message || "当前对话轮",
          outcome: turn.termination_reason || step.error || step.detail || "调用失败",
          source: "execution_trace_only",
        },
        _traceOnly: true,
        _globalCallIndex: `trace-${index}`,
      };
    });
  return [...knownTurns]
    .sort((a, b) => a - b)
    .map((turnIndex) => ({
      turnIndex,
      turn: turns[turnIndex] || {},
      calls: [...savedCalls, ...traceOnlyCalls].filter(
        (call) => conversationTurnNumber(call.conversation_turn) === turnIndex,
      ),
    }));
}

/** 1.0 `:1381-1383` */
export function showAgentLoopGroupHeaders(item: unknown): boolean {
  return conversationTurns(item as MediaLike | null | undefined).length > 1 || agentLoopGroups(item).length > 1;
}

/** 1.0 `:1399-1401` */
export function turnRecoveryCount(group: unknown): number {
  const calls = objectList(dict(group).calls);
  return calls.filter((call) => callType(call) === "recovery").length;
}

// ---------------------------------------------------------------------------
// 1.0 :1406-1420 单题 LLM 汇总
// ---------------------------------------------------------------------------

/**
 * 1.0 `:1406-1420` —— 优先用已存 `item.llm_summary`，缺项现算。
 * 无调用回 `null`；`saved` 里数值为 `0` 时被认作有效值。
 */
export function itemLlmSummary(item: unknown): ItemLlmSummary | null {
  const calls = itemCallMetrics(item);
  if (!calls.length) return null;
  const saved = dict(dict(item).llm_summary);
  const numeric = (key: string, fallback: number): number =>
    finiteMetric(saved[key]) ?? fallback;
  const numericOrNull = (key: string, fallback: number | null): number | null =>
    finiteMetric(saved[key]) ?? fallback;
  return {
    call_count: numeric("call_count", calls.length),
    streamed_count: numeric("streamed_count", calls.filter((call) => dict(call).streamed === true).length),
    ttft_ms_avg: numericOrNull("ttft_ms_avg", average(calls.map((call) => dict(call).ttft_ms))),
    total_ms_sum: numeric(
      "total_ms_sum",
      calls.reduce((sum, call) => sum + (finiteMetric(dict(call).total_ms ?? dict(call).duration_ms) ?? 0), 0),
    ),
    prompt_tokens_total: numeric(
      "prompt_tokens_total",
      calls.reduce((sum, call) => sum + (Number(dict(call).prompt_tokens) || 0), 0),
    ),
    completion_tokens_total: numeric(
      "completion_tokens_total",
      calls.reduce((sum, call) => sum + (Number(dict(call).completion_tokens) || 0), 0),
    ),
    tokens_per_second_avg: numericOrNull(
      "tokens_per_second_avg",
      average(calls.map((call) => dict(call).tokens_per_second)),
    ),
  };
}

// ---------------------------------------------------------------------------
// 1.0 :1471-1501 调用呈现
// ---------------------------------------------------------------------------

/** 1.0 `:1471-1473` —— 四类不展示工具分支。 */
export function showToolBranch(call: unknown): boolean {
  return !["planner", "writer", "faithfulness_judge", "tool_internal"].includes(callType(call));
}

/** 1.0 `:1474-1478` */
export function noToolLabel(call: unknown): string {
  const type = callType(call);
  if (type === "agent") return "该调用直接生成回答，未触发工具。";
  if (type === "recovery") return "该恢复调用未触发工具。";
  return "该历史调用没有可绑定的工具记录。";
}

/** 1.0 `:1479-1485` —— 三个数都有限才出串，否则 `-`。 */
export function callBudget(call: unknown): string {
  const c = dict(call);
  const prompt = finiteMetric(c.preflight_prompt_tokens ?? c.prompt_tokens);
  const output = finiteMetric(c.effective_max_tokens);
  const limit = finiteMetric(c.max_model_len);
  if ([prompt, output, limit].some((value) => value == null)) return "-";
  return `${prompt} + ${output} / ${limit}`;
}

/** 1.0 `:1486-1502` */
export function callObservation(call: unknown): {
  purpose: string;
  trigger: string;
  outcome: string;
  source: string;
  relatedTool: string;
  parentStep: string;
} {
  const c = dict(call);
  const observation = dict(c.call_observation);
  const sources: Record<string, string> = {
    backend_recorded: "后端直接记录",
    saved_call_facts_v1: "已存调用事实（规则 v1）",
    historical_trace_aligned: "历史执行轨迹确定性对齐",
    historical_unresolved: "历史记录信息不足",
    execution_trace_only: "失败执行轨迹",
  };
  const sourceKey = str(observation.source);
  return {
    purpose: str(observation.purpose) || "未记录",
    trigger: str(observation.trigger) || "未记录",
    outcome: str(observation.outcome) || "未记录",
    source: sources[sourceKey] || sourceKey || "未记录",
    relatedTool: str(observation.related_tool) || "-",
    parentStep: str(observation.parent_step_id) || str(c.parent_step_id) || "-",
  };
}

/** 1.0 `:1503-1507` */
export function itemToolTrace(item: unknown): Dict[] {
  return objectList(dict(item).tool_trace);
}

/** 1.0 `:1510-1512` */
export function toolExecutionSteps(item: unknown): Dict[] {
  return itemExecutionTrace(item).filter((step) => String(step.stage || step.type || "") === "tool");
}

// ---------------------------------------------------------------------------
// 1.0 :1514-1545 工具 ↔ 调用绑定（最易错）
// ---------------------------------------------------------------------------

/** 同轮父步骤必须唯一匹配有效尝试；不跨用户轮次绑定。 */
export function callIndexForStep(item: unknown, stepId: unknown, conversationTurn: unknown): number | null {
  if (!stepId) return null;
  const calls = itemCallMetrics(item);
  const matches = calls.map((call, index) => ({ call, index })).filter(
    ({ call }) =>
      String(call.step_id || "") === String(stepId) &&
      conversationTurnNumber(call.conversation_turn) === conversationTurnNumber(conversationTurn) &&
      callType(call) !== "tool_internal" &&
      !["error", "failed", "cancelled"].includes(String(call.status)) && !call.error,
  );
  return matches.length === 1 ? matches[0].index : null;
}

/** 按父调用、父步骤或已存绑定定位；执行轨迹必须有相同工具ID。 */
export function normalizedToolCallIndex(item: unknown, trace: unknown, _traceIndex: number): number | null {
  const traceObj = dict(trace);
  const calls = itemCallMetrics(item);
  if (traceObj.parent_call_id) {
    const index = calls.findIndex(call => call.call_id === traceObj.parent_call_id &&
      conversationTurnNumber(call.conversation_turn) === conversationTurnNumber(traceObj.conversation_turn));
    return index >= 0 ? index : null;
  }
  const parentStepId = traceObj.parent_step_id;
  const parentIndex = callIndexForStep(item, parentStepId, traceObj.conversation_turn);
  if (parentStepId) return parentIndex;
  const savedIndex = finiteMetric(traceObj.model_call_index);
  if (savedIndex != null && Number.isInteger(savedIndex) && calls[savedIndex] &&
    conversationTurnNumber(calls[savedIndex].conversation_turn) === conversationTurnNumber(traceObj.conversation_turn)) return savedIndex;
  const id = traceObj.tool_call_id ?? traceObj.step_id;
  const steps = id ? toolExecutionSteps(item).filter(step =>
    (step.tool_call_id ?? step.step_id) === id &&
    conversationTurnNumber(step.conversation_turn) === conversationTurnNumber(traceObj.conversation_turn)) : [];
  return steps.length === 1 ? callIndexForStep(item, steps[0].parent_step_id, traceObj.conversation_turn) : null;
}

/** 1.0 `:1539-1545` —— 绑到组内调用 `_globalCallIndex` 的同轮工具 trace。 */
export function toolsForGroupedCall(item: unknown, call: unknown): Dict[] {
  const c = dict(call);
  const turnIndex = conversationTurnNumber(c.conversation_turn);
  const target = Number(c._globalCallIndex);
  return itemToolTrace(item).filter((trace, traceIndex) => {
    const sameTurn = conversationTurnNumber(dict(trace).conversation_turn) === turnIndex;
    return sameTurn && normalizedToolCallIndex(item, trace, traceIndex) === target;
  });
}

// ---------------------------------------------------------------------------
// 1.0 :1547-1612 时延归因
// ---------------------------------------------------------------------------

/** 1.0 `:1547-1553` —— 优先 `duration_ms`，否则 `latency_s * 1000`，都不行回 `null`。 */
export function toolDurationMs(trace: unknown): number | null {
  const t = dict(trace);
  const duration = finiteMetric(t.duration_ms);
  if (duration != null) return duration;
  const latency = finiteMetric(t.latency_s);
  return latency == null ? null : latency * 1000;
}

/** 1.0 `:1554-1572` —— `retrieval_timing` 拆段：查询构建 / 各通道 / 融合 / 后处理 / 其他。 */
export function toolLatencySegments(trace: unknown): ToolLatencySegment[] {
  const timing = dict(dict(trace).retrieval_timing);
  const segments: ToolLatencySegment[] = [];
  const add = (kind: string, label: string, value: unknown): void => {
    const ms = Number(value);
    if (Number.isFinite(ms) && ms > 0) segments.push({ kind, label, ms });
  };
  add("nested-tool", "查询构建", timing.query_build_ms);
  Object.entries(dict(timing.channels)).forEach(([channel, value]) => {
    add("nested-tool", channel, dict(value).latency_ms);
  });
  add("nested-tool", "融合", timing.fusion_ms);
  add("nested-tool", "后处理", timing.postprocess_ms);
  const total = Number(timing.total_ms);
  const accounted = segments.reduce((sum, segment) => sum + segment.ms, 0);
  if (Number.isFinite(total) && total > accounted + 0.5) {
    segments.push({ kind: "other", label: "工具其他", ms: total - accounted });
  }
  return segments;
}

/** 1.0 `:1573-1591` —— 工具耗时优先取已存 `agent_loop_timing.tool_ms`，否则累加各工具。 */
export function callAgentLoopTiming(item: unknown, call: unknown): AgentLoopTiming {
  const c = dict(call);
  // **`duration_ms` 才是后端写进 `model_call_metrics` 的字段名**（`run_turn.py` 的
  // `session.model_calls.append({... "duration_ms": ...})`）。此前只读 `total_ms`，
  // 于是每一次模型调用的耗时都是 null → `totalMs` 为 null → 整条延迟时间线是空的，
  // 而页面上「每一步骤的时间」的位置还在。`total_ms` 留着是为了兼容 1.0 的旧存档。
  const modelMs = finiteMetric(c.total_ms ?? c.duration_ms);
  const validModelMs = modelMs != null ? modelMs : null;
  const saved = dict(c.agent_loop_timing);
  const tools = toolsForGroupedCall(item, call);
  const toolMs = finiteMetric(saved.tool_ms) != null
    ? Number(saved.tool_ms)
    : tools.reduce((sum, trace) => sum + (toolDurationMs(trace) || 0), 0);
  const ttftMs = finiteMetric(c.ttft_ms);
  const validTtftMs = ttftMs != null ? ttftMs : null;
  const generationMs = finiteMetric(saved.model_generation_ms) != null
    ? Number(saved.model_generation_ms)
    : validModelMs == null || validTtftMs == null
      ? null
      : Math.max(0, validModelMs - validTtftMs);
  const totalMs = finiteMetric(c.agent_loop_total_ms) != null
    ? Number(c.agent_loop_total_ms)
    : validModelMs == null
      ? null
      : validModelMs + toolMs;
  return { modelMs: validModelMs, ttftMs: validTtftMs, generationMs, toolMs, totalMs, tools };
}

/** 1.0 `:1592-1607` —— 单次调用拆 ttft / 生成 / 各工具；差额归「Agent 编排」。 */
export function callLatencySegments(item: unknown, call: unknown): LatencySegment[] {
  const timing = callAgentLoopTiming(item, call);
  const segments: LatencySegment[] = [];
  if (timing.ttftMs != null && timing.ttftMs > 0) {
    segments.push({ kind: "ttft", label: "TTFT", ms: timing.ttftMs });
  }
  if (timing.generationMs != null && timing.generationMs > 0) {
    segments.push({ kind: "generation", label: "模型生成 / 回答", ms: timing.generationMs });
  }
  if (timing.ttftMs == null && timing.generationMs == null && timing.modelMs != null && timing.modelMs > 0) {
    segments.push({ kind: "model-request", label: "模型请求墙钟（未拆 TTFT）", ms: timing.modelMs });
  }
  timing.tools.forEach((trace, index) => {
    const ms = toolDurationMs(trace);
    if (ms != null && ms > 0) {
      segments.push({ kind: "tool", label: str(dict(trace).tool) || `工具 ${index + 1}`, ms, trace });
    }
  });
  const accounted = segments.reduce((sum, segment) => sum + segment.ms, 0);
  if (timing.totalMs != null && timing.totalMs > accounted + 0.5) {
    segments.push({ kind: "other", label: "Agent 编排", ms: timing.totalMs - accounted });
  }
  return segments;
}

/** 1.0 `:1608-1613` */
export function latencySegmentTitle(segment: LatencySegment): string {
  const retrievalTotal = dict(dict(segment.trace).retrieval_timing).total_ms;
  const detail = retrievalTotal != null ? `\n检索内部耗时 ${fmtMs(retrievalTotal)}` : "";
  return `${segment.label} · ${fmtMs(segment.ms)}${detail}`;
}

/**
 * 1.0 `:2122-2133`。`wall_clock_ms` / `model_ms` / `judge_ms` 各有各的回退源。
 *
 * 本次从 `lib/details.ts` 移入本模块（它本就是被 trace 时延计算消费的），实现逐字未改。
 */
export function itemTimingBreakdown(item: unknown): {
  wall_clock_ms: unknown;
  agent_wall_ms: unknown;
  model_ms: unknown;
  tool_ms: unknown;
  judge_ms: unknown;
  other_ms: unknown;
} {
  const i = dict(item);
  const saved = dict(i.timing_breakdown);
  // **两个回退源都从原始轨迹求和。** 2.0 的 run 不写 `timing_breakdown`，也不写
  // `llm_summary`；如果只读这两个，`model_ms` / `tool_ms` / `other_ms` 恒为空——
  // 页面上「每一步骤耗时」的位置还在，数字是 `-`。数据其实一直在
  // `model_call_metrics[].duration_ms` 与 `tool_trace[].duration_ms` 里。
  const modelFromCalls = itemCallMetrics(i)
    .filter(call => callType(call) !== "tool_internal")
    .map((call) => finiteMetric(call.total_ms ?? call.duration_ms))
    .filter((value): value is number => value != null);
  const toolFromTrace = (Array.isArray(i.tool_trace) ? i.tool_trace : [])
    .map((trace) => toolDurationMs(trace))
    .filter((value): value is number => value != null);
  const modelMs =
    saved.model_ms ??
    dict(i.llm_summary).total_ms_sum ??
    (modelFromCalls.length ? modelFromCalls.reduce((sum, value) => sum + value, 0) :
      Array.isArray(i.model_call_metrics) && !i.model_call_metrics.length ? 0 : null);
  const toolMs =
    saved.tool_ms ?? (toolFromTrace.length ? toolFromTrace.reduce((sum, value) => sum + value, 0) :
      Array.isArray(i.tool_trace) && !i.tool_trace.length ? 0 : null);
  const wallMs = saved.wall_clock_ms ?? i.wall_clock_ms ?? null;
  const judgeMs = saved.judge_ms ?? i.judge_ms ?? null;
  const currentContract = finiteMetric(i.metrics_contract_version) === 2;
  const wall = finiteMetric(wallMs), judge = finiteMetric(judgeMs);
  const agentWallMs = finiteMetric(saved.agent_wall_ms) ?? (currentContract ? wall : (
    wall != null && judge != null ? Math.max(0, wall - judge) : null));
  const model = finiteMetric(modelMs), tool = finiteMetric(toolMs);
  const otherMs = finiteMetric(saved.other_ms) ?? (
    agentWallMs != null && model != null && tool != null && model + tool <= agentWallMs
      ? agentWallMs - model - tool : null);
  return {
    wall_clock_ms: wallMs,
    agent_wall_ms: agentWallMs,
    model_ms: modelMs,
    tool_ms: toolMs,
    judge_ms: judgeMs,
    other_ms: otherMs,
  };
}

/** 2.0 Agent墙钟排除外部Judge；耗时重叠时不绘制伪组成条。 */
export function qaLatencySegments(item: unknown): LatencySegment[] {
  const i = dict(item);
  const current = finiteMetric(i.metrics_contract_version) === 2;
  const timing = itemTimingBreakdown(item);
  const wall = finiteMetric(current ? timing.agent_wall_ms : timing.wall_clock_ms);
  const segments: LatencySegment[] = agentLoopGroups(item).flatMap(group =>
    group.calls.flatMap((call, index) => {
      const ms = callAgentLoopTiming(item, call).totalMs;
      return ms == null || ms <= 0 ? [] : [{
        kind: "agent-loop", label: "对话 " + (group.turnIndex + 1) + " · 调用 " + (index + 1) + " · " + callTypeLabel(call),
        shortLabel: "对话 " + (group.turnIndex + 1) + " · 调用 " + (index + 1), ms, call,
      }];
    }));
  const unbound = itemToolTrace(item).filter((tool, index) => normalizedToolCallIndex(item, tool, index) == null);
  for (const tool of unbound) {
    const ms = toolDurationMs(tool);
    if (ms != null && ms > 0) segments.push({ kind: "tool", label: String(tool.tool || "工具"), shortLabel: "工具", ms, trace: tool });
  }
  if (!current) {
    const judge = finiteMetric(timing.judge_ms);
    if (judge != null && judge > 0) segments.push({ kind: "judge", label: "Judge", shortLabel: "Judge", ms: judge });
  }
  const sum = segments.reduce((total, segment) => total + segment.ms, 0);
  // 并行调用或工具内模型调用会产生重叠，缺少区间时只显示各自用时。
  if (wall != null && sum > wall + 0.5) return [];
  if (wall != null && wall > sum + 0.5) segments.push({
    kind: "other", label: "其他未归因时延", shortLabel: "其他", ms: wall - sum,
  });
  return segments;
}

/** 1.0 `:1641-1645` —— 宽度用 `flex`，占比写进 CSS 变量 `--segment-share`。 */
export function latencySegmentStyle(segment: LatencySegment, segments: LatencySegment[]): LatencySegmentStyle {
  const total = segments.reduce((sum, value) => sum + value.ms, 0) || 1;
  return {
    flex: `${Math.max(segment.ms, 0.1)} 1 0%`,
    "--segment-share": `${(segment.ms / total) * 100}%`,
  };
}

// ---------------------------------------------------------------------------
// 1.0 :1646-1717 调试轨迹定位
// ---------------------------------------------------------------------------

/** 1.0 `:1646-1650` —— 只留 `debug_trace` 是数组的轮。 */
export function runtimeDebugTurns(item: unknown): Dict[] {
  const turns = dict(item).runtime_turns || [];
  return Array.isArray(turns)
    ? turns.filter((turn): turn is Dict => Boolean(turn) && Array.isArray(dict(turn).debug_trace))
    : [];
}

/** 1.0 `:1651-1655` */
export function debugTraceForTurn(item: unknown, turnIndex: unknown): Dict[] {
  const turn = runtimeDebugTurns(item).find((t) => Number(dict(t).index) === Number(turnIndex));
  const trace = dict(turn).debug_trace;
  return Array.isArray(trace) ? (trace as Dict[]) : [];
}

/** 1.0 `:1656-1668` —— 按调用类型取第 N 个候选步，越界回 `null`。 */
export function debugStepForCall(item: unknown, call: unknown, callIndexInTurn: number): Dict | null {
  const turnIndex = conversationTurnNumber(dict(call).conversation_turn);
  const steps = debugTraceForTurn(item, turnIndex);
  const ctype = callType(call);
  const wantType = ctype === "faithfulness_judge" ? "judge" : "model";
  const candidates = steps.filter((step) => {
    if (wantType === "judge") return dict(step).type === "judge";
    if (wantType === "model") {
      return dict(step).type === "model" && (str(dict(step).call_type) || "agent") !== "faithfulness_judge";
    }
    return false;
  });
  return candidates[callIndexInTurn] || null;
}

/** 1.0 `:1669-1679` —— 只对 judge / agent / recovery 出候选。 */
export function debugStepsForCall(item: unknown, call: unknown): Dict[] {
  const turnIndex = conversationTurnNumber(dict(call).conversation_turn);
  const steps = debugTraceForTurn(item, turnIndex);
  const ctype = callType(call);
  if (ctype === "faithfulness_judge") return steps.filter((step) => dict(step).type === "judge");
  if (ctype === "agent" || ctype === "recovery") {
    return steps.filter(
      (step) => dict(step).type === "model" && (str(dict(step).call_type) || "agent") !== "faithfulness_judge",
    );
  }
  return [];
}

/**
 * 1.0 `:1680-1706` —— 三级定位：`step_id` 精确 → 按调用类型第 N 个 → legacy 全量第 N 个。
 */
export function debugStepForCallInGroup(item: unknown, group: unknown, call: unknown): Dict | null {
  const c = dict(call);
  const turnIndex = conversationTurnNumber(c.conversation_turn);
  const steps = debugTraceForTurn(item, turnIndex);
  if (c.step_id) {
    const matched = steps.find((step) => dict(step).step_id === c.step_id);
    if (matched) return matched;
  }
  const ctype = callType(call);
  const calls = objectList(dict(group).calls);
  if (ctype === "planner") {
    return steps.find((step) => dict(step).type === "planner" || dict(step).call_type === "planner") || null;
  }
  if (ctype === "faithfulness_judge") {
    const judges = steps.filter((step) => dict(step).type === "judge" || dict(step).call_type === "faithfulness_judge");
    const index = calls.filter((x) => callType(x) === "faithfulness_judge").indexOf(c);
    return judges[index] || null;
  }
  if (ctype === "agent" || ctype === "recovery") {
    const models = steps.filter(
      (step) =>
        dict(step).type === "model" &&
        dict(step).call_type !== "faithfulness_judge" &&
        dict(step).call_type !== "planner",
    );
    const index = calls.filter((x) => ["agent", "recovery"].includes(callType(x))).indexOf(c);
    return models[index] || null;
  }
  const index = calls.indexOf(c);
  const models = steps.filter(
    (step) => dict(step).type === "model" || dict(step).type === "planner" || dict(step).type === "judge",
  );
  return models[index] || null;
}

/** 1.0 `:1707-1714` —— 用调试步的 `step_id` 反查 `parent_step_id` 相等的工具步。 */
export function debugToolsForCall(item: unknown, group: unknown, call: unknown): Dict[] {
  const modelStep = debugStepForCallInGroup(item, group, call);
  if (!modelStep) return [];
  const turnIndex = conversationTurnNumber(dict(call).conversation_turn);
  const steps = debugTraceForTurn(item, turnIndex);
  return steps.filter(
    (step) => dict(step).type === "tool" && String(dict(step).parent_step_id) === String(dict(modelStep).step_id),
  );
}

/** 1.0 `:1715-1717` */
export function debugPromptAnnotations(item: unknown, group: unknown, call: unknown): unknown[] {
  const annotations = dict(debugStepForCallInGroup(item, group, call)).prompt_annotations;
  return Array.isArray(annotations) ? annotations : [];
}

// ---------------------------------------------------------------------------
// 1.0 :1718-1741 Agent2 轨迹
// ---------------------------------------------------------------------------

/** 1.0 `:1718-1730` —— item 级优先；否则扫 `runtime_turns` / `conversation` 的 turn 级。 */
export function getAgent2Trace(item: unknown): Dict | null {
  if (!item) return null;
  const it = dict(item);
  const hasContent = (trace: Dict): boolean =>
    Boolean(trace.task_declaration || trace.task_state || trace.evidence_ledger);
  const own = dict(it.agent2_trace);
  if (it.agent2_trace && hasContent(own)) return own;
  const turns = it.runtime_turns || it.conversation || [];
  if (Array.isArray(turns)) {
    for (const turn of turns) {
      const trace = dict(dict(turn).agent2_trace);
      if (dict(turn).agent2_trace && hasContent(trace)) return trace;
    }
  }
  return it.agent2_trace ? own : null;
}

/** 1.0 `:1731-1735` */
export function getAgent2Requirements(item: unknown): Dict[] {
  const trace = getAgent2Trace(item);
  if (!trace) return [];
  const t = dict(trace);
  const requirements = dict(t.task_state).requirements || dict(t.task_declaration).requirements || t.requirements || [];
  return Array.isArray(requirements) ? (requirements as Dict[]) : [];
}

/** 1.0 `:1736-1741` */
export function getAgent2LedgerEntries(item: unknown): Dict[] {
  const trace = getAgent2Trace(item);
  if (!trace) return [];
  const entries = dict(dict(trace).evidence_ledger).entries || [];
  return Array.isArray(entries) ? (entries as Dict[]) : [];
}

// ---------------------------------------------------------------------------
// 1.0 :1742-1793 Agent2 证据呈现
// ---------------------------------------------------------------------------

/** 1.0 `:1742-1756` —— 空值有专门文案；字符串会尝试 JSON 美化，失败原样返回。 */
export function formatAgent2EvidenceValue(value: unknown): string {
  if (value == null) return "已记录证据，但没有提取出的文本值";
  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return "已记录证据，但没有提取出的文本值";
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return value;
    }
  }
  const formatted = JSON.stringify(value, null, 2);
  return formatted ?? String(value);
}

/** 1.0 `:1757-1759` —— 行数至少 3。 */
export function agent2EvidenceRows(value: unknown): number {
  return Math.max(3, formatAgent2EvidenceValue(value).split("\n").length);
}

/** 1.0 `:1760-1762` —— 每行 15.5px + 16px 内边距。 */
export function agent2EvidenceHeight(value: unknown): string {
  return String(agent2EvidenceRows(value) * 15.5 + 16) + "px";
}

/** 1.0 `:1763-1765` */
export function agent2RequirementStatusLabel(status: unknown): string {
  const labels: Record<string, string> = {
    satisfied: "已满足",
    partially_supported: "部分满足",
    open: "待满足",
    failed: "未满足",
  };
  const key = str(status);
  return labels[key] || key || "未记录";
}

/** 1.0 `:1766-1768` —— 三档：satisfied / partially_supported / 其余（含缺省）失败色。 */
export function agent2RequirementStatusClass(status: unknown): string {
  const key = str(status);
  return key === "satisfied" ? "status-complete" : key === "partially_supported" ? "status-partial" : "status-failed";
}

/** 1.0 `:1769-1781` */
export function agent2EvidenceTypeLabel(type: unknown): string {
  const labels: Record<string, string> = {
    // Legacy aliases kept for historical runs.
    visual: "视觉",
    text: "文字 / OCR",
    temporal: "时间",
    geographic: "地点",
    identity: "身份",
    semantic: "语义",
    // Canonical Agent2 evidence types emitted by the backend.
    memory_asset: "照片 / 视频",
    memory_reference: "记忆引用",
    visual_observation: "视觉观察",
    visible_text: "可见文字 / OCR",
    structured_fact: "结构化事实",
    temporal_metadata: "时间元数据",
    location_metadata: "地点元数据",
    confirmed_identity: "已确认身份",
    user_statement: "用户陈述",
    transcript: "转写文本",
  };
  const key = str(type);
  return labels[key] || key || "证据";
}

/** 1.0 `:1782-1787` */
export function agent2RequirementSummary(item: unknown): string {
  const requirements = getAgent2Requirements(item);
  const satisfied = requirements.filter((req) => dict(req).status === "satisfied").length;
  const partial = requirements.filter((req) => dict(req).status === "partially_supported").length;
  return `${satisfied}/${requirements.length} 项满足${partial ? `，${partial} 项部分满足` : ""}`;
}

/** 1.0 `:1788-1793` */
export function agent2DecisionSummary(item: unknown): string {
  const decisions = dict(getAgent2Trace(item)).planner_decisions || [];
  const list = Array.isArray(decisions) ? decisions : [];
  const decision = dict(list[list.length - 1]);
  if (decision.status === "accepted") return "规划已接受";
  if (decision.status === "fallback") return "规划失败，已回退主流程";
  return str(decision.status) || "已记录";
}

// ---------------------------------------------------------------------------
// 1.0 :1794-1805 归因
// ---------------------------------------------------------------------------

/** 1.0 `:1794-1801` —— `layers` 里值为 `fail` 的键用 ` / ` 连接，空回 `无`。 */
export function attributionSummary(item: unknown): AttributionSummary {
  const attribution = dict(dict(item).attribution);
  const layers = dict(attribution.layers);
  return {
    primary: str(attribution.primary) || "未归因",
    failed:
      Object.entries(layers)
        .filter(([, status]) => status === "fail")
        .map(([key]) => key)
        .join(" / ") || "无",
  };
}

/** 1.0 `:1802-1804` */
export function attributionLabel(key: unknown): string {
  const labels: Record<string, string> = {
    R: "检索",
    V: "视觉",
    O: "OCR",
    T: "工具",
    S: "综合",
    G: "Guard",
    J: "Judge",
    PASS: "通过",
  };
  const k = str(key);
  return labels[k] || k;
}

/** 1.0 `:1805` —— fail / pass / 其余三档。 */
export function attributionClass(status: unknown): string {
  const key = str(status);
  return key === "fail" ? "score-0" : key === "pass" ? "score-2" : "score-none";
}
