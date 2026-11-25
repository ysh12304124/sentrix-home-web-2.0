import type { MediaLike } from "../../api/media.ts";
import { isVideoMedia, imageUrl, pauseVideoAtEvidenceFrame } from "../../api/media.ts";
import { esc } from "../../lib/format.ts";

/** 1.0 `media.file_name || media.media_id || media.image_id`。 */
function mediaLabel(media: MediaLike): string {
  return esc(media.file_name) || esc(media.media_id) || esc(media.image_id);
}

/** 1.0 `:3529-3537` 共用的图片瓦片（含视频证据帧）。 */
export function MediaTile({
  media,
  onOpenImage,
  unmatched = false,
  unmatchedLabel = "未命中",
}: {
  media: MediaLike;
  onOpenImage: (media: MediaLike) => void;
  unmatched?: boolean;
  unmatchedLabel?: string;
}) {
  const src = imageUrl(media);
  return (
    <div className="image-tile">
      {isVideoMedia(media) && src ? (
        <video
          src={src}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(event) => pauseVideoAtEvidenceFrame(media, { target: event.currentTarget })}
        ></video>
      ) : src ? (
        <img src={src} alt={esc(media.file_name)} loading="lazy" onClick={() => onOpenImage(media)} />
      ) : (
        <span className="image-empty">无媒体</span>
      )}
      <span className="image-label">
        {mediaLabel(media)}
        {unmatched ? <em> · {unmatchedLabel}</em> : null}
      </span>
    </div>
  );
}
