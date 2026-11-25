import type { ReactNode } from 'react';
import type { MediaLike } from '../../api/media.ts';
import { decorateMedia, isVideoMedia, mediaKey, mediaRefs } from '../../api/media.ts';
import type { RunItemDetail, SourceRef } from '../../api/types.ts';
import { itemEvidenceMedia, itemMedia, toolRecallMedia } from '../../lib/evidence.ts';
import { SourceGrid, SourceMetrics } from './SourceGrid.tsx';
import { MediaTile } from './MediaTile.tsx';

type OpenMedia = (media: MediaLike) => void;

function uniqueSources(items: SourceRef[]): SourceRef[] {
  return [...new Map(items.filter(s => s.source_id).map(s => [`${s.source_type || 'document'}:${String(s.source_id)}`, s])).values()];
}
function deliveredSources(item: RunItemDetail): SourceRef[] {
  return uniqueSources([...(item.predicted_source_refs || []), ...(item.attachments || []).filter(s => s.source_id)]);
}

export function UnifiedMedia({ title, media, sources, albumId, onOpenMedia, empty = '无', unmatchedLabel = '未命中', renderMedia, children }: {
  title: string; media: MediaLike[]; sources: SourceRef[]; albumId: string; onOpenMedia: OpenMedia;
  empty?: string; unmatchedLabel?: string;
  renderMedia?: (media: MediaLike) => ReactNode;
  children?: ReactNode;
}) {
  const files = [...new Map(media.map(m => [mediaKey(m), m])).values()];
  const texts = uniqueSources(sources);
  return <section className="unified-media" aria-label={title}>
    <h4>{title}（{files.length + texts.length}）</h4>
    {children}
    {files.length > 0 && <div className="image-grid">{decorateMedia(files, albumId).map(m => renderMedia ? renderMedia(m) : <MediaTile key={mediaKey(m)} media={m}
      onOpenImage={onOpenMedia} unmatched={m.matched === false} unmatchedLabel={unmatchedLabel}/>)}</div>}
    {texts.length > 0 && <SourceGrid items={texts} albumId={albumId}/>}
    {!files.length && !texts.length && <span className="muted small">{empty}</span>}
  </section>;
}

type ResultProps = { item: RunItemDetail; albumId: string; onOpenMedia: OpenMedia };
export function CandidateMedia({ item, albumId, onOpenMedia }: ResultProps) {
  return <>
    <UnifiedMedia title="工具召回候选媒体" media={toolRecallMedia(item, albumId)} sources={item.retrieved_source_refs || []}
      albumId={albumId} onOpenMedia={onOpenMedia} empty="检索未返回可识别媒体"/>
    {!!item.read_source_refs?.length && <details className="qa-detail-block"><summary>实际读取的邮件/文档（{uniqueSources(item.read_source_refs).length}）</summary>
      <SourceGrid items={uniqueSources(item.read_source_refs)} albumId={albumId}/></details>}
    {item.source_metrics && <SourceMetrics metrics={item.source_metrics}/>}
  </>;
}
export function DeliveredMedia({ item, albumId, onOpenMedia }: ResultProps) {
  return <UnifiedMedia title="模型交付媒体" media={itemEvidenceMedia(item, albumId)} sources={deliveredSources(item)}
    albumId={albumId} onOpenMedia={onOpenMedia} empty="模型未交付媒体"/>;
}
export function GroundTruthMedia({ item, albumId, onOpenMedia, fromDataset = false }: ResultProps & { fromDataset?: boolean }) {
  const retrieval = fromDataset ? mediaRefs(item, 'retrieval') : itemMedia(item, albumId, true);
  const media = [...retrieval, ...mediaRefs(item, 'answer_evidence').filter(m => !retrieval.some(r => mediaKey(r) === mediaKey(m)))];
  const evidence = (item.evidence_v12 ?? {}) as Record<string, unknown>;
  const frames = Array.isArray(evidence.frames) ? (evidence.frames as Record<string, unknown>[]).filter(frame =>
    typeof frame?.frame === 'string' && /^keyframes\/[^/]+\/[^/]+\.(jpg|jpeg|png|webp)$/i.test(frame.frame)
      && typeof frame.time_seconds === 'number' && Number.isFinite(frame.time_seconds) && frame.time_seconds >= 0
  ) : [];
  const segments = Array.isArray(evidence.segments) ? evidence.segments as Record<string, unknown>[] : [];
  const unmatchedLabel = Number(item.metrics_contract_version) === 2 ? '未交付' : '未命中';
  function renderReferenceMedia(file: MediaLike) {
    const videoStem = String(file.video_id || file.media_id || file.file_name || '').replace(/\.mp4$/i, '');
    const matchingFrames = isVideoMedia(file) ? frames.filter(frame => String(frame.frame).split('/')[1] === videoStem) : [];
    if (!matchingFrames.length) return <MediaTile key={mediaKey(file)} media={file} onOpenImage={onOpenMedia}
      unmatched={file.matched === false} unmatchedLabel={unmatchedLabel}/>;
    return matchingFrames.map((frame, index) => {
      const seconds = Number(frame.time_seconds);
      const segment = segments.find(s => Number(s.start_seconds) <= seconds && seconds <= Number(s.end_seconds));
      return <div key={`${mediaKey(file)}-${index}`}>
        <MediaTile media={{...file, source_timestamp_sec: seconds}} onOpenImage={onOpenMedia}
          unmatched={file.matched === false} unmatchedLabel={unmatchedLabel}/>
        <small>代表帧 {seconds.toFixed(2)} 秒{segment ? ` · 标注时间段 ${String(segment.start_seconds)}–${String(segment.end_seconds)} 秒` : ''}</small>
      </div>;
    });
  }
  return <UnifiedMedia title="检索 GT 媒体" media={media}
    sources={[...(item.retrieval_source_refs || []), ...(item.answer_evidence_source_refs || [])]}
    albumId={albumId} onOpenMedia={onOpenMedia} unmatchedLabel={unmatchedLabel} renderMedia={renderReferenceMedia}>
    {frames.length === 0 && evidence.status === 'not_required' && <p className="muted">本题不要求关键帧，无代表帧时间点。</p>}
    {frames.length === 0 && evidence.status && evidence.status !== 'not_required' ? <p className="muted">本题没有可用的代表帧时间点{evidence.reason ? `（${String(evidence.reason)}）` : ''}。</p> : null}
  </UnifiedMedia>;
}
