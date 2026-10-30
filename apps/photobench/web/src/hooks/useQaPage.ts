/**
 * 「逐题结果」面板的数据层。逐条移植 1.0 `App.vue`（只读，唯一权威）：
 *
 *   - 状态：`qaPage` `:216`、`qaDetails` `:217`、`openQaItems` `:218`、
 *     `loadingQaItems` `:219`、`qaPageSize` `:220`、`qaFilters` `:221`
 *   - `visibleQaItems`   `:355-364`
 *   - `loadQaPage`       `:2261-2284`（含 judge 增量刷新判据）
 *   - `applyQaFilters`   `:2285`
 *   - `resetQaFilters`   `:2286-2289`
 *   - `loadQaDetail`     `:2347-2355`
 *   - `toggleQa`         `:2356-2361`
 *   - `changeQaPage`     `:2362-2367`
 *   - `changeQaPageSize` `:2368`
 *
 * 三处 React 化改写，其余逐行照搬：
 *
 *   1. `qaDetails` / `openQaItems` / `loadingQaItems` 在 1.0 是 `reactive`，读写看得见
 *      最新值；React 的 state 在闭包里会读到旧快照。这里用「ref 为权威 + state 做镜像」
 *      （`commit`）：写时同步更新 ref 供异步链读最新值，再把同一个新值交给 `useState`
 *      触发重渲染。两个 Set **必须整体替换**（`new Set(prev)`）——`prev.add()` 不换引用，
 *      React 不会重渲染。
 *   2. `changeQaPage` 里的 `document.querySelector("#qa-results")` 换成返回给组件的
 *      `resultsRef`，不留全局查询（1.0 `:2366`）。
 *   3. 1.0 用 `activeRunId.value !== runId`（`:2267` / `:2353`）防串台；React 侧
 *      `activeRunId` 是入参，用 `activeRunIdRef` 存最新值供异步回来时比对。
 *
 * 卸载清理：`aliveRef` 在 unmount 置 false，迟到的响应直接丢弃。**注意**现有
 * `api/client.ts` 在 `fetch` 前无条件覆盖 `signal`，外部无法把 AbortController 传进去，
 * 且本切片不得改 client——所以这里用「丢弃迟到响应 + 不 setState」达成等价的清理效果。
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client.ts";
import type { ItemDetailResponse, ItemPage, Judge, RejudgeTask, RunItemDetail, RunItemSummary } from "../api/types.ts";

/** 1.0 `qaFilters`（`App.vue:221`）：8 个下拉 + 搜索，全部按字符串存（`v-model` 原样）。 */
export interface QaFilters {
  search: string;
  score: string;
  task_type: string;
  question_type?: string;
  tag: string;
  angle: string;
  difficulty: string;
  answerability: string;
  agent_status: string;
  primary: string;
}

const EMPTY_QA_FILTERS: QaFilters = {
  search: "",
  score: "",
  task_type: "",
  question_type: "",
  tag: "",
  angle: "",
  difficulty: "",
  answerability: "",
  agent_status: "",
  primary: "",
};

const INITIAL_QA_PAGE: ItemPage = { items: [], page: 1, page_size: 20, total: 0, pages: 1 };

type Updater<T> = T | ((prev: T) => T);

/**
 * 「ref 为权威 + state 做镜像」：写时同步更新 ref（供异步链读最新值），
 * 同时把**同一个**新值交给 `setState` 触发重渲染。等价于 1.0 的 `reactive` 可读性。
 */
function commit<T>(ref: { current: T }, setValue: (value: T) => void, next: Updater<T>): void {
  const resolved = typeof next === "function" ? (next as (prev: T) => T)(ref.current) : next;
  ref.current = resolved;
  setValue(resolved);
}

export interface UseQaPageOptions {
  /** 1.0 `activeRunId`（`:24`）。为空时所有请求直接返回。 */
  activeRunId: string | null;
  /** 1.0 `activeRejudge`（`:354`，取自 `activeRun.value?.rejudge`），`visibleQaItems` 用它。 */
  activeRejudge?: RejudgeTask | null;
}

