import { useState } from 'react';
import { imageUrl, type MediaLike } from '../../api/media.ts';
import { Lightbox, type LightboxTarget } from '../../components/Lightbox.tsx';
import type { RunItemDetail } from '../../api/types.ts';
import { DebugTracePanel } from '../qa/DebugTracePanel.tsx';
import { CandidateMedia, DeliveredMedia } from './UnifiedMedia.tsx';
import { fmtTimestamp } from '../../lib/format.ts';

type Event = { timestamp?: unknown; text?: unknown; kind?: unknown; status?: unknown };
export function ExecutionView({ item, albumId, modelLabel, running = false, phases, hideAnswer = false }: {
  item?: RunItemDetail | null; albumId: string; modelLabel: string; running?: boolean;
  phases?: Record<string, { status?: string; error?: string }>;
  hideAnswer?: boolean;
}) {
  const [lightbox, setLightbox] = useState<LightboxTarget | null>(null);
  const events = (item?.runtime_turns || []).flatMap(turn => Array.isArray(turn.trace) ? turn.trace as Event[] : []);
  const openMedia = (media: MediaLike) => {
    setLightbox({ url: imageUrl(media), name: String(media.file_name || media.media_id || '') });
  };
  return <section className="execution-view" aria-label="实时回答与轨迹">
    <div className="execution-state" role="status">{running ? '运行中 · 可查看日志与轨迹' : '执行记录'}</div>
    {!!phases && <div className="execution-phases">{Object.entries(phases).map(([key, phase]) => <span key={key}>{key} · {phase.status || '等待'}{phase.error ? ` · ${phase.error}` : ''}</span>)}</div>}
    {item && <>{!hideAnswer && <><h4>模型回答</h4><p className="execution-answer">{String(item.final_answer || item.answer || item.error || (running ? '正在回答…' : '未生成回答'))}</p></>}
      <CandidateMedia item={item} albumId={albumId} onOpenMedia={openMedia}/>
      <DeliveredMedia item={item} albumId={albumId} onOpenMedia={openMedia}/>
    </>}
    {item?.trace_storage ? <p className="muted small">运行日志可在下方轨迹的“原始事件”中按需查看。</p> : <details className="execution-event-log" open><summary>运行日志（{events.length}）</summary>
      <div className="execution-events" aria-live="polite">{events.map((event, i) => <p key={i}><time dateTime={String(event.timestamp || '')} title={`${fmtTimestamp(event.timestamp)} · ${String(event.timestamp || '')}`}>{fmtTimestamp(event.timestamp, true)}</time> {String(event.text || event.kind || event.status || '')}</p>)}
        {!events.length && <p className="muted">{running ? '等待执行事件…' : '暂无执行事件'}</p>}</div>
    </details>}
    {item && <DebugTracePanel detail={item} modelLabel={modelLabel} defaultOpen/>}
    <Lightbox target={lightbox} onClose={() => setLightbox(null)}/>
  </section>;
}
