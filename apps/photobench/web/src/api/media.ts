/**
 * 媒体路径与媒体引用。逐条移植 1.0 `App.vue` 的
 * `inferMediaType`(:384) / `mediaKey`(:389) / `mediaRefs`(:394) / `imageUrl`(:553) /
 * `albumLocalUrl`(:624) / `isVideoMedia`(:631) / `pauseVideoAtEvidenceFrame`(:639) /
 * `decorateMedia`(:646)。
 *
 * **唯一的接口适配**：1.0 的 `albumLocalUrl` 与 `decorateMedia` 从模块级 `activeRun`
 * 读 `album_id`；React 里没有那个全局，所以把 `albumId` 提成第一个参数——**逻辑一字未改**，
 * 只是把「从哪读相册」的入口显式化。
 *
 * **媒体来源的取舍口径照搬 1.0 的两处，不合并**：
 *   - `imageUrl`（:553）：有 `asset_id` 就走 `/api/v1/assets/{id}/file`（按资产 id，最准）；
 *     否则回落到结果里已经带上的 `media_url`。
 *   - `albumLocalUrl` / `decorateMedia`（:624 / :646）：从**文件名**按相册路径拼
 *     `/api/v1/albums/{album}/{photos|videos|faces}/{file}`（按相册布局，是回退口径）。
 * 两条路的取舍条件不同，合并会把「按 id 有」和「按路径拼」混成一件事。
 */

import { BASE } from "./client.ts";
import type { MediaRef, MediaType } from "./types.ts";

/** 媒体引用在成为 `MediaRef` 之前的松散形状（后端可能只给 id / file_name）。 */
export interface MediaLike {
  media_type?: unknown;
  media_id?: unknown;
  image_id?: unknown;
  video_id?: unknown;
  file_name?: unknown;
  media_url?: unknown;
  [key: string]: unknown;
}

const VIDEO_EXT = /\.(?:mp4|mov|m4v|webm|mkv|avi)$/i;
const FACE_FILE = /^faceid_[^/]+\.(?:jpe?g|png|webp)$/i;

