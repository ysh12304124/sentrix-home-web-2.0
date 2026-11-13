/**
 * 资产详情。
 *
 * 1.0（`src/app.js:1016-1046`）按媒体类型分别交付：图片 `<img>`、音频 `<audio>`、
 * 视频 `<video>`、文本给文件名。2.0 照搬这条分流，并补两件 1.0 也有的事：
 *
 * 1. **视频定位**：检索命中的是派生关键帧时，用 `source_timestamp_sec` 在源视频上
 *    `currentTime` 定位（1.0 的 `pauseVideoAtEvidenceFrame` 同一条读法）；
 * 2. **Observation 列表**：能拿到就展示，拿不到就如实说明——**不伪造空列表**。
 *
 * 交付 URL 统一走 `/assets/{id}/preview`（视频/音频会落到 `deliver_media`，图片是
 * 服务端转码的 JPEG）。原片可能 HEVC/HEIC，浏览器放不了，详情里不再直接塞 `/file`。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  assetFileUrl,
  assetInlineUrl,
  assetPosterUrl,
  assetThumbnailUrl,
  getVideo,
  deleteLibraryAsset,
  type AssetDetailResponse,
  type ObservationPayload,
  type VideoDetailResponse,
} from "../../shared/api/client";
import "./asset-detail.css";

const MEDIA_LABEL: Readonly<Record<string, string>> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  text: "文本",
};

const ASSET_STATUS_LABEL: Readonly<Record<string, string>> = {
  processed: "已整理",
  processing: "正在整理",
  queued: "等待整理",
  failed: "整理失败",
};

/** 只把整理时间与描述带出来；没有内容时整段省略，不向用户解释内部端点缺口。 */
function ObservationList({ observations }: { observations: readonly ObservationPayload[] }) {
  if (observations.length === 0) return null;
  return (
    <ul className="asset-observations">
      {observations.map((observation) => (
        <li key={observation.id}>
          <div>
            <strong>{observation.caption || "整理记录"}</strong>
            <small>
              {observation.captured_at ?? "未标注时间"} · {observation.source_type}
            </small>
          </div>
          <a
            href={assetFileUrl(observation.asset_id)}
            target="_blank"
            rel="noreferrer"
            className="text-link"
          >
            打开证据
          </a>
        </li>
      ))}
    </ul>
  );
}

export function isDeletableAsset(asset: AssetDetailResponse): boolean {
  return (asset.media_type === "image" || asset.media_type === "video")
    && asset.parent_asset_id == null && asset.derived_kind == null;
}

export interface AssetDetailProps {
  asset: AssetDetailResponse;
  onClose: () => void;
  deletion?: { scopeId: string; onAccepted: (assetId: string) => void };
  confirmDeletionOnOpen?: boolean;
  /** 视频命中帧的秒数；非视频忽略。 */
  initialTimeSec?: number | null;
  /** 调用方已经有 Observation 时直接传入（实体详情 / 实体组详情）。 */
  observations?: readonly ObservationPayload[];
}

