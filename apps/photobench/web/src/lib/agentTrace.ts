/**
 * 2.0 的完整 Agent 轨迹访问器。
 *
 * 数据来源是主 API 回合终态载荷里的 `trace.debug_calls`（评测侧 `include_debug=true`
 * 才会写）：每次模型调用一条，带实际发送的 `messages` / `tools` 与原始 `response`。
 * PhotoBench 把它原样存进 `runtime_turns[i].trace_bundle`，所以这里不构造任何新事实，
 * 只负责把「模型调用」和「它发起的工具调用」按发生顺序拼回一条时间线。
 *
 * 老 run（改动前）没有 `debug_calls`；所有函数都保留 1.0 `debug_trace` 的回退，
 * 缺数据时返回空表/`null`，由渲染层显示「未记录」，**不推断**。
 */

import { agentLoopGroups, debugStepForCallInGroup, itemCallMetrics, normalizedToolCallIndex } from "./trace.ts";

export type Dict = Record<string, unknown>;

export interface TraceTimelineEntry {
  /** 时间线条目的类型。 */
  kind: "planner" | "model" | "judge" | "tool" | "event" | "answer";
  event?: Dict;
  unbound?: boolean;
  key: string;
  /** 模型调用（planner / model / judge）。 */
  call?: Dict;
  /** 工具调用（tool_trace 的一行）。 */
  tool?: Dict;
  /** 最终回答。 */
  answer?: string;
  attachments?: unknown[];
}

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

function objectList(value: unknown): Dict[] {
  return Array.isArray(value)
    ? value.filter((item): item is Dict => typeof item === "object" && item !== null)
    : [];
}

/** 读取已存用户轮次；只有题级调用的旧结果按其原轮次分组。 */
export function runtimeTurns(item: unknown): Dict[] {
  const i = dict(item);
  const turns = objectList(i.runtime_turns);
  if (turns.length) return turns;
  if (!i.question && !itemCallMetrics(i).length && !objectList(i.tool_trace).length && !objectList(i.conversation).length) return [];
  // 旧存档只有题级调用时，沿已有轮次分组展示，不能在移除旧调用树后丢失。
  const groups = agentLoopGroups(i);
  return groups.map(group => ({
    ...(groups.length === 1 ? i : {}), ...group.turn, index: group.turnIndex,
    message: group.turn.message ?? (groups.length === 1 ? i.question : undefined),
  }));
}

/** 一轮里的完整模型调用；老 run 没有这一格时回空表。 */
export function turnDebugCalls(turn: unknown): Dict[] {
  return objectList(dict(dict(turn).trace_bundle).debug_calls);
}

/** 一轮里的原始进度事件（2.0 的 `trace`），用于「运行时事件」清单。 */
export function turnProgressEvents(turn: unknown): Dict[] {
  return objectList(dict(turn).trace);
}

/** 整题摊平后的模型调用，附带它所属的轮次号。 */
export function itemDebugCalls(item: unknown): Dict[] {
  return runtimeTurns(item).flatMap((turn) =>
    turnDebugCalls(turn).map((call) => ({ ...call, conversation_turn: Number(turn.index) || 0 })),
  );
}

/** 进度事件对应已生效的成功尝试；调用卡自身的IO另外按attempt精确匹配。 */
function preferredCall(calls: Dict[], stepId: string, callType: string): Dict | null {
  let fallback: Dict | null = null;
  let preferred: Dict | null = null;
  for (const call of calls) {
    if (String(call.step_id || "") !== stepId) continue;
    if (String(call.call_type || "agent") !== callType) continue;
    fallback = call;
    if (call.status !== "error" && call.response) preferred = call;
  }
  return preferred ?? fallback;
}

/** 一轮里每个 agent `step_id` 最终生效的那条调用（供工具绑定用）。 */
function preferredAgentCallMap(calls: Dict[]): Map<string, Dict> {
  const map = new Map<string, Dict>();
  for (const call of calls) {
    if (String(call.call_type || "agent") !== "agent") continue;
    const stepId = String(call.step_id || "");
    if (!stepId) continue;
    const current = map.get(stepId);
    if (!current || (call.status !== "error" && call.response)) map.set(stepId, call);
  }
  return map;
}

