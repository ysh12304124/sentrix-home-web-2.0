import { useId, useState, type ReactNode } from "react";
import type { RunItemDetail } from "../../api/types.ts";
import { toolsForDebugCall, type Dict, type TraceTimelineEntry } from "../../lib/agentTrace.ts";
import { fmtMs } from "../../lib/format.ts";
import { toolDurationMs, toolLatencySegments } from "../../lib/trace.ts";
import { repeatedMessagePrefix, traceNodeSources, type TraceSource } from "../../lib/traceNodes.ts";
import { ModelPerformanceDetails, TimingSegments } from "./TracePerformance.tsx";
import "./traceNodes.css";

/** 原始 JSON 全字段可查；大对象只在展开时挂载。 */
export function RawTraceRecord({ label, value, path, defaultOpen = false }: { label: ReactNode; value: unknown; path?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className="trace-raw-record" open={open} onToggle={e => {
    if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
  }}><summary>{label}</summary>{path ? <code className="trace-source-path">{path}</code> : null}
    {open ? <pre>{typeof value === "string" ? value : value === undefined ? "未记录" : JSON.stringify(value, null, 2)}</pre> : null}
  </details>;
}

function SavedMessages({ detail, turnIndex, call }: { detail: RunItemDetail; turnIndex: number; call: Dict }) {
  if (!Array.isArray(call.messages)) return <p className="trace-node-note">完整输入未记录。</p>;
  const prefix = repeatedMessagePrefix(detail, turnIndex, call);
  const messages = call.messages;
  const row = (message: unknown, index: number) => <RawTraceRecord key={index}
    label={<>messages[{index}] · {String((message as Dict | null)?.role ?? "角色未记录")}</>} value={message} />;
  return <section className="trace-saved-messages"><p>本次保存输入 · {messages.length} 条消息</p>
    {prefix.count > 0 ? <details className="trace-repeated-context"><summary>前 {prefix.count} 条与 {String(prefix.previous?.step_id ?? "上一次同类调用")} 完全一致 · 本次仍携带</summary>
      {messages.slice(0, prefix.count).map(row)}</details> : null}
    {messages.slice(prefix.count).map((message, index) => row(message, index + prefix.count))}
  </section>;
}

const has = (object: Dict, key: string) => Object.hasOwn(object, key);
export interface TraceNodeDetailsProps { detail: RunItemDetail; turnIndex: number; entry: TraceTimelineEntry; modelLabel?: string; initialTab?: "input" | "output" }
export function TraceNodeDetails({ detail, turnIndex, entry, modelLabel, initialTab = "input" }: TraceNodeDetailsProps) {
  const [tab, setTab] = useState<string>(initialTab);
  const id = useId();
  const call = entry.call;
  const tool = entry.tool;
  const sources = traceNodeSources(detail, turnIndex, entry);
  const rawCall = sources.find(source => source.kind === "debug")?.value as Dict | undefined;
  // 字段级展示使用保存的调试对象；只有旧档 prompt 投影才读取 legacy call。
  const io = rawCall ?? call;
  const rawSection = (source: TraceSource, index: number) => <RawTraceRecord key={source.path + index}
    label={source.path} path={source.path} value={source.value} />;
  const turn = (sources.find(source => source.kind === "turn")?.value ?? {}) as Dict;
  const tabs = [{ key: "input", title: "输入" }, { key: "output", title: "输出" }, { key: "metrics", title: "耗时与指标" }, { key: "raw", title: "原始记录" }];
  return <div className="trace-node-details">
    {call ? <p className="trace-node-note">{call.model != null ? `模型 ${String(call.model)}` : modelLabel ? `任务配置模型 ${modelLabel}` : "模型未记录"}{call.role != null ? ` · ${String(call.role)}` : ""}</p> : null}
    <div className="trace-detail-tabs" role="tablist" aria-label="节点记录">
      {tabs.map(t => <button key={t.key} id={`${id}-${t.key}`} role="tab" type="button" aria-selected={tab === t.key}
        aria-controls={`${id}-panel`} onClick={() => setTab(t.key)}>{t.title}</button>)}
    </div>
    <div key={tab} id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${tab}`} className="trace-detail-panel">
      {tab === "input" && <>
        {call && io ? <><SavedMessages detail={detail} turnIndex={turnIndex} call={io} />
          <RawTraceRecord label={Array.isArray(io.tools) ? `本次可见 tools · ${io.tools.length} 项` : "本次可见 tools · 未记录"} value={io.tools} />
          <p className="trace-node-note">以上为应用层已保存输入；未采集的 HTTP 报文字段不在此补写。</p></> : tool ?
          <RawTraceRecord defaultOpen label="工具参数 · arguments" value={tool.arguments} /> : entry.kind === "answer" ?
          <p className="trace-node-note">这是本轮保存的回答结果，未额外调用模型。输入请查看前面的调用节点。</p> :
          <RawTraceRecord label="已保存事件" value={entry.event} />}
      </>}
      {tab === "output" && <>
        {call && io ? <><RawTraceRecord defaultOpen label={has(io, "response") ? "已保存 response · 全部字段" : "response · 未记录"} value={io.response} />
          {io.error != null ? <RawTraceRecord label="错误原文" value={io.error} /> : null}</> : tool ? <>
          {has(tool, "result") ? <RawTraceRecord defaultOpen label="工具输出 · result" value={tool.result} /> : null}
          {has(tool, "observation") ? <RawTraceRecord defaultOpen label="工具观察 · observation" value={tool.observation} /> : null}
          {!has(tool, "result") && !has(tool, "observation") ? <p>工具输出未记录。</p> : null}</> : entry.kind === "answer" ? <>
          <p className="trace-saved-answer">{entry.answer === "" ? "（空字符串）" : entry.answer ?? "回答未记录"}</p>
          <p className="trace-node-note">状态 {String(turn.status ?? turn.agent_status ?? "未记录")} · 结束原因 {String(turn.termination_reason ?? "未记录")}</p>
          {has(turn, "attachments") ? <RawTraceRecord label="已保存附件" value={turn.attachments} /> : null}</> :
          <RawTraceRecord label="已保存事件" value={entry.event} />}
      </>}
      {tab === "metrics" && <>
        {call ? <><div className="trace-node-metrics">
          {call.role != null ? <span>角色 {String(call.role)}</span> : null}
          {call.attempt != null ? <span>attempt {String(call.attempt)}</span> : null}
          <span>请求耗时 {fmtMs(call.duration_ms ?? call.total_ms)}</span>
          <span>TTFT {call.ttft_ms != null ? fmtMs(call.ttft_ms) : call.streamed === false ? "不适用（非流式）" : "未记录"}</span>
          <span>Token 输入 {String(call.prompt_tokens ?? "未记录")} / 输出 {String(call.completion_tokens ?? "未记录")}</span>
          {call.tokens_per_second != null ? <span>{String(call.tokens_per_second)} token/s</span> : null}
        </div><ModelPerformanceDetails detail={detail} call={call} tools={toolsForDebugCall(detail, turnIndex, call)} />
          {sources.filter(source => source.kind === "metrics").map(rawSection)}</> : tool ? <>
          <p>工具耗时 {fmtMs(toolDurationMs(tool))}</p>
          <TimingSegments title="工具内部耗时" total={toolDurationMs(tool)} segments={toolLatencySegments(tool)} />
        </> : <p className="trace-node-note">{entry.kind === "answer" ? "回合结果不计作一次额外调用。" : "事件自身耗时未单独采集。"}</p>}
      </>}
      {tab === "raw" && <>
        <p className="trace-node-note">各来源分别展示全部已保存字段，保留原值；同名字段不互相覆盖。</p>
        {sources.length ? sources.map(rawSection) : <p>没有唯一匹配的原始对象，请查看完整记录。</p>}
      </>}
    </div>
  </div>;
}