export function useQaPage({ activeRunId, activeRejudge = null }: UseQaPageOptions) {
  const [qaPage, setQaPageState] = useState<ItemPage>(INITIAL_QA_PAGE);
  const qaPageRef = useRef(qaPage);
  const [qaDetails, setQaDetailsState] = useState<Record<number, RunItemDetail>>({});
  const qaDetailsRef = useRef(qaDetails);
  const [openQaItems, setOpenQaItemsState] = useState<Set<number>>(new Set());
  const openQaItemsRef = useRef(openQaItems);
  const [loadingQaItems, setLoadingQaItemsState] = useState<Set<number>>(new Set());
  const loadingQaItemsRef = useRef(loadingQaItems);
  const [qaPageSize, setQaPageSizeState] = useState(20);
  const qaPageSizeRef = useRef(qaPageSize);
  const [qaFilters, setQaFiltersState] = useState<QaFilters>({ ...EMPTY_QA_FILTERS });
  const qaFiltersRef = useRef(qaFilters);

  /** `#qa-results`（1.0 `:3432`）的落点，组件挂到这个 ref 上。 */
  const resultsRef = useRef<HTMLElement | null>(null);

  const activeRunIdRef = useRef(activeRunId);
  useEffect(() => {
    activeRunIdRef.current = activeRunId;
  }, [activeRunId]);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const setQaPage = (next: ItemPage): void => commit(qaPageRef, setQaPageState, next);
  const setQaDetails = (next: Updater<Record<number, RunItemDetail>>): void =>
    commit(qaDetailsRef, setQaDetailsState, next);
  const setOpenQaItems = (next: Updater<Set<number>>): void => commit(openQaItemsRef, setOpenQaItemsState, next);
  const setLoadingQaItems = (next: Updater<Set<number>>): void =>
    commit(loadingQaItemsRef, setLoadingQaItemsState, next);
  const setQaPageSize = (next: number): void => commit(qaPageSizeRef, setQaPageSizeState, next);
  const setQaFilters = (next: Updater<QaFilters>): void => commit(qaFiltersRef, setQaFiltersState, next);

  /** 1.0 `loadQaDetail`（`:2347-2355`）。 */
  async function loadQaDetail(index: number, { force = false }: { force?: boolean } = {}): Promise<void> {
    const runId = activeRunIdRef.current;
    if (!runId || loadingQaItemsRef.current.has(index) || (!force && qaDetailsRef.current[index])) return;
    setLoadingQaItems((prev) => {
      const next = new Set(prev);
      next.add(index);
      return next;
    });
    try {
      const payload = await api<ItemDetailResponse>(`/runs/${encodeURIComponent(runId)}/items/${index}`);
      if (aliveRef.current && activeRunIdRef.current === runId) {
        setQaDetails((prev) => ({ ...prev, [index]: payload.item }));
      }
    } finally {
      setLoadingQaItems((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
    }
  }

  /**
   * 1.0 `loadQaPage`（`:2261-2284`）。
   *
   * judge 增量刷新的判据（照搬 `:2275-2280`）：对每个**已展开**（`qaDetails` 里已有）的
   * 题，取 `oldJudge = detail.judge || {}`、`newJudge = summary.judge || {}`：
   *
   *   - 当 `newJudge.rejudge_id` 为真 **且**
   *     （`rejudge_id` 变了 **或**（新 `status === "completed"` 且旧 `status !== "completed"`））
   *     → 把该 index 加入 `refreshDetails`，最后 `loadQaDetail(index, { force: true })` 重拉；
   *   - 否则 → `Object.assign(oldJudge, newJudge)` 就地合并（不重拉）。
   *
   * 两个边界都照搬：新 summary 没有 `rejudge_id` 时**永不重拉**（只合并）；`detail.judge`
   * 原本为空时 `oldJudge` 是 `{}` 这个临时对象，`Object.assign` 的结果被丢弃——即**空转**。
   */
  async function loadQaPage(page = qaPageRef.current.page || 1): Promise<void> {
    const runId = activeRunIdRef.current;
    if (!runId) return;
    const params = new URLSearchParams({ page: String(page), page_size: String(qaPageSizeRef.current) });
    for (const [key, value] of Object.entries(qaFiltersRef.current)) {
      if (value) params.set(key, value);
    }
    const payload = await api<ItemPage>(`/runs/${encodeURIComponent(runId)}/items?${params}`);
    if (!aliveRef.current || activeRunIdRef.current !== runId) return;
    setQaPage(payload);

    const refreshDetails: number[] = [];
    const merges: Array<{ index: number; judge: Judge }> = [];
    for (const summary of payload.items || []) {
      const detail = qaDetailsRef.current[summary.index];
      if (!detail) continue;
      const oldJudge: Judge = detail.judge || {};
      const newJudge: Judge = summary.judge || {};
      if (
        newJudge.rejudge_id &&
        (newJudge.rejudge_id !== oldJudge.rejudge_id ||
          (newJudge.status === "completed" && oldJudge.status !== "completed"))
      ) {
        refreshDetails.push(summary.index);
      } else if (detail.judge) {
        // 1.0 `Object.assign(oldJudge, newJudge)`：只改 detail 上已存在的 judge。
        merges.push({ index: summary.index, judge: { ...detail.judge, ...newJudge } });
      }
    }
    if (merges.length) {
      setQaDetails((prev) => {
        const next = { ...prev };
        for (const { index, judge } of merges) {
          const detail = next[index];
          if (detail) next[index] = { ...detail, judge };
        }
        return next;
      });
    }
    await Promise.all(refreshDetails.map((index) => loadQaDetail(index, { force: true })));
  }

  /** 1.0 `toggleQa`（`:2356-2361`）。 */
  async function toggleQa(summary: Pick<RunItemSummary, "index">): Promise<void> {
    const index = summary.index;
    if (openQaItemsRef.current.has(index)) {
      setOpenQaItems((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
      return;
    }
    setOpenQaItems((prev) => {
      const next = new Set(prev);
      next.add(index);
      return next;
    });
    await loadQaDetail(index);
  }

  /** 1.0 `changeQaPage`（`:2362-2367`）：夹到 `[1, pages]`，同页不动，切完滚到结果区。 */
  async function changeQaPage(page: number): Promise<void> {
    const target = Math.max(1, Math.min(Number(page), qaPageRef.current.pages || 1));
    if (target === qaPageRef.current.page) return;
    await loadQaPage(target);
    resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /**
   * 1.0 `changeQaPageSize`（`:2368`）。1.0 靠 `v-model.number` 先把 `qaPageSize` 写好、
   * `@change` 再调它；React 没有 v-model，所以这里接受可选的新值先落到 `qaPageSize`，
   * 再与 1.0 一样回到第 1 页。
   */
  async function changeQaPageSize(size?: number): Promise<void> {
    if (size !== undefined) setQaPageSize(size);
    await loadQaPage(1);
  }

  /** 1.0 `applyQaFilters`（`:2285`）。 */
  async function applyQaFilters(): Promise<void> {
    await loadQaPage(1);
  }

  /** 1.0 `resetQaFilters`（`:2286-2289`）：清空全部 9 个条件再回第 1 页。 */
  async function resetQaFilters(): Promise<void> {
    setQaFilters({ ...EMPTY_QA_FILTERS });
    await loadQaPage(1);
  }

  /**
   * 1.0 `:2322-2326`（`loadActiveRun` 的 `resetPage` 分支）：**立刻**清空逐题分页、已加载
   * 详情与展开项。**不动** `qaFilters` / `qaPageSize`（1.0 同样不清）。`page_size` 沿用
   * 当前值（1.0 `:2323` 的 `page_size: qaPageSize.value`），不是 `20` 这个初值。
   */
  function resetQaState(): void {
    setQaPage({ items: [], page: 1, page_size: qaPageSizeRef.current, total: 0, pages: 1 });
    setQaDetails({});
    setOpenQaItems(new Set());
  }

  /**
   * `openChainNode`（1.0 `:1984-2015`）在 `await` 多个 `loadQaDetail` 之后要读**权威**的
   * 详情/分页；组合层拿到的是渲染快照，异步回来时可能已过期。这里读内部 ref（本就是
   * 「ref 为权威」的那份）。
   */
  function getQaSnapshot(): { page: ItemPage; details: Record<number, RunItemDetail> } {
    return { page: qaPageRef.current, details: qaDetailsRef.current };
  }

  /** `v-model="qaFilters.x"` 的 React 替身：写单个字段。 */
  function setQaFilter<K extends keyof QaFilters>(key: K, value: QaFilters[K]): void {
    setQaFilters((prev) => ({ ...prev, [key]: value }) as QaFilters);
  }

  /** 1.0 `visibleQaItems`（`:355-364`）。 */
  const visibleQaItems = useMemo(() => {
    const items = qaPage.items || [];
    const task = activeRejudge;
    if (!task || task.status === "completed") return items;
    if (!["running", "failed", "interrupted"].includes(task.status ?? "")) return items;
    return items.filter((item) => {
      const judge = item.judge || {};
      return judge.rejudge_id === task.rejudge_id && ["completed", "failed"].includes(judge.status ?? "");
    });
  }, [qaPage, activeRejudge]);

  return {
    qaPage,
    qaDetails,
    openQaItems,
    loadingQaItems,
    qaPageSize,
    qaFilters,
    visibleQaItems,
    resultsRef,
    loadQaPage,
    loadQaDetail,
    toggleQa,
    changeQaPage,
    changeQaPageSize,
    applyQaFilters,
    resetQaFilters,
    resetQaState,
    getQaSnapshot,
    setQaFilter,
    setQaFilters,
    setQaPageSize,
  };
}

export type UseQaPageApi = ReturnType<typeof useQaPage>;
