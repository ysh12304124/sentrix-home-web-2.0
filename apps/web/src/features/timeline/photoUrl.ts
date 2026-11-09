import { assetPreviewUrl, assetThumbnailUrl, type TimelinePhoto } from "../../shared/api/client";

/** 事件详情放大图：1600px 转码预览，浏览器缓存。 */
export function timelinePhotoUrl(photo: TimelinePhoto): string {
  return assetPreviewUrl(photo.asset_id);
}

/** 事件详情里的缩略图：480px，避免几十张原图同时解码。 */
export function timelinePhotoThumbUrl(photo: TimelinePhoto): string {
  return assetThumbnailUrl(photo.asset_id);
}
