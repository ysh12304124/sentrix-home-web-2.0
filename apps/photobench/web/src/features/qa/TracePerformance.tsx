import type { RunItemDetail } from "../../api/types.ts";
import type { Dict } from "../../lib/agentTrace.ts";
import { callTypeDescription, finiteMetric, fmtMs } from "../../lib/format.ts";
import { callAgentLoopTiming, callBudget, callLatencySegments, callObservation, toolDurationMs } from "../../lib/trace.ts";
import type { LatencySegment } from "../../lib/trace.ts";
import { segmentStyle } from "./qaShared.ts";

/** 只有各项可以相加时才画组成条；并行分量保留各自时长。 */
export function TimingSegments({ title, segments, total }: { title: string; segments: LatencySegment[]; total: unknown }) {
  if (!segments.length) return null;
  const wall = finiteMetric(total);
  const sum = segments.reduce((n, segment) => n + segment.ms, 0);
  const additive = wall != null && sum <= wall + 0.5;
  return <section className="latency-composition call-latency-composition">
    <div className="latency-composition-head"><strong>{title}</strong><span>{fmtMs(total)}</span></div>
    {additive ? <div className="latency-bar" role="img" aria-label={title}>
      {segments.map((segment, i) => <span key={i} className={`latency-segment latency-segment-${segment.kind}`}
        style={segmentStyle(segment, segments)} data-tooltip={`${segment.label} · ${fmtMs(segment.ms)}`}>
        <b>{fmtMs(segment.ms)}</b>
      </span>)}
    </div> : <p className="muted small">分项时长单独展示，不相加。</p>}
    <div className="latency-segment-legend">{segments.map((segment, i) => <span key={i}>
      <i className={`latency-dot latency-dot-${segment.kind}`} />{segment.label} {fmtMs(segment.ms)}
    </span>)}</div>
  </section>;
}

export function ModelPerformanceDetails({ detail, call }: { detail?: RunItemDetail; call: Dict }) {
  const observation = callObservation(call);
  const timing = detail ? callAgentLoopTiming(detail, call) : null;
  const incompleteToolTiming = timing?.tools.some(tool => toolDurationMs(tool) == null);
  return <section className="trace-call-performance">
    {detail && timing && !incompleteToolTiming ? <TimingSegments title="本步耗时 · 模型与关联工具" total={timing.totalMs} segments={callLatencySegments(detail, call)} /> : null}
    {incompleteToolTiming ? <p className="muted small">关联工具的耗时未完整记录，本步合计未知。</p> : null}
    <div className="call-purpose-grid">
      <span><small>用途</small><b>{callTypeDescription(call)}</b></span>
      <span><small>触发</small><b>{observation.trigger}</b></span>
      <span><small>结果</small><b>{observation.outcome}</b></span>
      <span><small>记录来源</small><b>{observation.source}</b></span>
    </div>
    <div className="call-budget-line">
      <span>请求预算（输入 + 输出上限 / 上下文）</span><b>{callBudget(call)}</b>
      {call.preflight_prompt_tokens != null ? <span>预检输入 {String(call.preflight_prompt_tokens)} tokens</span> : null}
      {observation.relatedTool !== "-" ? <span>关联工具 {observation.relatedTool}</span> : null}
      {observation.parentStep !== "-" ? <span>父步骤 {observation.parentStep}</span> : null}
    </div>
    {call.error ? <p className="error">{String(call.error)}</p> : null}
  </section>;
}