/** 一次 Agent Loop 调用对应的完整模型输入/输出；按轮 + `step_id` 精确匹配。 */
export function debugCallForCall(item: unknown, call: unknown): Dict | null {
  const c = dict(call);
  const turnIndex = Number(c.conversation_turn ?? 0);
  const turn = runtimeTurns(item).find((entry, index) => Number(entry.index ?? index) === turnIndex);
  if (!turn) return null;
  const calls = turnDebugCalls(turn);
  if (c.call_id) {
    const exact = calls.filter(entry => entry.call_id === c.call_id);
    if (exact.length === 1) return exact[0];
  }
  if (!c.step_id) return null;
  const matches = calls.filter(entry => String(entry.step_id) === String(c.step_id) &&
    (!c.call_id || !entry.call_id || c.call_id === entry.call_id) &&
    String(entry.call_type || "agent") === String(c.call_type || "agent") &&
    (c.attempt == null || entry.attempt === c.attempt));
  return matches.length === 1 ? matches[0] : null;
}

/** 指标与完整IO按调用身份合并；仅有指标也保留调用事实。 */
export function turnModelCalls(item: unknown, turnIndex: number): Dict[] {
  const turn = runtimeTurns(item).find((entry, index) => Number(entry.index ?? index) === turnIndex);
  if (!turn) return [];
  const debug = turnDebugCalls(turn);
  const used = new Set<Dict>();
  const metrics = itemCallMetrics(item).map((call, index) => ({ ...call, _globalCallIndex: index }))
    .filter(call => Number((call as Dict).conversation_turn ?? 0) === turnIndex);
  const records: Dict[] = metrics.length ? metrics : objectList(dict(turn.trace_bundle).model_calls)
    .map(call => ({ ...call, conversation_turn: turnIndex }));
  const group = agentLoopGroups(item).find(value => value.turnIndex === turnIndex);
  for (const call of group?.calls ?? []) if (call._traceOnly) records.push(call);
  const merged = records.map(call => {
    const io = debugCallForCall(item, call);
    if (io) used.add(io);
    const legacy = !io && group && Number(dict(item).metrics_contract_version) !== 2 && !turn.trace_bundle
      ? debugStepForCallInGroup(item, group, call) : null;
    const legacyIO = legacy ? { messages: legacy.prompt ?? dict(legacy.debug).prompt,
      response: { content: legacy.raw_full ?? legacy.raw ?? dict(legacy.debug).raw } } : {};
    return { ...legacyIO, ...io, ...call, conversation_turn: turnIndex };
  });
  // 只保存了debug的真实调用也要展示，不把调用数写成0。
  const extra = debug.filter(call => !used.has(call)).map(call => ({ ...call, conversation_turn: turnIndex }));
  return [...merged, ...extra].sort((a, b) => {
    const left = Number((a as Dict).seq), right = Number((b as Dict).seq);
    return Number.isFinite(left) && Number.isFinite(right) ? left - right : 0;
  });
}

export function isRuleRefusal(turn: unknown): boolean {
  const t = dict(turn);
  return t.termination_reason === "policy_refusal" && !objectList(dict(t.trace_bundle).model_calls).length &&
    !turnDebugCalls(t).length;
}

/**
 * 进度事件里的一个步骤对应的模型调用。
 *
 * - `plan`：规划器调用（取最后一次成功尝试；重试用 `planner_calls` 完整展示）。
 * - `model_action`：发起该工具调用的那次 Agent 调用（`parent_step_id`）。
 * - `judge`：评审调用（后端在 payload 里带 `call_id`）。
 */