/** 1.0 `:384`。显式 `image`/`video` 优先，否则按扩展名或 `video-<n>` 命名判。 */
export function inferMediaType(value: unknown, explicit: unknown = ""): MediaType {
  if (["image", "video"].includes(String(explicit || "").toLowerCase())) {
    return String(explicit).toLowerCase() as MediaType;
  }
  const text = String(value || "").split(/[?#]/)[0];
  return VIDEO_EXT.test(text) || /^video-\d+$/i.test(text.split("/").pop() || "")
    ? "video"
    : "image";
}

/** 1.0 `:389`。GT 与召回比对的稳定键；视频去掉扩展名，整体小写。 */
export function mediaKey(ref: MediaLike | null | undefined): string {
  const mediaType = inferMediaType(
    ref?.media_id || ref?.file_name || ref?.image_id,
    ref?.media_type,
  );
  const name =
    String(ref?.media_id || ref?.file_name || ref?.image_id || "")
      .split(/[/?#]/)
      .filter(Boolean)
      .pop() || "";
  return `${mediaType}:${mediaType === "video" ? name.replace(/\.[^.]+$/, "") : name}`.toLocaleLowerCase();
}

/**
 * 1.0 `:394`。优先认后端已经算好的 `<prefix>_media_refs`（带 media_type）；
 * 退回到旧的 `<prefix>_image_ids` / `<prefix>_video_ids` 两个平铺数组，按 `mediaKey` 去重。
 * 两条分支都要走，少一条历史 run 的媒体就整批消失。
 */
export function mediaRefs(item: MediaLike | null | undefined, prefix = "retrieval"): MediaRef[] {
  const typed = item?.[`${prefix}_media_refs`];
  if (Array.isArray(typed)) {
    return typed
      .map((ref) => ({
        ...(ref as MediaRef),
        media_type: inferMediaType(ref?.media_id, ref?.media_type),
        media_id: String(ref?.media_id || ""),
      }))
      .filter((ref) => ref.media_id);
  }
  const imageIds = (item?.[`${prefix}_image_ids`] as unknown[] | undefined) || [];
  const videoIds = (item?.[`${prefix}_video_ids`] as unknown[] | undefined) || [];
  const refs: MediaRef[] = [
    ...imageIds.map((media_id) => ({
      media_type: inferMediaType(media_id),
      media_id: String(media_id),
    })),
    ...videoIds.map((media_id) => ({ media_type: "video" as const, media_id: String(media_id) })),
  ];
  return [...new Map(refs.map((ref) => [mediaKey(ref), ref])).values()];
}

/** 1.0 `:553`。按资产 id 取文件，取不到才用响应里带的 `media_url`。 */
export function imageUrl(image: MediaLike | null | undefined): string {
  const local = String(image?.media_url || "");
  if (local.startsWith(`${BASE}/albums/`)) return local;
  const assetId = image?.asset_id;
  return assetId
    ? `${BASE}/assets/${encodeURIComponent(String(assetId))}/file`
    : String(image?.media_url || "");
}

/**
 * 1.0 `:624`。按相册布局拼本地媒体路径（没有 `asset_id` 时的回退口径）。
 * 集合判据：视频 → `videos`，`faceid_*` 文件 → `faces`，其余 → `photos`；
 * 无扩展名的视频补 `.mp4`。
 */
export function albumLocalUrl(albumId: string, fileName: unknown, mediaType: unknown = ""): string {
  const album = albumId || "";
  const video = inferMediaType(fileName, mediaType) === "video";
  const collection = video ? "videos" : FACE_FILE.test(String(fileName || "")) ? "faces" : "photos";
  const mediaFile =
    video && !VIDEO_EXT.test(String(fileName || "")) ? `${fileName}.mp4` : fileName;
  return album && mediaFile
    ? `${BASE}/albums/${encodeURIComponent(album)}/${collection}/${encodeURIComponent(String(mediaFile))}`
    : "";
}

/** 1.0 `:631`。显式 `media_type === "video"` 优先，否则看 id / 文件名 / 地址。 */
export function isVideoMedia(image: MediaLike | null | undefined): boolean {
  if (image?.media_type === "video") return true;
  return [image?.media_id, image?.video_id, image?.image_id, image?.file_name, image?.media_url].some(
    (value) => {
      const text = String(value || "");
      const fileName = text.split(/[/?#]/).filter(Boolean).pop() || "";
      return /^video-\d+(?:\.mp4)?$/i.test(fileName) || /\.mp4(?:$|[?#])/i.test(text);
    },
  );
}

/**
 * 1.0 `:639`。视频元素加载元数据后暂停在证据帧。
 * `Number(undefined)` 是 NaN → `Number.isFinite` 为假 → 直接返回，与 1.0 一致。
 */
export function pauseVideoAtEvidenceFrame(
  media: MediaLike | null | undefined,
  event: { target: HTMLVideoElement | null } | null | undefined,
): void {
  const seconds = Number(media?.source_timestamp_sec);
  const player = event?.target;
  if (!player || !Number.isFinite(seconds) || seconds < 0) return;
  player.pause();
  player.currentTime = seconds;
}

/**
 * 1.0 `:646`。给还没有 `media_url` 的条目补一条本地地址；已有 `media_url` 的原样返回。
 * `albumId` 缺省时回落到「`media_id` 是 `相册/文件` 两段」里的相册名（1.0 就是这条）。
 */
export function decorateMedia<T extends MediaLike>(
  list: T[] | null | undefined,
  albumId: string,
): Array<T & { media_type?: MediaType; media_url?: string }> {
  return (list || []).map((img) => {
    if (img?.media_url) return img as T & { media_type?: MediaType; media_url?: string };
    const sourceId = String(img?.media_id || img?.image_id || img?.video_id || "");
    const parts = sourceId.split("/");
    const file = parts.length >= 2 ? parts[parts.length - 1] : String(img?.file_name || sourceId);
    const album = albumId || (parts.length === 2 ? parts[0] : "");
    const video = isVideoMedia(img);
    const collection = video ? "videos" : FACE_FILE.test(file) ? "faces" : "photos";
    const mediaFile = video && !/\.mp4$/i.test(file) ? `${file}.mp4` : file;
    const local =
      album && mediaFile
        ? `${BASE}/albums/${encodeURIComponent(album)}/${collection}/${encodeURIComponent(mediaFile)}`
        : "";
    return local
      ? { ...img, media_type: video ? ("video" as const) : ((img.media_type as MediaType) || "image"), media_url: local }
      : (img as T & { media_type?: MediaType; media_url?: string });
  });
}
