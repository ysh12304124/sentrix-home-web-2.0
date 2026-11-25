import { useState } from "react";
import type { ReactNode } from "react";
import type { RunItemDetail } from "../../api/types.ts";
import { isRuleRefusal, turnModelCalls, runtimeTurns } from "../../lib/agentTrace.ts";
import type { Dict } from "../../lib/agentTrace.ts";
import { conversationTurns } from "../../lib/evidence.ts";
import { actionLabel, esc, judgeScoreLabel, turnCompletionLabel, turnTerminationLabel } from "../../lib/format.ts";
import { Agent2SummaryPanel } from "./Agent2SummaryPanel.tsx";
import { TraceNodeBrowser } from "./TraceNodeBrowser.tsx";

function Fold({ className, summary, children, defaultOpen = false }: {
  className: string; summary: ReactNode; children: () => ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className={className} open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}><summary>{summary}</summary>{open ? children() : null}</details>;
}
export interface DebugTracePanelProps { detail: RunItemDetail; modelLabel?: string; defaultOpen?: boolean }
export function DebugTracePanel({ detail, modelLabel, defaultOpen = false }: DebugTracePanelProps) {
  const raw = runtimeTurns(detail);
  const planned = conversationTurns(detail);
  const turns: Dict[] = raw.length ? raw : planned as unknown as Dict[];
  if (!turns.length) return null;
  const calls = turns.flatMap((turn, index) => turnModelCalls(detail, Number(turn.index ?? index)));
  const split = (detail.trace_storage as Dict | undefined)?.version === 3;
  const overview = detail.trace_read_summary as Dict | undefined;
  const toolCount = overview?.tool_call_count ?? (!split && Array.isArray(detail.tool_trace) ? detail.tool_trace.length : null);
  const modelCount = overview?.model_call_count ?? (!split && (calls.length || Array.isArray(detail.model_call_metrics)) ? calls.length : null);
  const knownCalls = modelCount != null || turns.every(isRuleRefusal);
  const shared = detail.conversation_context_mode === "shared_conversation_id" ||
    (turns.length > 1 && turns.every(turn => turn.conversation_id && turn.conversation_id === turns[0].conversation_id));
  return <Fold className="call-node debug-trace-node" defaultOpen={defaultOpen} summary={<>
    <strong>对话与执行轨迹</strong>
    <span>{turns.length} 轮用户对话 · {knownCalls ? String(modelCount ?? 0) : "未记录"} 次模型调用 · {toolCount == null ? "未记录" : String(toolCount)} 次工具调用</span>
    {turns.length > 1 ? <span>{shared ? "共享会话上下文" : "会话复用情况未记录"}</span> : null}
  </>}>{() => <div className="debug-trace-body">

    {!split && calls.some(call => !Array.isArray(call.messages)) ? <p className="debug-trace-missing">部分调用只保存了指标，完整输入输出未记录；调用和工具关系仍按已有记录展示。</p> : null}
    {turns.map((turn, position) => {
      const index = Number(turn.index ?? position);
      const turnCalls = turnModelCalls(detail, index);
      const ruleRefusal = !turnCalls.length && isRuleRefusal(turn);
      const behavior = (turn.task_judge ?? {}) as Dict;
      const budget = ((turn.trace_bundle as Dict | undefined)?.budget ?? {}) as Dict;
      const guard = (turn.guard_debug ?? {}) as Dict;
      return <Fold key={index} className="debug-turn debug-turn-full" defaultOpen summary={<>
        <strong>用户第 {index + 1} 轮</strong><span>{esc(turn.message)}</span>
        <span>{turnCompletionLabel(turn)} · {turnTerminationLabel(turn)}</span>
      </>}>{() => <>
        {Number(turn.context_turn_count) > 0 || budget.model_steps != null || budget.tool_round_trips != null || guard.recovery_attempts != null ? <div className="trace-context-note trace-turn-meta">
          {Number(turn.context_turn_count) > 0 ? <span>已携带 {String(turn.context_turn_count)} 轮历史对话</span> : null}
          {budget.model_steps != null ? <span>模型步骤 {String(budget.model_steps)}{budget.max_steps != null ? ` / ${String(budget.max_steps)}` : ""}</span> : null}
          {budget.tool_round_trips != null ? <span>工具往返 {String(budget.tool_round_trips)}{budget.max_tool_calls != null ? ` / ${String(budget.max_tool_calls)}` : ""}</span> : null}
          {guard.recovery_attempts != null && Number(guard.recovery_attempts) > 0 ? <span>恢复 {String(guard.recovery_attempts)} 次</span> : null}
        </div> : null}
        {ruleRefusal ? <p className="trace-policy-refusal">安全规则直接拒答，本轮未调用模型或工具。</p> : null}
        <TraceNodeBrowser detail={detail} turnIndex={index} modelLabel={modelLabel} />
        {turn.agent2_trace && !ruleRefusal ? <Agent2SummaryPanel detail={{index, agent2_trace:turn.agent2_trace}} /> : null}
        {turn.expected_action || turn.task_judge || turn.judge ? <div className="result-turn-scores">
          <span>任务行为：期望{actionLabel(turn.expected_action)} / 实际{actionLabel(behavior.actual_action)}</span>
          <span>{behavior.correct === true ? "行为符合" : behavior.correct === false ? "行为不符" : "行为未评分"}</span>
          <span>回答质量：{judgeScoreLabel(turn, null)}</span>
        </div> : null}
      </>}</Fold>;
    })}
  </div>}</Fold>;
}