export function debugCallForStep(turn: unknown, step: unknown): Dict | null {
  const s = dict(step);
  // 两个来源：PhotoBench 投影过的 `debug_trace`（字段在顶层）与主 API 的原始
  // `trace` 事件（字段在 `payload` 里）。同一个函数要同时认，否则「运行时事件」
  // 清单里的步骤会挂不到模型调用上。
  const payload = dict(s.payload);
  const calls = turnDebugCalls(turn);
  if (!calls.length) return null;
  const type = String(s.type || s.kind || "");
  if (type === "plan") {
    const planners = calls.filter((call) => String(call.call_type) === "planner");
    return planners[planners.length - 1] ?? null;
  }
  if (type === "model_action") {
    const stepId = String(s.parent_step_id ?? payload.parent_step_id ?? "");
    return preferredCall(calls, stepId, "agent");
  }
  if (type === "judge") {
    const callId = String(s.call_id ?? payload.call_id ?? "");
    return (
      calls.find(
        (call) =>
          String(call.call_type) === "faithfulness_judge" &&
          (!callId || String(call.step_id || "") === callId),
      ) ?? null
    );
  }
  return null;
}

/** 一个模型调用发起的工具调用（按 `parent_step_id` 绑定）。 */
export function toolsForDebugCall(item: unknown, turnIndex: number, call: unknown): Dict[] {
  const c = dict(call);
  if (["error", "failed", "cancelled"].includes(String(c.status)) || c.error) return [];
  const calls = turnModelCalls(item, turnIndex);
  const preferred = preferredAgentCallMap(calls);
  return objectList(dict(item).tool_trace).filter((trace, index) => {
    if (Number(trace.conversation_turn ?? 0) !== turnIndex) return false;
    if (c._globalCallIndex != null) return normalizedToolCallIndex(item, trace, index) === c._globalCallIndex;
    if (trace.parent_call_id) return trace.parent_call_id === c.call_id;
    const target = preferred.get(String(trace.parent_step_id || ""));
    if (!target || !c.step_id || target.step_id !== c.step_id) return false;
    return target.seq === c.seq && target.attempt === c.attempt;
  });
}

/** 规划器重试的完整列表（`plan` 步骤上要能看见每一次尝试）。 */
export function plannerCallsForTurn(turn: unknown): Dict[] {
  return turnDebugCalls(turn).filter((call) => String(call.call_type) === "planner");
}

