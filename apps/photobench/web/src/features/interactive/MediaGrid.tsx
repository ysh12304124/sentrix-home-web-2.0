import { decorateMedia, imageUrl, isVideoMedia, type MediaLike } from '../../api/media.ts';

export function MediaGrid({ items, albumId, onOpenMedia }: {
  items: MediaLike[]; albumId: string; onOpenMedia: (target: { url: string; name: string }) => void;
}) {
  return <div className="image-grid interactive-media">{decorateMedia(items, albumId).map((media, i) => {
    const url = imageUrl(media), name = String(media.file_name || media.media_id || media.asset_id || '');
    return <div key={String(media.asset_id || media.media_id || i)} className="image-tile">
      {url ? isVideoMedia(media) ? <video src={url} controls preload="metadata" playsInline /> : <img src={url} alt={name} loading="lazy" onClick={() => onOpenMedia({ url, name })}/> : <span>未找到媒体</span>}
      <span className="image-label">{name}</span>
    </div>;
  })}{!items.length && <span className="muted small">无</span>}</div>;
}
