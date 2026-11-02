/**
 * 资料库。对应 1.0 `src/app.js:683` 的 `libraryView()`（pageHeader 在 `:687`）。
 *
 * 版式照 1.0：相册摘要行 → 四个统计 → 筛选行（全部/图片/音频/文本/视频 + 排序）→ 网格。
 *
 * **Observation 面板没做**：1.0 的资产详情能展开「处理状态、Observation、模型原始输出」，
 * 而 2.0 没有 `/observations` 端点。详情里据实说明，不放空壳。
 */

import { useEffect, useMemo, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { listAssets, type AssetSummary } from "../../shared/api/client";
import { AssetGrid } from "./AssetGrid";
import { countByScope } from "./scopeCounts";
import "../../styles/library.css";

type MediaFilter = "all" | "image" | "audio" | "text" | "video";
type SortOrder = "newest" | "oldest";

const FILTERS: readonly (readonly [MediaFilter, string])[] = [
  ["all", "全部"],
  ["image", "图片"],
  ["audio", "音频"],
  ["text", "文本"],
  ["video", "视频"],
];

export function LibraryPage() {
  const { scopeId, spaces } = useScope();
  const [assets, setAssets] = useState<readonly AssetSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [order, setOrder] = useState<SortOrder>("newest");

  useEffect(() => {
    let cancelled = false;
    listAssets(scopeId)
      .then((items) => {
        if (cancelled) return;
        setAssets(items);
        setError(null);
      })
      .catch((cause: Error) => !cancelled && setError(cause.message));
    return () => {
      cancelled = true;
    };
  }, [scopeId]);

  const visible = useMemo(() => {
    const filtered = filter === "all" ? assets : assets.filter((a) => a.media_type === filter);
    // 1.0 的 `toggle-sort` 语义：按导入时间，默认最近在前。没有拍摄时间的排在后面。
    return [...filtered].sort((left, right) => {
      const a = left.captured_at ?? "";
      const b = right.captured_at ?? "";
      return order === "newest" ? b.localeCompare(a) : a.localeCompare(b);
    });
  }, [assets, filter, order]);

  const countOf = (type: MediaFilter) =>
    type === "all" ? assets.length : assets.filter((a) => a.media_type === type).length;
  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";

  // 后端目前不给 `AssetSummary.scope_id`，所以这里通常是空对象；只有它真的返回时
  // 才显示按相册的汇总。**字段缺失不等于「0 个相册」**，两者要分开呈现。
  const scopeCounts = useMemo(() => countByScope(assets), [assets]);
  const scopedEntries = Object.entries(scopeCounts);

  return (
    <>
      <PageHeader
        kicker="家庭资料 / 全部内容"
        title="所有资料，都有回到记忆的路径。"
        description={`当前范围：${scopeName}。这里展示真实导入的原始 Asset。点击任意条目可查看文件与处理状态。`}
      />

      <div className="album-summary-row">
        <strong>相册内容</strong>
        <span className="album-badge">{scopeName}</span>
        {scopedEntries.length > 0 ? (
          scopedEntries.map(([id, count]) => (
            <span key={id}>
              {spaces.find((space) => space.id === id)?.name ?? id} · {count}
            </span>
          ))
        ) : (
          <span className="scope-count-note">当前范围内没有分相册的条目</span>
        )}
      </div>

      <div className="library-summary">
        <div><strong>{assets.length}</strong><span>当前范围资产</span></div>
        <div><strong>{countOf("image")}</strong><span>图片</span></div>
        <div><strong>{countOf("audio") + countOf("text")}</strong><span>音频 / 文本</span></div>
        <div className="muted-stat"><strong>{countOf("video")}</strong><span>视频与场景</span></div>
      </div>

      <div className="filter-row">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={filter === key ? "filter-chip active" : "filter-chip"}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
        <span className="filter-spacer" />
        <button
          type="button"
          className="sort-label"
          onClick={() => setOrder(order === "newest" ? "oldest" : "newest")}
        >
          按{order === "newest" ? "最近" : "最早"}导入 ↕
        </button>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {visible.length === 0 ? (
        <EmptyState
          symbol="▦"
          title={assets.length === 0 ? "还没有资料" : "没有匹配的资料"}
          description={
            assets.length === 0
              ? "导入一份原始文件后，它会出现在这里。"
              : "调整筛选条件，或切换到其它相册范围。"
          }
        />
      ) : (
        <AssetGrid assets={visible} />
      )}
    </>
  );
}