/** 在同一用户轮内合并调用、工具和进度事件；无法确定父调用的工具独立保留。 */
export function turnTimeline(item: unknown, turnIndex: number): TraceTimelineEntry[] {
  const turn = runtimeTurns(item).find((entry, index) => Number(entry.index ?? index) === turnIndex);
  if (!turn) return [];
  const calls = turnModelCalls(item, turnIndex);
  const tools = objectList(dict(item).tool_trace).filter(tool => Number(tool.conversation_turn ?? 0) === turnIndex);
  const owners = calls.map(call => toolsForDebugCall(item, turnIndex, call));
  const emitted = new Set<Dict>();
  const entries: TraceTimelineEntry[] = [];
  let cursor = -1;
  const addTool = (tool: Dict, unbound = false) => {
    if (emitted.has(tool)) return;
    emitted.add(tool);
    entries.push({ kind: "tool", key: "tool-" + turnIndex + "-" + tools.indexOf(tool), tool, unbound });
  };
  const through = (target: number) => {
    while (cursor < target) {
      if (cursor >= 0) owners[cursor].forEach(tool => addTool(tool));
      cursor++;
      const call = calls[cursor];
      const kind = call.call_type === "planner" ? "planner" :
        ["faithfulness_judge", "claim_verifier"].includes(String(call.call_type)) ? "judge" : "model";
      entries.push({ kind, key: "call-" + turnIndex + "-" + cursor, call });
    }
  };
  const events = turnProgressEvents(turn);
  if (events.length) {
    events.forEach((event, eventIndex) => {
      const payload = dict(event.payload);
      const kind = String(event.kind || event.type || "");
      const toolId = payload.tool_call_id ?? payload.step_id;
      const tool = tools.find(value => (value.tool_call_id ?? value.step_id) === toolId && toolId != null);
      let callIndex = tool ? owners.findIndex(group => group.includes(tool)) : -1;
      if (kind === "plan") callIndex = calls.findLastIndex(call => call.call_type === "planner");
      if (kind === "judge") callIndex = calls.findIndex(call =>
        (call.call_id === payload.call_id || call.step_id === payload.call_id) && payload.call_id != null);
      if (kind === "terminal") {
        through(calls.length - 1);
        if (cursor >= 0) owners[cursor].forEach(tool => addTool(tool));
        tools.forEach(tool => addTool(tool, true));
      }
      if (callIndex >= 0) through(callIndex);
      // 原始进度事件只有秒级时间；只放入明确已开始的调用，边界不向后扩秒。
      const eventTime = Date.parse(String(event.timestamp || ""));
      if (Number.isFinite(eventTime)) {
        const started = calls.findLastIndex(call => {
          const time = Date.parse(String(call.started_at || ""));
          return Number.isFinite(time) && time <= eventTime;
        });
        if (started >= 0) through(started);
      }
      if (["tool_result", "tool_error"].includes(kind) && tool) {
        addTool(tool, callIndex < 0);
        return;
      }
      // 发起工具与工具结果合在一张卡，原始事件保留为核验入口。
      if (kind === "model_action" && tool) return;
      if (kind === "judge" && callIndex >= 0) return;
      entries.push({ kind: "event", key: "event-" + turnIndex + "-" + eventIndex, event });
    });
  }
  through(calls.length - 1);
  if (cursor >= 0) owners[cursor].forEach(tool => addTool(tool));
  tools.forEach(tool => addTool(tool, true));
  if (!calls.length && !events.length) {
    objectList(turn.debug_trace).forEach((step, index) => {
      const type = String(step.type || step.kind || "");
      const key = "legacy-" + turnIndex + "-" + index;
      if (["planner", "model", "judge"].includes(type)) entries.push({
        kind: type as "planner" | "model" | "judge", key,
        call: { ...step, messages: step.prompt, response: step.raw_full || step.raw ? { content: step.raw_full ?? step.raw } : undefined },
      });
      else entries.push({ kind: "event", key, event: step });
    });
  }
  entries.push({ kind: "answer", key: "answer-" + turnIndex,
    answer: String(turn.answer || turn.final_answer || ""), attachments: Array.isArray(turn.attachments) ? turn.attachments : [] });
  return entries;
}

// ---------------------------------------------------------------------------
// 模型调用字段的安全访问
// ---------------------------------------------------------------------------

export interface ChatMessageView {
  role: string;
  content: string;
  toolCallId: string;
  toolCalls: Dict[];
}

export function callMessages(call: unknown): ChatMessageView[] {
  const messages = dict(call).messages;
  if (!Array.isArray(messages)) return [];
  return messages
    .filter((message): message is Dict => typeof message === "object" && message !== null)
    .map((message) => ({
      role: String(message.role || ""),
      content: typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2),
      toolCallId: String(message.tool_call_id || ""),
      toolCalls: objectList(message.tool_calls),
    }));
}

export function callTools(call: unknown): Dict[] {
  return objectList(dict(call).tools);
}

export function callResponse(call: unknown): Dict {
  return dict(dict(call).response);
}

export function callContent(call: unknown): string {
  const content = callResponse(call).content;
  return typeof content === "string" ? content : "";
}

export function callThought(call: unknown): string {
  const thought = callResponse(call).thought;
  return typeof thought === "string" ? thought : "";
}

export function callToolCalls(call: unknown): Dict[] {
  return objectList(callResponse(call).tool_calls);
}

export function callLabel(callType: unknown): string {
  const labels: Record<string, string> = {
    planner: "规划器",
    agent: "主 Agent",
    faithfulness_judge: "语义评审",
    claim_verifier: "答案事实核验",
  };
  const key = String(callType || "");
  return labels[key] || key || "模型调用";
}

/** 模型调用的状态：失败时显示错误，而不是把整个调用藏掉。 */
export function callStatus(call: unknown): string {
  const c = dict(call);
  if (c.error) return `失败 · ${String(c.error)}`;
  return ({ complete: "完成", completed: "完成", error: "失败", failed: "失败", cancelled: "已取消" } as Record<string, string>)[String(c.status)] || String(c.status || "已记录");
}