export function AssetDetail({
  asset,
  onClose,
  deletion,
  confirmDeletionOnOpen = false,
  initialTimeSec = null,
  observations: providedObservations,
}: AssetDetailProps) {
  const observations = providedObservations ?? asset.observations ?? [];
  const [textContent, setTextContent] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [videoDetail, setVideoDetail] = useState<{
    id: string;
    response: VideoDetailResponse | null;
    error: string | null;
  } | null>(null);
  const currentVideo = videoDetail?.id === asset.id ? videoDetail : null;
  const [confirming, setConfirming] = useState(confirmDeletionOnOpen);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const canDelete = deletion && isDeletableAsset(asset);
  const mediaLabel = MEDIA_LABEL[asset.media_type];
  async function confirmDelete() {
    if (!deletion || !canDelete || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteLibraryAsset(asset.id, deletion.scopeId);
      deletion.onAccepted(asset.id);
    } catch {
      setDeleteError("未能确认删除请求。请重试，重复请求不会创建额外任务。");
      setDeleting(false);
    }
  }

  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (asset.media_type !== "video") return;
    let cancelled = false;
    getVideo(asset.id)
      .then((response) => !cancelled && setVideoDetail({ id: asset.id, response, error: null }))
      .catch((cause: Error) => !cancelled && setVideoDetail({ id: asset.id, response: null, error: cause.message }));
    return () => { cancelled = true; };
  }, [asset.id, asset.media_type]);

  // 文本资产没有 `<img>`/`<video>` 可看，1.0 只显示文件名；这里把 `/file` 的内容读出来，
  // 仍然走 `/assets/{id}/file` 这条既有交付路径，不新造端点。
  useEffect(() => {
    if (asset.media_type !== "text") {
      // 非文本资产不读内容；状态只在文本分支使用，不在 effect 里同步清空。
      return;
    }
    let cancelled = false;
    fetch(assetFileUrl(asset.id))
      .then(async (response) => {
        if (!response.ok) throw new Error(`读取文本失败：${response.status}`);
        return response.text();
      })
      .then((text) => !cancelled && setTextContent(text))
      .catch((cause: Error) => !cancelled && setTextError(cause.message));
    return () => {
      cancelled = true;
    };
  }, [asset.id, asset.media_type]);

  const media = useMemo(() => {
    if (asset.media_type === "image") {
      return <img src={assetInlineUrl(asset)} alt="资料图片" />;
    }
    if (asset.media_type === "video") {
      return (
        <video
          ref={videoRef}
          controls
          preload="metadata"
          playsInline
          poster={assetPosterUrl(asset.id)}
          src={assetInlineUrl(asset)}
          aria-label="视频"
          onLoadedMetadata={(event) => {
            if (initialTimeSec !== null && Number.isFinite(initialTimeSec)) {
              // 命中视频帧时直接停在那一秒，用户不必自己拖动。
              event.currentTarget.currentTime = Math.max(0, initialTimeSec);
            }
          }}
        />
      );
    }
    if (asset.media_type === "audio") {
      return <audio controls preload="metadata" src={assetInlineUrl(asset)} aria-label="音频" />;
    }
    if (asset.media_type === "text") {
      return textError !== null ? (
        <p className="error">{textError}</p>
      ) : (
        <pre className="asset-text">{textContent ?? "读取文本中…"}</pre>
      );
    }
    return <p className="asset-detail-note">这份资料暂时不能直接预览。</p>;
  }, [asset, initialTimeSec, textContent, textError]);

  return (
    <div className="detail asset-detail" role="dialog" aria-modal="true" aria-label="资料详情">
      <button type="button" className="close" aria-label="关闭资料详情" onClick={onClose} disabled={deleting}>
        关闭
      </button>
      {canDelete && confirming && (
        <section className="asset-delete" aria-label="确认删除">
          <p>确定删除这{asset.media_type === "video" ? "段视频及其关键帧" : "张图片"}吗？资料会立即从相册隐藏，删除后无法撤销。</p>
          <button type="button" autoFocus disabled={deleting} onClick={() => {
            if (confirmDeletionOnOpen) onClose();
            else setConfirming(false);
          }}>取消</button>
          <button type="button" className="asset-delete-danger" disabled={deleting} onClick={() => void confirmDelete()}>
            {deleting ? "正在提交…" : "确认删除"}
          </button>
          {deleteError && <p role="alert">{deleteError}</p>}
        </section>
      )}
      <div className="asset-detail-media">{media}</div>
      {asset.media_type === "video" && Boolean(currentVideo?.response?.keyframe_assets?.length) && (
        <section aria-label="视频关键帧">
          <h3>关键帧</h3>
          <ul className="asset-keyframes">
            {currentVideo?.response?.keyframe_assets?.map((frame, index) => {
              const seconds = frame.source_timestamp_sec;
              const label = seconds === null ? `关键帧 ${index + 1}` : formatVideoTime(seconds);
              const thumbnail = <img loading="lazy" src={assetThumbnailUrl(frame.id)} alt={label} />;
              return (
                <li key={frame.id}>
                  {seconds === null ? thumbnail : (
                    <button type="button" onClick={() => {
                      if (videoRef.current) videoRef.current.currentTime = Math.max(0, seconds);
                    }}>
                      {thumbnail}
                      <span aria-hidden="true">{label}</span>
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {asset.media_type === "video" ? (
        <VideoSpeechTranscript
          scenes={currentVideo?.response?.scenes ?? null}
          error={currentVideo?.error ?? null}
          onSeek={(seconds) => {
            if (videoRef.current) {
              videoRef.current.currentTime = seconds;
              void videoRef.current.play();
            }
          }}
        />
      ) : null}
      <dl>
        <dt>类型</dt><dd>{MEDIA_LABEL[asset.media_type] ?? "资料"}</dd>
        <dt>拍摄时间</dt><dd>{asset.captured_at ?? "未标注"}</dd>
        <dt>地点</dt>
        <dd>{asset.place !== undefined && asset.place !== "" ? asset.place : asset.gps ? `${asset.gps.latitude.toFixed(4)}, ${asset.gps.longitude.toFixed(4)}` : "地点未记录"}</dd>
        {initialTimeSec !== null ? (
          <>
            <dt>命中时间</dt><dd>{initialTimeSec.toFixed(2)} 秒</dd>
          </>
        ) : null}
        <dt>整理状态</dt><dd>{ASSET_STATUS_LABEL[asset.status] ?? "状态更新中"}</dd>
        {observations.length > 0 ? (
          <>
            <dt>整理记录</dt>
            <dd className="asset-detail-observations"><ObservationList observations={observations} /></dd>
          </>
        ) : null}
        <dt>原始文件</dt>
        <dd><a href={assetFileUrl(asset.id)} target="_blank" rel="noreferrer">打开原文件</a></dd>
      </dl>
      {canDelete && !confirming && (
        <section className="asset-delete" aria-label="删除资料">
          <button type="button" className="asset-delete-danger" onClick={() => {
            setDeleteError(null);
            setConfirming(true);
          }}>
            删除{mediaLabel}
          </button>
        </section>
      )}
    </div>
  );
}

function formatVideoTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function VideoSpeechTranscript({
  scenes,
  error,
  onSeek,
}: {
  scenes: VideoDetailResponse["scenes"] | null;
  error: string | null;
  onSeek: (seconds: number) => void;
}) {
  const segments = (scenes ?? [])
    .flatMap((scene) => scene.speech_segments.map((segment) => ({
      ...segment,
      sceneTitle: scene.title,
      alignedKeyframes: (scene.keyframe_timestamps_sec ?? []).filter(
        (timestamp) => timestamp >= segment.start_sec && timestamp <= segment.end_sec,
      ),
    })))
    .sort((a, b) => a.start_sec - b.start_sec);
  return (
    <section className="asset-video-speech" aria-label="视频语音转写">
      <h3>语音转写与关键帧时间对齐</h3>
      {error ? <p className="error">语音信息读取失败：{error}</p> : scenes === null ? (
        <p className="asset-detail-note">正在读取语音片段…</p>
      ) : segments.length === 0 ? (
        <p className="asset-detail-note">这个视频没有可展示的语音转写。</p>
      ) : (
        <ol>
          {segments.map((segment, index) => (
            <li key={`${segment.start_sec}-${index}`}>
              <button type="button" onClick={() => onSeek(segment.start_sec)}>
                <time>{formatVideoTime(segment.start_sec)}–{formatVideoTime(segment.end_sec)}</time>
                <span>{segment.text}</span>
                <small>
                  {segment.sceneTitle}
                  {segment.alignedKeyframes.map((timestamp) => (
                    <span key={timestamp}> · 关键帧 {formatVideoTime(timestamp)}</span>
                  ))}
                </small>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
