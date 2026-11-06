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

/** 只把观察时间与描述带出来；文件本身通过 Observation 里的 asset_id 打开。 */
function ObservationList({ observations }: { observations: readonly ObservationPayload[] }) {
  if (observations.length === 0) {
    return (
      <p className="asset-detail-note">
        后端没有按资产返回 Observation 的端点：<code>GET /assets/{"{id}"}</code> 只回
        AssetSummary，事件侧也只有 <code>GET /events/{"{id}"}</code>（没有按资产反查）。
        这里不伪造观察记录。
      </p>
    );
  }
  return (
    <ul className="asset-observations">
      {observations.map((observation) => (
        <li key={observation.id}>
          <div>
            <strong>{observation.caption || "原始观察证据"}</strong>
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

export interface AssetDetailProps {
  asset: AssetDetailResponse;
  onClose: () => void;
  /** 视频命中帧的秒数；非视频忽略。 */
  initialTimeSec?: number | null;
  /** 调用方已经有 Observation 时直接传入（实体详情 / 实体组详情）。 */
  observations?: readonly ObservationPayload[];
}

export function AssetDetail({
  asset,
  onClose,
  initialTimeSec = null,
  observations: providedObservations,
}: AssetDetailProps) {
  const observations = providedObservations ?? asset.observations ?? [];
  const [textContent, setTextContent] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [videoScenes, setVideoScenes] = useState<{ id: string; scenes: VideoScenePayload[]; error: string | null } | null>(null);
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
      <button type="button" className="close" onClick={onClose}>关闭</button>
      <div className="asset-detail-media">{media}</div>
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
      <dl>
        <dt>文件名</dt><dd>{asset.file_name}</dd>
        <dt>类型</dt><dd>{MEDIA_LABEL[asset.media_type] ?? asset.media_type}（{asset.mime_type ?? "未知 MIME"}）</dd>
        <dt>拍摄时间</dt><dd>{asset.captured_at ?? "未标注"}</dd>
        <dt>地点</dt>
        <dd>{asset.gps ? `${asset.gps.latitude.toFixed(6)}, ${asset.gps.longitude.toFixed(6)}` : "无 GPS"}</dd>
        {initialTimeSec !== null ? (
          <>
            <dt>命中时间</dt><dd>{initialTimeSec.toFixed(2)} 秒</dd>
          </>
        ) : null}
        <dt>处理状态</dt><dd>{asset.status}</dd>
        <dt>Observation</dt>
        <dd className="asset-detail-observations"><ObservationList observations={observations} /></dd>
        <dt>原始文件</dt>
        <dd><a href={assetFileUrl(asset.id)} target="_blank" rel="noreferrer">打开原文件</a></dd>
      </dl>
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
