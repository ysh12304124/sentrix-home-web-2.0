import { useEffect, useState } from "react";
import { api } from "../api/client.ts";
import type { RunItemDetail } from "../api/types.ts";
import type { Dict, TraceTimelineEntry } from "./agentTrace.ts";
import { finiteMetric } from "./format.ts";

export interface LazyTraceNode {
  node_id: string;
  kind: string;
  turn_index: number;
  metadata?: Dict;
  source_path?: (string | number)[];
  fields?: string[];
  message_count?: number;
  message_roles?: { index: number; role: string }[];
}
export interface LazyTracePage {
  nodes: LazyTraceNode[];
  total: number;
  offset?: number;
  next_offset?: number | null;
  read_revision?: string;
  order_version?: number;
}
export interface TraceOverview {
  model_call_count: number;
  tool_call_count: number;
  model_ms: number | null;
  tool_ms: number | null;
  timing_complete: boolean;
}

export function traceItemBase(detail: RunItemDetail): string {
  return String((detail.trace_storage as Dict | undefined)?.base_url ||
    `/runs/${encodeURIComponent(String(detail.run_id))}/items/${detail.index}`);
}

export function traceNodeEntry(node: LazyTraceNode): TraceTimelineEntry {
  const metadata = node.metadata ?? {};
  if (node.kind === "tool") return { key: node.node_id, kind: "tool", tool: metadata };
  if (["model", "planner", "judge", "metrics"].includes(node.kind)) {
    const kind = node.kind === "metrics" ? metadata.call_type === "planner" ? "planner" : "model" : node.kind;
    return { key: node.node_id, kind: kind as TraceTimelineEntry["kind"], call: metadata };
  }
  return { key: node.node_id, kind: "event", event: metadata };
}

export function traceListUrl(detail: RunItemDetail, turn: number, kind: string): string {
  const revision = String((detail.trace_storage as Dict)?.revision ?? "");
  return `${traceItemBase(detail)}/trace?turn=${turn}&kind=${kind}&revision=${encodeURIComponent(revision)}`;
}

/** Older format-3 servers expose the same bounded metadata pages, without an aggregate yet. */
export async function readTraceOverview(detail: RunItemDetail, signal: AbortSignal): Promise<TraceOverview> {
  let modelCount = 0, toolCount = 0, modelMs = 0, toolMs = 0, complete = true;
  const turns = detail.runtime_turns?.length ? detail.runtime_turns : [{ index: 0 }];
  for (const [position, turn] of turns.entries()) {
    const turnIndex = Number(turn.index ?? position);
    let debugCount = 0, debugMs = 0, debugComplete = true, metricsCount = 0, metricsMs = 0, metricsComplete = true;
    for (const kind of ["calls", "sources"]) {
      let offset: number | null = 0;
      while (offset !== null) {
        const page: LazyTracePage = await api(`${traceListUrl(detail, turnIndex, kind)}&offset=${offset}`, { signal });
        for (const node of page.nodes) {
          const m = node.metadata ?? {};
          const duration = finiteMetric(m.duration_ms ?? m.total_ms) ?? (finiteMetric(m.latency_s) == null ? null : Number(m.latency_s) * 1000);
          if (node.kind === "tool") {
            toolCount++; toolMs += duration ?? 0; complete &&= duration !== null;
          } else if (node.kind === "metrics") {
            metricsCount++;
            if (m.call_type !== "tool_internal") { metricsMs += duration ?? 0; metricsComplete &&= duration !== null; }
          } else if (["model", "planner", "judge"].includes(node.kind)) {
            debugCount++;
            if (m.call_type !== "tool_internal") { debugMs += duration ?? 0; debugComplete &&= duration !== null; }
          }
        }
        offset = page.next_offset != null && page.next_offset > (page.offset ?? 0) ? page.next_offset : null;
      }
    }
    modelCount += metricsCount || debugCount;
    modelMs += metricsCount ? metricsMs : debugMs;
    complete &&= metricsCount ? metricsComplete : debugComplete;
  }
  return { model_call_count: modelCount, tool_call_count: toolCount, model_ms: modelMs, tool_ms: toolMs, timing_complete: complete };
}

export function useTraceOverview(detail: RunItemDetail): { overview?: TraceOverview; loading: boolean; error: string } {
  const saved = detail.trace_read_summary as TraceOverview | undefined;
  const split = (detail.trace_storage as Dict | undefined)?.version === 3;
  const [result, setResult] = useState<{ key: string; overview?: TraceOverview; error: string }>();
  const key = `${detail.run_id}:${detail.index}:${String((detail.trace_storage as Dict | undefined)?.revision)}`;
  useEffect(() => {
    if (!split || saved) return;
    const controller = new AbortController();
    void readTraceOverview(detail, controller.signal).then(overview => {
      if (!controller.signal.aborted) setResult({ key, overview, error: "" });
    }).catch(reason => { if (!controller.signal.aborted) setResult({ key, error: String(reason) }); });
    return () => controller.abort();
  }, [key, split, saved, detail]); // The revision pins every page to one committed item.
  return { overview: saved ?? (result?.key === key ? result.overview : undefined), loading: split && !saved && result?.key !== key,
    error: result?.key === key ? result.error : "" };
}
