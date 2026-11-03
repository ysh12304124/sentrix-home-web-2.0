/**
 * 资料库：分页、筛选与计数都由后端覆盖完整范围。
 *
 * 前端不再用当前页长度推算“当前范围资产”，也不在已取回的 200 条里筛选。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import {
  listAssetPage,
  type AssetSummary,
} from "../../shared/api/client";
import { newOperationId, reportClientEvent } from "../../shared/clientEvents";
import { AssetGrid } from "./AssetGrid";
import "../../styles/library.css";

type MediaFilter = "all" | "image" | "audio" | "text" | "video";
type SortKey = "captured" | "created";

const PAGE_SIZE = 60;

const FILTERS: readonly (readonly [MediaFilter, string])[] = [
  ["all", "全部"],
  ["image", "图片"],
  ["video", "视频"],
  ["audio", "音频"],
  ["text", "文本"],
];

export function LibraryPage() {
  const { scopeId, spaces } = useScope();
  const [assets, setAssets] = useState<readonly AssetSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("captured");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [reloadKey, setReloadKey] = useState(0);
  const requestSeq = useRef(0);
  const knownTotal = useRef<number | null>(null);

  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";

  useEffect(() => {
    void reportClientEvent({
      eventName: "library.view",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
    });
  }, [scopeId]);

  useEffect(() => {
    let cancelled = false;
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    setNotice("");
    listAssetPage(scopeId, {
      limit: PAGE_SIZE,
      mediaType: filter === "all" ? "" : filter,
      sort: sortKey,
      order,
    })
      .then((page) => {
        if (cancelled || seq !== requestSeq.current) return;
        if (knownTotal.current !== null && (page.counts.all ?? 0) > knownTotal.current) {
          setNotice("有新资料，刷新后可看到最新内容。");
        }
        knownTotal.current = page.counts.all ?? page.total;
        setAssets(page.items);
        setTotal(page.total);
        setCounts(page.counts);
      })
      .catch((cause: Error) => {
        if (!cancelled && seq === requestSeq.current) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled && seq === requestSeq.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, order, reloadKey, scopeId, sortKey]);

  const loadMore = useCallback(async () => {
    if (loadingMore || assets.length >= total) return;
    setLoadingMore(true);
    try {
      const page = await listAssetPage(scopeId, {
        offset: assets.length,
        limit: PAGE_SIZE,
        mediaType: filter === "all" ? "" : filter,
        sort: sortKey,
        order,
      });
      if (knownTotal.current !== null && (page.counts.all ?? 0) > knownTotal.current) {
        setNotice("有新资料，刷新后可看到最新内容。");
      }
      knownTotal.current = page.counts.all ?? knownTotal.current;
      setAssets((previous) => {
        const seen = new Set(previous.map((asset) => asset.id));
        return [...previous, ...page.items.filter((asset) => !seen.has(asset.id))];
      });
      setTotal(page.total);
      setCounts(page.counts);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }, [assets, filter, loadingMore, order, scopeId, sortKey, total]);

  const changeFilter = (next: MediaFilter) => {
    if (next === filter) return;
    setFilter(next);
    void reportClientEvent({
      eventName: "library.filter",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
    });
  };

  const changeSort = (value: string) => {
    const [nextKey, nextOrder] = value.split(":") as [SortKey, "asc" | "desc"];
    setSortKey(nextKey);
    setOrder(nextOrder);
    void reportClientEvent({
      eventName: "library.filter",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
    });
  };

  const count = (type: string) => counts[type] ?? 0;
  const emptyAll = count("all") === 0;

  return (
    <>
      <PageHeader
        kicker="家庭资料"
        title="所有资料"
        description={`当前范围：${scopeName}。可以按类型筛选、按拍摄或导入时间排序；这里的总数来自服务器上的全部资料。`}
      />

      <div className="library-summary">
        <div><strong>{count("all")}</strong><span>全部资料</span></div>
        <div><strong>{count("image")}</strong><span>图片</span></div>
        <div><strong>{count("video")}</strong><span>视频</span></div>
        <div><strong>{count("audio")}</strong><span>音频</span></div>
        <div className="muted-stat"><strong>{count("text")}</strong><span>文本</span></div>
      </div>

      <div className="filter-row">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={filter === key ? "filter-chip active" : "filter-chip"}
            aria-pressed={filter === key}
            onClick={() => changeFilter(key)}
          >
            {label}
          </button>
        ))}
        <span className="filter-spacer" />
        <label className="sort-select">
          排序
          <select
            value={`${sortKey}:${order}`}
            onChange={(event) => changeSort(event.target.value)}
          >
            <option value="captured:desc">按拍摄时间（最近）</option>
            <option value="captured:asc">按拍摄时间（最早）</option>
            <option value="created:desc">按导入时间（最近）</option>
            <option value="created:asc">按导入时间（最早）</option>
          </select>
        </label>
        <button
          type="button"
          className="sort-label"
          onClick={() => setReloadKey((value) => value + 1)}
        >
          刷新 ↻
        </button>
      </div>

      {notice !== "" ? (
        <p className="library-notice" role="status">
          {notice}
          <button type="button" className="text-button" onClick={() => setReloadKey((value) => value + 1)}>
            刷新
          </button>
        </p>
      ) : null}

      {error ? (
        <p className="error">
          资料读取失败：{error}
          <button type="button" className="text-button" onClick={() => setReloadKey((value) => value + 1)}>
            重试
          </button>
        </p>
      ) : null}

      {loading ? (
        <p className="library-loading">正在读取资料…</p>
      ) : assets.length === 0 ? (
        <EmptyState
          symbol="▦"
          title={emptyAll ? "还没有资料" : "没有匹配的资料"}
          description={
            emptyAll
              ? "添加照片或视频后，整理完成的资料会出现在这里。"
              : "调整类型筛选，或切换到其它相册范围。"
          }
        />
      ) : (
        <>
          <AssetGrid
            assets={assets}
            onOpen={(asset) =>
              void reportClientEvent({
                eventName: "library.detail_open",
                result: "ok",
                operationId: newOperationId(),
                scopeId,
                assetId: asset.id,
              })
            }
            onMediaError={(asset) =>
              void reportClientEvent({
                eventName: "library.media_error",
                result: "failed",
                errorCode: "media_load",
                operationId: newOperationId(),
                scopeId,
                assetId: asset.id,
              })
            }
          />
          <div className="library-footer">
            <span>
              已显示 {assets.length} / {total} 份
              {total > assets.length ? "，继续加载不会重复已显示的条目。" : "。"}
            </span>
            {assets.length < total ? (
              <button
                type="button"
                className="button ghost"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "正在加载…" : "加载更多"}
              </button>
            ) : null}
          </div>
        </>
      )}
    </>
  );
}
