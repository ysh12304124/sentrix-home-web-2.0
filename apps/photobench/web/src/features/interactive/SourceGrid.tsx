import { useRef, useState } from "react";
import { BASE } from "../../api/client.ts";
import type { SourceRef } from "../../api/types.ts";
import { fmtTimestamp } from '../../lib/format.ts';

function SourceCard({ source, albumId }: { source: SourceRef; albumId: string }) {
  const pending = useRef(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const name = String(source.source_id || source.file_name || "文档");
  const fileName = String(source.file_name || `${name}.txt`);
  const timestamp = String(source.source_timestamp || '');
  const url = source.asset_id
    ? `${BASE}/assets/${encodeURIComponent(String(source.asset_id))}/file`
    : `${BASE}/albums/${encodeURIComponent(albumId)}/documents/${encodeURIComponent(fileName)}`;
  async function loadText() {
    if (pending.current) return;
    pending.current = true;
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`读取失败：${response.status}`);
      setText(await response.text());
    } catch (cause) { setError((cause as Error).message); }
    finally { pending.current = false; }
  }
  return <details className="source-card" onToggle={event => {
    if (event.target !== event.currentTarget) return;
    if (event.currentTarget.open && text === null && !error) void loadText();
  }}>
    <summary><span className="source-card-title">{source.source_type === "email" ? "邮件" : "文档"} · {String(source.title || name)}</span>{timestamp ? <time dateTime={timestamp} title={timestamp}>{fmtTimestamp(timestamp)}</time> : null}</summary>
    <div className="source-card-body"><p className="source-card-id">{name}</p>
    {source.summary ? <p>{String(source.summary)}</p> : null}
    {source.excerpt ? <blockquote>{String(source.excerpt)}</blockquote> : null}
    <pre>{error || text || "加载原文…"}</pre></div>
  </details>;
}

export function SourceGrid({ items, albumId }: { items: SourceRef[]; albumId: string }) {
  return <div className="source-grid">{items.length ? items.map((source, index) =>
    <SourceCard key={String(source.asset_id || source.source_id || index)} source={source} albumId={albumId} />
  ) : <span className="muted small">无</span>}</div>;
}

export function SourceMetrics({ metrics }: { metrics: Record<string, unknown> }) {
  return <details><summary>邮件/文档召回指标</summary><table>
    <thead><tr><th>环节</th><th>命中 / 标准证据</th><th>召回率</th><th>准确率</th></tr></thead>
    <tbody>{[["candidates", "检索候选"], ["reads", "实际阅读"], ["citations", "最终引用"]].map(([key, label]) => {
      const row = metrics[key!] as Record<string, unknown> | undefined;
      if (!row || row.status === "not_applicable") return null;
      const percent = (value: unknown) => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "未记录";
      return <tr key={key}><th>{label}</th><td>{String(row.matched)} / {String(row.gt)}</td><td>{percent(row.recall)}</td><td>{percent(row.precision)}</td></tr>;
    })}</tbody>
  </table></details>;
}
