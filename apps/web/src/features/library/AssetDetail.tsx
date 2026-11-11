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
  deleteAsset,
  getVideo,
  type AssetDetailResponse,
  type ObservationPayload,
  type VideoScenePayload,
} from "../../shared/api/client";
import "./asset-detail.css";

const MEDIA_LABEL: Readonly<Record<string, string>> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  text: "文本",
};

const STATUS_LABEL: Readonly<Record<string, string>> = {
  queued: "待处理",
  processing: "处理中",
  processed: "已处理",
  failed: "处理失败",
};

function displayTime(value: string | null | undefined): string {
  if (!value) return "未标注";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** 只把观察时间与描述带出来；文件本身通过 Observation 里的 asset_id 打开。 */
function ObservationList({ observations }: { observations: readonly ObservationPayload[] }) {
  if (observations.length === 0) {
    return <p className="asset-detail-note">当前详情没有可展示的观察记录。</p>;
  }
  return (
    <ul className="asset-observations">
      {observations.map((observation) => (
        <li key={observation.id}>
          <div>
            <strong>{observation.caption || "原始观察证据"}</strong>
            <small>
              {displayTime(observation.captured_at)} · {MEDIA_LABEL[observation.source_type] ?? observation.source_type}
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

export interface AssetDetailProps {
  asset: AssetDetailResponse;
  onClose: () => void;
  onDeleted?: (assetId: string, warnings: string[]) => void;
  /** 视频命中帧的秒数；非视频忽略。 */
  initialTimeSec?: number | null;
  /** 调用方已经有 Observation 时直接传入（实体详情 / 实体组详情）。 */
  observations?: readonly ObservationPayload[];
}

export function AssetDetail({
  asset,
  onClose,
  onDeleted,
  initialTimeSec = null,
  observations: providedObservations,
}: AssetDetailProps) {
  const observations = providedObservations ?? asset.observations ?? [];
  const [textContent, setTextContent] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [videoScenes, setVideoScenes] = useState<{ id: string; scenes: VideoScenePayload[]; error: string | null } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (asset.media_type !== "video") return;
    let cancelled = false;
    getVideo(asset.id)
      .then((response) => !cancelled && setVideoScenes({ id: asset.id, scenes: response.scenes, error: null }))
      .catch((cause: Error) => !cancelled && setVideoScenes({ id: asset.id, scenes: [], error: cause.message }));
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
      return <img src={assetInlineUrl(asset)} alt={asset.file_name} />;
    }
    if (asset.media_type === "video") {
      return (
        <video
          ref={videoRef}
          controls
          preload="metadata"
          playsInline
          src={assetInlineUrl(asset)}
          aria-label={asset.file_name}
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
      return <audio controls preload="metadata" src={assetInlineUrl(asset)} aria-label={asset.file_name} />;
    }
    if (asset.media_type === "text") {
      return textError !== null ? (
        <p className="error">{textError}</p>
      ) : (
        <pre className="asset-text">{textContent ?? "读取文本中…"}</pre>
      );
    }
    return <p className="asset-detail-note">{asset.file_name}</p>;
  }, [asset, initialTimeSec, textContent, textError]);

  return (
    <div className="detail asset-detail" role="dialog" aria-modal="true" aria-label="资产详情">
      <div className="asset-detail-panel">
        <header className="asset-detail-header">
          <div className="asset-detail-title">
            <span className="asset-detail-eyebrow">{MEDIA_LABEL[asset.media_type] ?? asset.media_type}详情</span>
            <h2 title={asset.file_name}>{asset.file_name}</h2>
          </div>
          <button type="button" className="asset-detail-close" aria-label="关闭资产详情" onClick={onClose}>关闭</button>
        </header>
        <div className="asset-detail-body">
          <div className="asset-detail-media">{media}</div>
          <section className="asset-detail-info" aria-label="文件信息">
            <h3>文件信息</h3>
            <dl>
              <div><dt>文件类型</dt><dd>{asset.mime_type ?? "未知"}</dd></div>
              <div><dt>拍摄时间</dt><dd title={asset.captured_at ?? undefined}>{displayTime(asset.captured_at)}</dd></div>
              <div><dt>地点坐标</dt><dd>{asset.gps ? `${asset.gps.latitude.toFixed(6)}, ${asset.gps.longitude.toFixed(6)}` : "无 GPS"}</dd></div>
              {initialTimeSec !== null ? <div><dt>命中时间</dt><dd>{initialTimeSec.toFixed(2)} 秒</dd></div> : null}
              <div><dt>处理状态</dt><dd>{STATUS_LABEL[asset.status] ?? asset.status}</dd></div>
            </dl>
            <a className="asset-detail-file-link" href={assetFileUrl(asset.id)} target="_blank" rel="noreferrer">打开原文件 ↗</a>
          </section>
          {asset.media_type === "video" ? (
            <VideoSpeechTranscript
              scenes={videoScenes?.id === asset.id ? videoScenes.scenes : null}
              error={videoScenes?.id === asset.id ? videoScenes.error : null}
              onSeek={(seconds) => {
                if (videoRef.current) {
                  videoRef.current.currentTime = seconds;
                  void videoRef.current.play();
                }
              }}
            />
          ) : null}
          <section className="asset-detail-evidence" aria-label="观察记录">
            <h3>观察记录</h3>
            <ObservationList observations={observations} />
          </section>
        </div>
        {onDeleted && !asset.parent_asset_id ? (
          <footer className="asset-detail-footer">
            {deleteError ? <p role="alert" className="error">删除失败：{deleteError}</p> : null}
            <button type="button" className="asset-detail-delete" disabled={deleting} onClick={() => {
              if (!window.confirm(`确定删除「${asset.file_name}」及其关联记忆和索引？此操作无法撤销。`)) return;
              setDeleting(true);
              setDeleteError(null);
              void deleteAsset(asset.id)
                .then((result) => onDeleted(asset.id, result.warnings))
                .catch((cause: Error) => setDeleteError(cause.message))
                .finally(() => setDeleting(false));
            }}>{deleting ? "正在删除…" : "删除媒体及关联记忆"}</button>
          </footer>
        ) : null}
      </div>
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
  scenes: VideoScenePayload[] | null;
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
