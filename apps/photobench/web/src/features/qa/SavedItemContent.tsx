import { useEffect, useRef, useState } from "react";
import type { RunItemDetail } from "../../api/types.ts";
import { api } from "../../api/client.ts";

interface Chunk { text: string; offset: number; next_offset?: number | null }

export function SavedItemContent({ detail, path, label }: { detail: RunItemDetail; path: string; label: string }) {
  const epoch = useRef(0);
  const [offsets, setOffsets] = useState<number[]>([]);
  const identity = `${detail.run_id}:${detail.index}:${String((detail.trace_storage as Record<string, unknown>)?.revision)}:${path}`;
  useEffect(() => { epoch.current += 1; setChunk(null); setOffsets([]); setLoading(false); setError(""); return () => { epoch.current += 1; }; }, [identity]);
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load(offset: number, previous = false) {
    const token = ++epoch.current;
    setLoading(true); setError("");
    try {
      const storage = detail.trace_storage as Record<string, unknown>;
      const value = await api<Chunk>(`/runs/${encodeURIComponent(String(detail.run_id))}/items/${detail.index}/content?path=${encodeURIComponent(path)}&offset=${offset}&revision=${encodeURIComponent(String(storage.revision))}`);
      if (token === epoch.current) { setOffsets(values => previous ? values.slice(0, -1) : chunk ? [...values, chunk.offset] : []); setChunk(value); }
    } catch (reason) { if (token === epoch.current) setError(String(reason)); }
    finally { if (token === epoch.current) setLoading(false); }
  }
  return <section className="trace-node-note">
    <button type="button" disabled={loading} onClick={() => chunk ? setChunk(null) : void load(0)}>{loading ? "正在读取…" : chunk ? `收起${label}` : `按块查看${label}`}</button>
    {error ? <p>{error}</p> : null}
    {chunk ? <>
      <pre>{chunk.text}</pre>
      <button type="button" disabled={loading || chunk.offset === 0} onClick={() => void load(offsets.at(-1) ?? 0, true)}>上一块</button>
      <button type="button" disabled={loading || chunk.next_offset == null} onClick={() => void load(chunk.next_offset ?? 0)}>下一块</button>
      <span>字符 {chunk.offset + 1} 起 · {chunk.next_offset == null ? "已到末尾" : "还有后续内容"}</span>
    </> : null}
  </section>;
}
