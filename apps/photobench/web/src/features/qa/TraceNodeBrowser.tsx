import { useEffect, useId, useState } from "react";
import type { RunItemDetail } from "../../api/types.ts";
import { LazyTraceNodeDetails } from "./LazyTraceNodeDetails.tsx";
import { traceListUrl, traceNodeEntry, type LazyTraceNode, type LazyTracePage } from "../../lib/lazyTrace.ts";
import { api } from "../../api/client.ts";
import { turnTimeline, turnProgressEvents, runtimeTurns } from "../../lib/agentTrace.ts";
import { callStatus } from "../../lib/format.ts";
import { traceDurationLabel, traceNodeTitle } from "../../lib/traceNodes.ts";
import { toolStatusLabel } from "./qaShared.ts";
import { RawTraceRecord, TraceNodeDetails } from "./TraceNodeDetails.tsx";

function pageNodes(page: LazyTracePage, kind: string): LazyTraceNode[] {
  if (kind !== "calls" || page.order_version === 2 || page.total !== page.nodes.length) return page.nodes;
  const rows = page.nodes.map(node => ({...node.metadata, _lazyNodeId:node.node_id, conversation_turn:0}));
  const calls = rows.filter((_, index) => page.nodes[index].kind !== "tool");
  const tools = rows.filter((_, index) => page.nodes[index].kind === "tool");
  const timeline = turnTimeline({runtime_turns:[{index:0,trace_bundle:{debug_calls:calls}}],tool_trace:tools},0);
  const nodes = new Map(page.nodes.map(node => [node.node_id,node]));
  return timeline.flatMap(entry => {
    const node = nodes.get(String((entry.call ?? entry.tool)?._lazyNodeId));
    return node ? [node] : [];
  });
}

export function LazyTraceNodeBrowser({ detail, turnIndex, initialKind = "calls", modelLabel }: { detail: RunItemDetail; turnIndex: number; initialKind?: string; modelLabel?: string }) {
  const [result, setResult] = useState<{ url: string; page?: LazyTracePage; error?: string }>();
  const [selection, setSelection] = useState<{ url: string; node: LazyTraceNode }>();
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState(initialKind);
  const listUrl = `${traceListUrl(detail, turnIndex, kind)}&offset=${offset}`;
  const page = result?.url === listUrl ? result.page : undefined;
  const error = result?.url === listUrl ? result.error : undefined;
  const selected = selection?.url === listUrl ? selection.node : undefined;
  useEffect(() => {
    const controller = new AbortController();
    void api<LazyTracePage>(listUrl, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setResult({ url: listUrl, page: value }); })
      .catch(reason => { if (!controller.signal.aborted) setResult({ url: listUrl, error: String(reason) }); });
    return () => controller.abort();
  }, [listUrl]);
  const entry = selected ? traceNodeEntry(selected) : null;
  return <>
    <div className="trace-view-tabs" role="tablist" aria-label="轨迹类型">
      {[["calls", "调用流程"], ["event", "原始事件"], ["sources", "性能来源"], ["judges", "评分请求"]].map(([value, label]) =>
        <button key={value} type="button" role="tab" aria-selected={kind === value} onClick={() => { setKind(value); setOffset(0); }}>{label}</button>)}
    </div>
    <div className="trace-node-workspace">
      <nav aria-label="调用节点" className="trace-node-route"><ol>
        {error ? <li className="trace-node-note">节点摘要读取失败：{error}</li> : !page ? <li className="trace-node-note">正在读取节点摘要…</li> : null}
        {page && pageNodes(page, kind).map((node, index) => {
          const entry = traceNodeEntry(node), raw = node.metadata ?? {};
          return <li key={node.node_id}><button type="button" aria-pressed={selected?.node_id === node.node_id}
            className={`trace-route-node trace-route-${entry.kind}`} onClick={() => setSelection({ url: listUrl, node })}>
            <span className="trace-node-number">{(page.offset ?? 0) + index + 1}</span><span>
              <strong>{traceNodeTitle(entry)}</strong>
              {raw.step_id != null ? <small>{String(raw.step_id)}{raw.attempt != null ? ` · attempt ${String(raw.attempt)}` : ""}</small> : null}
              <small>{traceDurationLabel(entry)} · {entry.tool ? toolStatusLabel(raw) : callStatus(raw)}</small>
              {raw.parent_step_id ? <small>父步骤 {String(raw.parent_step_id)}</small> : null}
            </span>
          </button></li>;
        })}
      </ol>
      {page ? <div className="trace-node-pagination">
        <span>{page.total ? `${(page.offset ?? 0) + 1}–${(page.offset ?? 0) + page.nodes.length} / ${page.total} 个节点` : "暂无节点"}</span>
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button>
        <button type="button" disabled={page.next_offset == null} onClick={() => setOffset(page.next_offset ?? 0)}>下一页</button>
      </div> : null}</nav>
      <section className="trace-node-inspector" aria-label="节点详情" aria-live="polite">
        {selected && entry ? <><header><h5>{traceNodeTitle(entry)}</h5><span>{traceDurationLabel(entry)}</span></header>
          <LazyTraceNodeDetails key={`${listUrl}:${selected.node_id}`} detail={detail} node={selected} modelLabel={modelLabel}/></> :
          <p className="trace-node-note">选择左侧节点，查看输入、输出和耗时。正文仅在查看时读取。</p>}
      </section>
    </div>
  </>;
}

