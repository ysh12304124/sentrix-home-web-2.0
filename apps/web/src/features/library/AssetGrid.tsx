/**
 * 资产网格。结构照 1.0 的 `assetCard`（`src/app.js` 的 `libraryView` 里）：
 * `.asset-card` 里套 `.asset-thumb` + `.asset-info`，缩略图左下角一个媒体类型徽标。
 *
 * 缩略图走 `assetDisplayUrl`（预览），**详情一律走 `/file` 原图**——那是产品硬约束：
 * 查询必须返回可打开的原始 Asset。
 */

import { useRef, useState } from "react";

import {
  assetPosterUrl,
  assetThumbnailUrl,
  getAsset,
  type AssetDetailResponse,
  type AssetSummary,
} from "../../shared/api/client";
import { AssetDetail, isDeletableAsset } from "./AssetDetail";

const TYPE_MARK: Readonly<Record<string, string>> = { image: "▦", video: "▶", audio: "♪", text: "¶" };

function mediaLabel(mediaType: string): string {
  return { image: "图片", video: "视频", audio: "音频", text: "文本" }[mediaType] ?? "资料";
}

/** 视频未播放时也必须看得见画面：后端 poster 路由优先复用关键帧，没有就抽首帧。 */
function AssetThumb({
  asset,
  onMediaError,
}: {
  asset: AssetSummary;
  onMediaError?: (asset: AssetSummary) => void;
}) {
  const [posterFailed, setPosterFailed] = useState(false);
  if (asset.media_type === "image" || (asset.media_type === "video" && !posterFailed)) {
    return (
      <img
        src={asset.media_type === "video" ? assetPosterUrl(asset.id) : assetThumbnailUrl(asset.id)}
        alt={asset.media_type === "video" ? "视频封面" : "资料图片"}
        loading="lazy"
        onError={() => {
          if (asset.media_type === "video") setPosterFailed(true);
          onMediaError?.(asset);
        }}
      />
    );
  }
  return <span className="asset-type-mark" aria-hidden="true">{TYPE_MARK[asset.media_type] ?? "▦"}</span>;
}

export function AssetGrid({
  assets,
  onOpen,
  deletion,
  onMediaError,
}: {
  assets: readonly AssetSummary[];
  deletion?: { onAccepted: (assetId: string) => void };
  onOpen?: (asset: AssetSummary) => void;
  onMediaError?: (asset: AssetSummary) => void;
}) {
  const [selected, setSelected] = useState<{ asset: AssetDetailResponse; confirmDeletion: boolean } | null>(null);
  const openRequest = useRef(0);

  /**
   * 详情优先走 `GET /assets/{id}`：这样后端哪天在响应里补上 `observations`，
   * 列表这边不用改就能显示。请求失败时退回列表已有的摘要，点开仍然可用。
   */
  async function open(asset: AssetSummary) {
    const request = ++openRequest.current;
    onOpen?.(asset);
    const detail = await getAsset(asset.id).catch(() => asset);
    if (request === openRequest.current) {
      setSelected({ asset: detail, confirmDeletion: false });
    }
  }

  function openDeletion(asset: AssetSummary) {
    ++openRequest.current;
    setSelected({ asset, confirmDeletion: true });
  }

  return (
    <>
      <section className="asset-grid library-grid">
        {assets.map((asset) => (
          <div key={asset.id} className="library-asset-tile">
            <button type="button" className="asset-card" onClick={() => void open(asset)}>
              <div className="asset-thumb">
                <AssetThumb asset={asset} onMediaError={onMediaError} />
                <b>{mediaLabel(asset.media_type)}</b>
              </div>
              <div className="asset-info">
                <small className="asset-time">
                  {asset.captured_at ? asset.captured_at.slice(0, 10) : "时间未记录"}
                </small>
                <span className="asset-place">
                  {asset.place !== undefined && asset.place !== "" ? asset.place : "地点未记录"}
                </span>
              </div>
            </button>
            {deletion && asset.scope_id && isDeletableAsset(asset) && (
              <button
                type="button"
                className="library-asset-delete"
                aria-label={`删除${mediaLabel(asset.media_type)} ${asset.file_name}`}
                onClick={() => openDeletion(asset)}
              >
                删除
              </button>
            )}
          </div>
        ))}
      </section>
      {selected ? (
        <AssetDetail
          key={selected.asset.id}
          asset={selected.asset}
          confirmDeletionOnOpen={selected.confirmDeletion}
          onClose={() => setSelected(null)}
          deletion={deletion && selected.asset.scope_id ? {
            scopeId: selected.asset.scope_id,
            onAccepted: (id) => { setSelected(null); deletion.onAccepted(id); },
          } : undefined}
        />
      ) : null}
    </>
  );
}
