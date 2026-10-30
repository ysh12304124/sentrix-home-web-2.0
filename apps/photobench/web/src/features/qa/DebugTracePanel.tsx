import { useState } from "react";
import type { ReactNode } from "react";
import type { RunItemDetail } from "../../api/types.ts";
import { isRuleRefusal, turnModelCalls, turnProgressEvents, turnTimeline, runtimeTurns } from "../../lib/agentTrace.ts";
import type { Dict, TraceTimelineEntry } from "../../lib/agentTrace.ts";
import { conversationTurns, itemParseRate } from "../../lib/evidence.ts";
import { actionLabel, esc, fmtMs, judgeScoreLabel, retrievalBackendLabel, retrievalBackendDegraded, turnCompletionLabel, turnTerminationLabel } from "../../lib/format.ts";
import { toolDurationMs, toolLatencySegments } from "../../lib/trace.ts";
import { toolStatusLabel } from "./qaShared.ts";
import { TimingSegments } from "./TracePerformance.tsx";
import { Agent2SummaryPanel } from "./Agent2SummaryPanel.tsx";
import { ModelCallView } from "./ModelCallView.tsx";

function Fold({ className, summary, children, defaultOpen = false }: {
  className: string; summary: ReactNode; children: () => ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className={className} open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}><summary>{summary}</summary>{open ? children() : null}</details>;
}
const eventLabels: Record<string, string> = {
  status: "状态变化", plan: "规划完成", planner: "任务规划", model_action: "发起工具",
  tool_result: "工具结果", tool_error: "工具失败", judge: "答案核验", guard: "检查与恢复",
  evidence_binding: "证据核对", terminal: "本轮结束", model: "模型调用", tool: "工具调用",
};
function pretty(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function RuntimeEvent({ event }: { event: Dict }) {
  const kind = String(event.kind || event.type || event.stage || "event");
  return <Fold className={"debug-step debug-kind-" + kind} summary={<>
    <strong>{eventLabels[kind] || kind}</strong>
    <span>{esc(event.text) || esc(event.message)}</span>
  </>}>{() => <pre>{pretty(event)}</pre>}</Fold>;
}
function ToolTraceView({ tool, unbound }: { tool: Dict; unbound?: boolean }) {
  const name = String(tool.tool || tool.tool_name || "未知工具");
  const backend = retrievalBackendLabel(tool);
  return <Fold className="model-tool-node" summary={<>
    <strong>{name === "final_answer" ? "交付回答 · final_answer" : name}</strong>
    <span>{toolStatusLabel(tool)}</span><span>工具耗时 {fmtMs(toolDurationMs(tool))}</span>
    {backend ? <span className={`retrieval-backend${retrievalBackendDegraded(tool) ? " degraded" : ""}`}>检索后端 {backend}</span> : null}
    {unbound ? <span>父调用未确定</span> : null}
    {tool.text ? <span>{esc(tool.text)}</span> : null}
  </>}>{() => <div className="model-tool-node-body">
    <TimingSegments title="工具内部耗时" total={toolDurationMs(tool)} segments={toolLatencySegments(tool)} />
    {name === "final_answer" ? <p className="muted small">执行上一步模型给出的答案和媒体选择；此工具不额外调用模型。</p> : null}
    <p className="muted small">工具输入</p><pre>{pretty(tool.arguments ?? "未记录")}</pre>
    <p className="muted small">工具输出</p><pre>{pretty(tool.result ?? tool.observation ?? "未记录")}</pre>
    <p className="muted small">调用ID · {esc(tool.tool_call_id ?? tool.step_id) || "未记录"} · 父步骤 {esc(tool.parent_step_id) || "未记录"}</p>
  </div>}</Fold>;
}
function TimelineEntryView({ entry, detail, modelLabel }: { entry: TraceTimelineEntry; detail: RunItemDetail; modelLabel?: string }) {
  if (entry.kind === "event") return <RuntimeEvent event={entry.event ?? {}} />;
  if (entry.kind === "tool") return <ToolTraceView tool={entry.tool ?? {}} unbound={entry.unbound} />;
  if (entry.kind === "answer") return <section className="trace-answer-block">
    <h5>本轮回答</h5><p>{esc(entry.answer) || "未记录"}</p>
    {entry.attachments?.length ? <details><summary>交付媒体 · {entry.attachments.length} 项</summary>
      <pre>{pretty(entry.attachments)}</pre></details> : null}
  </section>;
  if (!entry.call) return null;
  return <ModelCallView call={entry.call} detail={detail} modelLabel={modelLabel} title={entry.kind === "planner" ? "规划器调用" : entry.kind === "judge" ? "答案核验调用" : entry.call.call_type === "tool_internal" ? "工具内部模型调用" : "Agent 模型调用"} />;
}
export interface DebugTracePanelProps { detail: RunItemDetail; modelLabel?: string }
export function DebugTracePanel({ detail, modelLabel }: DebugTracePanelProps) {
  const raw = runtimeTurns(detail);
  const planned = conversationTurns(detail);
  const turns: Dict[] = raw.length ? raw : planned as unknown as Dict[];
  if (!turns.length) return null;
  const calls = turns.flatMap((turn, index) => turnModelCalls(detail, Number(turn.index ?? index)));
  const toolCount = Array.isArray(detail.tool_trace) ? detail.tool_trace.length : null;
  const knownCalls = calls.length > 0 || Array.isArray(detail.model_call_metrics) || turns.every(isRuleRefusal);
  const shared = detail.conversation_context_mode === "shared_conversation_id" ||
    (turns.length > 1 && turns.every(turn => turn.conversation_id && turn.conversation_id === turns[0].conversation_id));
  return <Fold className="call-node debug-trace-node" summary={<>
    <strong>对话与执行轨迹</strong>
    <span>{turns.length} 轮用户对话 · {knownCalls ? calls.length : "未记录"} 次模型调用 · {toolCount ?? "未记录"} 次工具调用</span>
    {turns.length > 1 ? <span>{shared ? "共享会话上下文" : "会话复用情况未记录"}</span> : null}
  </>}>{() => <div className="debug-trace-body">
    <div className="trace-performance-summary"><span>{itemParseRate(detail)}</span><span>展开调用查看 Token、TTFT 和工具耗时</span></div>
    {calls.some(call => !Array.isArray(call.messages)) ? <p className="debug-trace-missing">部分调用只保存了指标，完整输入输出未记录；调用和工具关系仍按已有记录展示。</p> : null}
    {turns.map((turn, position) => {
      const index = Number(turn.index ?? position);
      const timeline = turnTimeline(detail, index);
      const events = turnProgressEvents(turn);
      const turnCalls = turnModelCalls(detail, index);
      const ruleRefusal = !turnCalls.length && isRuleRefusal(turn);
      const behavior = (turn.task_judge ?? {}) as Dict;
      const budget = ((turn.trace_bundle as Dict | undefined)?.budget ?? {}) as Dict;
      const guard = (turn.guard_debug ?? {}) as Dict;
      return <Fold key={index} className="debug-turn debug-turn-full" defaultOpen summary={<>
        <strong>用户第 {index + 1} 轮</strong><span>{esc(turn.message)}</span>
        <span>{turnCompletionLabel(turn)} · {turnTerminationLabel(turn)}</span>
      </>}>{() => <>
        <div className="trace-context-note">
          {turn.context_turn_count != null ? <span>已携带 {String(turn.context_turn_count)} 轮历史对话</span> : null}
          {turn.conversation_id ? <details><summary>会话标识</summary><code>{String(turn.conversation_id)}</code></details> : null}
        </div>
        <details className="trace-execution-diagnostics"><summary>执行诊断</summary>
          <div className="trace-performance-summary">
            <span>状态：{turnCompletionLabel(turn)} · {turnTerminationLabel(turn)}</span>
            {budget.model_steps != null ? <span>模型步骤 {String(budget.model_steps)} / {String(budget.max_steps ?? "上限未记录")}</span> : null}
            {budget.tool_round_trips != null ? <span>工具往返 {String(budget.tool_round_trips)} / {String(budget.max_tool_calls ?? "上限未记录")}</span> : null}
            {guard.recovery_attempts != null ? <span>恢复 {String(guard.recovery_attempts)} 次</span> : null}
            {turn.parse_status ? <span>动作解析：{String(turn.parse_status)}</span> : null}
            {turn.agent_stability ? <span>{itemParseRate(turn)}</span> : null}
            {turn.error || turn.agent_reason ? <span>{String(turn.error || turn.agent_reason)}</span> : null}
          </div>
        </details>
        {ruleRefusal ? <p className="trace-policy-refusal">安全规则直接拒答，本轮未调用模型或工具。</p> : null}
        {timeline.some(entry => entry.kind !== "answer") ? <Fold className="trace-execution" defaultOpen={turns.length === 1}
          summary={<strong>执行步骤 · {turnCalls.length} 次模型 · {timeline.filter(entry => entry.kind === "tool").length} 次工具</strong>}>
          {() => <>
            <div className="trace-timeline">{timeline.filter(entry => entry.kind !== "answer")
              .map(entry => <TimelineEntryView key={entry.key} entry={entry} detail={detail} modelLabel={modelLabel} />)}</div>
            {turn.agent2_trace && !ruleRefusal ? <Agent2SummaryPanel detail={{index, agent2_trace:turn.agent2_trace}} /> : null}
            <details className="trace-progress-events"><summary>原始事件 · {events.length} 条（核对用）</summary>
              <pre>{pretty(events.length ? events : turn.debug_trace ?? [])}</pre>
            </details>
          </>}
        </Fold> : null}
        {timeline.find(entry => entry.kind === "answer") ? <TimelineEntryView entry={timeline.find(entry => entry.kind === "answer")!} detail={detail} />
          : <p>{esc(turn.answer) || esc(turn.final_answer) || "执行记录未保存"}</p>}
        {turn.expected_action || turn.task_judge || turn.judge ? <div className="result-turn-scores">
          <span>任务行为：期望{actionLabel(turn.expected_action)} / 实际{actionLabel(behavior.actual_action)}</span>
          <span>{behavior.correct === true ? "行为符合" : behavior.correct === false ? "行为不符" : "行为未评分"}</span>
          <span>回答质量：{judgeScoreLabel(turn, null)}</span>
        </div> : null}
      </>}</Fold>;
    })}
  </div>}</Fold>;
}