export function TraceNodeBrowser({ detail, turnIndex, modelLabel }: { detail: RunItemDetail; turnIndex: number; modelLabel?: string }) {
  const [view, setView] = useState("flow");
  const [selection, setSelection] = useState<string | null>(null);
  const id = useId();
  const turn = runtimeTurns(detail).find((value, index) => Number(value.index ?? index) === turnIndex);
  const nodes = turnTimeline(detail, turnIndex).filter(entry => entry.kind !== "event");
  const selected = nodes.find(entry => entry.key === selection) ?? nodes[0];
  const events = turnProgressEvents(turn);
  const eventRecords = events.length ? events : Array.isArray(turn?.debug_trace) ? turn.debug_trace : [];
  if ((detail.trace_storage as Record<string, unknown> | undefined)?.version === 3) {
    return <section className="trace-node-browser" aria-label="执行轨迹节点视图">
      <LazyTraceNodeBrowser detail={detail} turnIndex={turnIndex} modelLabel={modelLabel} />
    </section>;
  }
  const views = [{ key: "flow", title: "调用流程" }, { key: "events", title: `原始事件 · ${eventRecords.length} 条（核对用）` }, { key: "raw", title: "完整记录" }];
  return <section className="trace-node-browser" aria-label="执行轨迹节点视图">
    <div className="trace-view-tabs" role="tablist" aria-label="轨迹视图">{views.map(v =>
      <button key={v.key} type="button" role="tab" id={`${id}-${v.key}`} aria-selected={view === v.key}
        aria-controls={`${id}-view`} onClick={() => setView(v.key)}>{v.title}</button>)}</div>
    <div role="tabpanel" id={`${id}-view`} aria-labelledby={`${id}-${view}`}>
      {view === "flow" && <>
        <p className="trace-node-note">连线表示记录顺序；父调用按已保存身份关联。节点耗时只计自身，关联工具合计见“耗时与指标”。</p>
        <div className="trace-node-workspace">
          <nav aria-label="调用节点" className="trace-node-route"><ol>{nodes.map((entry, index) => {
            const raw = entry.call ?? entry.tool ?? {};
            return <li key={entry.key}><button type="button" aria-pressed={selected?.key === entry.key}
              className={`trace-route-node trace-route-${entry.kind}`} onClick={() => setSelection(entry.key)}>
              <span className="trace-node-number">{index + 1}</span><span>
                <strong>{traceNodeTitle(entry)}</strong>
                {raw.step_id != null ? <small>{String(raw.step_id)}{raw.attempt != null ? ` · attempt ${String(raw.attempt)}` : ""}</small> : null}
                <small>{traceDurationLabel(entry)}{entry.kind !== "answer" ? ` · ${entry.tool ? toolStatusLabel(raw) : callStatus(raw)}` : ""}</small>
                {entry.unbound ? <small>父调用未确定</small> : entry.tool?.parent_step_id ? <small>父步骤 {String(entry.tool.parent_step_id)}</small> : null}
              </span>
            </button></li>;
          })}</ol></nav>
          <section className="trace-node-inspector" aria-label="节点详情" aria-live="polite">
            {selected ? <><header><h5>{traceNodeTitle(selected)}</h5><span>{traceDurationLabel(selected)}</span></header>
              <TraceNodeDetails key={selected.key} detail={detail} turnIndex={turnIndex} entry={selected} modelLabel={modelLabel}
                initialTab={selected.kind === "answer" ? "output" : "input"} /></> : <p>此存档没有可展示的调用轨迹。</p>}
          </section>
        </div>
      </>}
      {view === "events" && <div className="trace-node-events">{eventRecords.map((event, index) =>
        <RawTraceRecord key={index} label={`${index + 1}. ${String((event as Record<string, unknown>).kind ?? (event as Record<string, unknown>).type ?? "事件")}`} value={event} />)}</div>}
      {view === "raw" && <RawTraceRecord label="本题完整已保存对象 · 全部字段" value={detail} path="$" />}
    </div>
  </section>;
}
