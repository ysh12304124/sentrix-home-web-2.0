/**
 * 检索结果。
 *
 * 三条与 1.0 对齐的行为：
 *
 * 1. **结果可点开**：图片结果本身是按钮，点开复用资料库的 `AssetDetail`（不复制一份）；
 * 2. **视频帧定位**：命中派生关键帧时，用 `source_timestamp_sec` 在源视频上
 *    `currentTime` 定位（1.0 `mediaResults` 的 `data-evidence-timestamp` 同一条读法）；
 * 3. **降级显式展示**：后端在降级，用户有权知道结果是怎么来的。
 *
 * `GET /search` 只返回 `AssetSummary`，**没有事件 id / 活动描述**。详情里的「时间、地点」
 * 来自资产自己的 `captured_at` 与 GPS；活动描述没有数据来源，所以不编。
 */

import { useState } from "react";

import {
  assetDisplayUrl,
  assetPreviewUrl,
  getAsset,
  type AssetDetailResponse,
  type SearchHit,
  type SearchResponse,
} from "../../shared/api/client";
import { AssetDetail } from "../library/AssetDetail";
import "./search-results.css";

const MEDIA_MARK: Readonly<Record<string, string>> = { audio: "♪", text: "¶" };

/**
 * 展示真实相似度，而不是 ``score``。
 *
 * ``score`` 是加权 RRF 的名次推导分：每个通道的第一名都得到同一个数
 * （2.5 × 1/(60+1)），不含相似度信息——显示它会让用户看到「搜什么都一样」。
 * 真实相似度在 ``raw_scores`` 里，按通道存各通道自报的原始分。
 *
 * 单通道只显示数字；多通道带上通道名，因为各通道量纲不同（余弦 ∈ [-1,1]，词法分无
 * 上界），不加名会被当成同一把尺子。通道名排序后拼接，保证同一份数据渲染稳定。
 */
function formatSimilarity(hit: SearchHit): string {
  const channels = Object.entries(hit.raw_scores).sort(([a], [b]) => a.localeCompare(b));
  if (channels.length === 0) return "—";
  if (channels.length === 1) return channels[0][1].toFixed(4);
  return channels.map(([channel, value]) => `${channel}: ${value.toFixed(4)}`).join(" · ");
}

function formatCapturedAt(value: string | null): string {
  return value === null ? "未标注时间" : value.replace("T", " ").slice(0, 19);
}

/** 命中视频帧：命中资产是派生图片，播放的是它的源视频。 */
function frameSource(hit: SearchHit): { videoId: string; seconds: number } | null {
  const { parent_asset_id, source_timestamp_sec, media_type } = hit.asset;
  if (media_type !== "image" || parent_asset_id == null || source_timestamp_sec == null) {
    return null;
  }
  return { videoId: parent_asset_id, seconds: source_timestamp_sec };
}

/** 视频结果：`/preview` 优先交付浏览器可放的转码件。 */
function VideoResult({
  assetId,
  startSec,
}: {
  assetId: string;
  startSec: number | null;
}) {
  return (
    <video
      controls
      preload="metadata"
      playsInline
      src={assetPreviewUrl(assetId)}
      aria-label="检索命中的视频"
      onLoadedMetadata={(event) => {
        if (startSec !== null && Number.isFinite(startSec)) {
          event.currentTarget.currentTime = Math.max(0, startSec);
        }
      }}
    />
  );
}

export function SearchResults({
  response,
  onClose,
}: {
  response: SearchResponse;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<{
    asset: AssetDetailResponse;
    startSec: number | null;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function openHit(hit: SearchHit, startSec: number | null) {
    // 普通结果直接用检索已经带回的 AssetSummary，不再多请求一次。
    if (startSec === null || hit.asset.parent_asset_id == null) {
      setSelected({ asset: hit.asset, startSec });
      return;
    }
    // 视频帧命中：父视频的完整摘要要走 `/assets/{id}`——
    // 关键帧自己的文件名/类型不是用户想看的那份资产。
    setBusyId(hit.asset.id);
    setError(null);
    try {
      const parent = await getAsset(hit.asset.parent_asset_id);
      setSelected({ asset: parent, startSec });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="results">
      <header className="results-header">
        <h2>
          「{response.query}」的结果（{response.items.length}）
        </h2>
        <button type="button" onClick={onClose}>
          返回全部照片
        </button>
      </header>
      {response.degraded ? (
        <p className="degraded">
          检索正在降级（{response.backend}）：{response.degraded_reason}
        </p>
      ) : null}
      {error !== null ? <p className="error">{error}</p> : null}
      {response.items.length === 0 ? (
        <p className="empty">
          {response.authoritative_vectors === 0 ? "还没有已索引的照片。" : "没有匹配的照片。"}
        </p>
      ) : (
        <>
          <p className="search-result-note">
            结果只携带资产本身的时间与 GPS；活动描述需要事件上下文，<code>/search</code> 不返回事件 id。
          </p>
          <ul className="grid search-results-grid">
            {response.items.map((item) => {
              const frame = frameSource(item);
              const videoId = frame?.videoId ?? (item.asset.media_type === "video" ? item.asset.id : null);
              const startSec = frame?.seconds ?? item.asset.source_timestamp_sec ?? null;
              const info = (
                <>
                  <span>{item.asset.place ?? "地点未记录"}</span>
                  <small>{formatCapturedAt(item.asset.captured_at)}</small>
                  <small>
                    {item.asset.gps
                      ? `${item.asset.gps.latitude.toFixed(4)}, ${item.asset.gps.longitude.toFixed(4)}`
                      : "无 GPS"}
                  </small>
                  {frame !== null ? <small>命中视频帧 · {frame.seconds.toFixed(2)}s</small> : null}
                  <small>{formatSimilarity(item)}</small>
                </>
              );
              const open = () => void openHit(item, startSec);
              return (
                <li key={item.asset.id}>
                  {videoId !== null ? (
                    <>
                      {/* 视频控件不能套在 button 里：`controls` 自己就是交互元素。 */}
                      <VideoResult assetId={videoId} startSec={startSec} />
                      <button
                        type="button"
                        className="search-result-card"
                        disabled={busyId === item.asset.id}
                        onClick={open}
                        title="查看详情"
                      >
                        {info}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="search-result-card"
                      disabled={busyId === item.asset.id}
                      onClick={open}
                      title="查看详情"
                    >
                      {item.asset.media_type === "image" ? (
                        <img src={assetDisplayUrl(item.asset)} alt="搜索结果照片" loading="lazy" />
                      ) : (
                        <span className="search-result-mark" aria-hidden="true">
                          {MEDIA_MARK[item.asset.media_type] ?? "▦"}
                        </span>
                      )}
                      {info}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      {selected !== null ? (
        <AssetDetail
          asset={selected.asset}
          initialTimeSec={selected.startSec}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </section>
  );
}
