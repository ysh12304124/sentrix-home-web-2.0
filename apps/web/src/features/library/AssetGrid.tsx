/**
 * 资产网格。结构照 1.0 的 `assetCard`（`src/app.js` 的 `libraryView` 里）：
 * `.asset-card` 里套 `.asset-thumb` + `.asset-info`，缩略图左下角一个媒体类型徽标。
 *
 * 缩略图走 `assetDisplayUrl`（预览），**详情一律走 `/file` 原图**——那是产品硬约束：
 * 查询必须返回可打开的原始 Asset。
 */

import { useState } from "react";

import {
  assetDisplayUrl,
  getAsset,
  type AssetDetailResponse,
  type AssetSummary,
} from "../../shared/api/client";
import { AssetDetail } from "./AssetDetail";

const TYPE_MARK: Readonly<Record<string, string>> = { image: "▦", video: "▶", audio: "♪", text: "¶" };

function mediaLabel(mediaType: string): string {
  return { image: "图片", video: "视频", audio: "音频", text: "文本" }[mediaType] ?? mediaType;
}

export function AssetGrid({ assets, onDeleted }: { assets: readonly AssetSummary[]; onDeleted?: (id: string, warnings: string[]) => void }) {
  const [selected, setSelected] = useState<AssetDetailResponse | null>(null);

  /**
   * 详情优先走 `GET /assets/{id}`：这样后端哪天在响应里补上 `observations`，
   * 列表这边不用改就能显示。请求失败时退回列表已有的摘要，点开仍然可用。
   */
  async function open(asset: AssetSummary) {
    try {
      setSelected(await getAsset(asset.id));
    } catch {
      setSelected(asset);
    }
  }

  return (
    <>
      <section className="asset-grid library-grid">
        {assets.map((asset) => (
          <button key={asset.id} type="button" className="asset-card" onClick={() => void open(asset)}>
            <div className="asset-thumb">
              {asset.media_type === "image" ? (
                <img src={assetDisplayUrl(asset)} alt={asset.file_name} loading="lazy" />
              ) : (
                <span className="asset-type-mark" aria-hidden="true">{TYPE_MARK[asset.media_type] ?? "▦"}</span>
              )}
              <b>{mediaLabel(asset.media_type)}</b>
            </div>
            <div className="asset-info">
              <div className="asset-info-head">
                <strong>{asset.file_name}</strong>
              </div>
              <small>{asset.captured_at ? asset.captured_at.slice(0, 10) : "未标注拍摄时间"}</small>
              <span>{asset.status}</span>
            </div>
          </button>
        ))}
      </section>
      {selected ? <AssetDetail asset={selected} onClose={() => setSelected(null)} onDeleted={(id, warnings) => {
        setSelected(null);
        onDeleted?.(id, warnings);
      }} /> : null}
    </>
  );
}
