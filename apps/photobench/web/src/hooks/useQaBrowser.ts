/**
 * 「QA 数据集浏览」视图的数据层。逐条移植 1.0 `App.vue`（只读，唯一权威）：
 *
 *   - 状态：`qaBrowserAlbum` `:269`、`qaBrowserSet` `:270`、`qaBrowserItems` `:271`、
 *     `qaBrowserSearch` `:272`、`qaBrowserTag` `:273`、`qaBrowserLoading` `:274`、
 *     `qaBrowserError` `:275`、`qaBrowserMediaResolution` `:276`
 *   - `qaBrowserOptions`       `:2768`
 *   - `qaBrowserTags`          `:2769`
 *   - `visibleQaBrowserItems`  `:2770-2778`
 *   - `loadQaBrowser`          `:2779-2788`
 *
 * 与 1.0 的两点出入（都因 React 没有全局 reactive，语义等价）：
 *
 *   1. `qaBrowserOptions` 依赖的 `manifests`、`loadQaBrowser` 依赖的 `sentrixUrl`
 *      （1.0 `:231` 的配置项）在 1.0 都是全局 ref；这里由调用方传入。
 *   2. 卸载时 `aliveRef` 置 false，迟到的响应不再 `setState`。**注意**现有
 *      `api/client.ts` 在 `fetch` 前无条件覆盖 `signal`，无法从外部塞 AbortController，
 *      本切片也不得改 client——所以用「丢弃迟到响应」达成等价的清理。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { taxonomyLabel } from "../lib/qaTaxonomy.ts";
import { api } from "../api/client.ts";
import type { ManifestSummary, MediaResolution, QaDatasetResponse, QaRow } from "../api/types.ts";

export interface UseQaBrowserOptions {
  /** 1.0 `sentrixUrl`（`:231`）：查询参数 `sentrix_url`，构造时会 `trim()`。 */
  sentrixUrl?: string;
  /** 1.0 `manifests`：`qaBrowserOptions` 从中按 `album_id` 找 `qa_sets`。 */
  manifests?: ManifestSummary[];
}

export function useQaBrowser({ sentrixUrl = "", manifests = [] }: UseQaBrowserOptions = {}) {
  // 初始值从当前 manifests 里取：153 有 album3，就先选 album3；只有 album3-14 的
  // 机器直接选 album3-14，避免先发一个必然 404 的请求。manifests 还没到时留空，
  // 由下面的同步 effect 补。
  const initialManifest =
    manifests.find((manifest) => manifest.album_id === "album3") || manifests[0] || null;
  const [qaBrowserAlbum, setQaBrowserAlbum] = useState(initialManifest?.album_id || "");
  const [qaBrowserSet, setQaBrowserSet] = useState(initialManifest?.qa_sets?.[0] || "");
  const [qaBrowserItems, setQaBrowserItems] = useState<QaRow[]>([]);
  const [qaBrowserSearch, setQaBrowserSearch] = useState("");
  const [qaBrowserTag, setQaBrowserTag] = useState("");
  const [qaTask, setQaTask] = useState("");
  const [qaSubtype, setQaSubtype] = useState("");
  const [qaAction, setQaAction] = useState("");
  const [qaBrowserLoading, setQaBrowserLoading] = useState(false);
  const [qaBrowserError, setQaBrowserError] = useState("");
  const [qaBrowserMediaResolution, setQaBrowserMediaResolution] = useState<MediaResolution | null>(null);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // 不同设备只装了不同相册：默认值不能再写死 album3/full-album3-38q。
  // manifests 异步到达，或当前选择在新机器上不存在时，落到本机真实存在的第一个相册/题集。
  useEffect(() => {
    if (!manifests.length) return;
    const selected = manifests.find((manifest) => manifest.album_id === qaBrowserAlbum);
    if (!selected) {
      const fallback = manifests.find((manifest) => manifest.album_id === "album3") || manifests[0]!;
      setQaBrowserAlbum(fallback.album_id);
      return;
    }
    const sets = selected.qa_sets || [];
    if (sets.length && !sets.includes(qaBrowserSet)) {
      setQaBrowserSet(sets[0]!);
    } else if (!sets.length && qaBrowserSet) {
      setQaBrowserSet("");
    }
  }, [manifests, qaBrowserAlbum, qaBrowserSet]);

  /** 1.0 `loadQaBrowser`（`:2779-2788`）。 */
  const loadQaBrowser = useCallback(async () => {
    setQaBrowserLoading(true);
    setQaBrowserError("");
    try {
      const data = await api<QaDatasetResponse>(
        `/qa-dataset?album_id=${encodeURIComponent(qaBrowserAlbum)}&qa_set=${encodeURIComponent(qaBrowserSet)}&sentrix_url=${encodeURIComponent(sentrixUrl.trim())}`,
      );
      if (!aliveRef.current) return;
      setQaBrowserItems(data.items || []);
      setQaTask(""); setQaSubtype(""); setQaAction(""); setQaBrowserTag("");
      setQaBrowserMediaResolution(data.media_resolution || null);
    } catch (e) {
      if (!aliveRef.current) return;
      setQaBrowserError(e instanceof Error ? e.message : String(e));
      setQaBrowserItems([]);
      setQaBrowserMediaResolution(null);
    } finally {
      if (aliveRef.current) setQaBrowserLoading(false);
    }
  }, [qaBrowserAlbum, qaBrowserSet, sentrixUrl]);

  /** 1.0 `qaBrowserOptions`（`:2768`）。 */
  const qaBrowserOptions = useMemo(
    () => manifests.find((m) => m.album_id === qaBrowserAlbum)?.qa_sets || [],
    [manifests, qaBrowserAlbum],
  );

  /** 1.0 `qaBrowserTags`（`:2769`）：去重后排序。 */
  const qaBrowserTags = useMemo(
    () => [...new Set(qaBrowserItems.flatMap((item) => (Array.isArray(item.tags) ? item.tags : [])))].sort(),
    [qaBrowserItems],
  );

  /** 1.0 `visibleQaBrowserItems`（`:2770-2778`）：先标签后关键词，关键词覆盖 qa_id/问题/答案/标签。 */
  const visibleQaBrowserItems = useMemo(() => {
    const query = qaBrowserSearch.trim().toLocaleLowerCase();
    return qaBrowserItems.filter((item) => {
      if (qaBrowserTag && !(item.tags || []).includes(qaBrowserTag)) return false;
      if (qaTask && item.task_type !== qaTask) return false;
      if (qaSubtype && item.question_type !== qaSubtype) return false;
      if (qaAction && item.expected_action !== qaAction) return false;
      if (!query) return true;
      return [item.qa_id, item.question, item.answer, taxonomyLabel(item.task_type), taxonomyLabel(item.question_type), ...(item.tags || [])].some((value) =>
        String(value || "")
          .toLocaleLowerCase()
          .includes(query),
      );
    });
  }, [qaBrowserItems, qaBrowserSearch, qaBrowserTag, qaTask, qaSubtype, qaAction]);

  return {
    qaTask, setQaTask, qaSubtype, setQaSubtype, qaAction, setQaAction,
    qaBrowserAlbum,
    setQaBrowserAlbum,
    qaBrowserSet,
    setQaBrowserSet,
    qaBrowserItems,
    qaBrowserSearch,
    setQaBrowserSearch,
    qaBrowserTag,
    setQaBrowserTag,
    qaBrowserLoading,
    qaBrowserError,
    qaBrowserMediaResolution,
    qaBrowserOptions,
    qaBrowserTags,
    visibleQaBrowserItems,
    loadQaBrowser,
  };
}
