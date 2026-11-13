import { assetFileUrl, assetPreviewUrl, type TimelinePhoto } from "../../shared/api/client";

const HEIC_SUFFIXES = [".heic", ".heif"];
const VIDEO_SUFFIXES = [".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"];

/**
 * 浏览器直接渲染不了的格式走服务端转码预览，其余走原图。
 *
 * 时间线卡片与事件详情共用这一条判断——两处各写一遍，改格式支持时迟早漏一处。
 */
export function timelinePhotoUrl(photo: TimelinePhoto): string {
  const lowered = photo.file_name.toLowerCase();
  return HEIC_SUFFIXES.some((suffix) => lowered.endsWith(suffix))
    ? assetPreviewUrl(photo.asset_id)
    : assetFileUrl(photo.asset_id);
}

export function isTimelineVideo(photo: TimelinePhoto): boolean {
  const lowered = photo.file_name.toLowerCase();
  return VIDEO_SUFFIXES.some((suffix) => lowered.endsWith(suffix));
}
