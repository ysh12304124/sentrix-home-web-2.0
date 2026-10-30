/**
 * 评测运行列表的数据 hook：迁移 1.0 `App.vue`（只读权威）的 runs 侧状态与轮询。
 *
 * 覆盖：`loadRuns`(:2233) / `runPage`(:23) / `activeRunId`(:24) / `hasRunning`(:352) /
 * `changeRunPage`(:2249) / `scrollToRunDetail`(:2369) / `selectRun`(:2379) /
 * `deleteRun`(:2691) / `startPolling`(:2697)。
 *
 * **两处必须改掉 1.0 的全局查询**（1.0 用 `document.querySelector` + `window.scrollTo`）：
 * `changeRunPage` 与 `scrollToRunDetail` 改成 hook 返回的 `runsRegionRef` /
 * `runDetailRef` + `scrollIntoView`。`scrollToRunDetail` 保留 1.0 的
 * `requestAnimationFrame`（= Vue 的 `nextTick`）等 DOM 落地。
 *
 * **本 hook 组装 `useRunDetail`**：轮询、`selectRun`、`deleteRun`、`startRejudge` 的
 * 后续都横跨两侧状态，而 `activeRunId` 归本 hook（1.0 唯一来源），所以由本 hook
 * 调用 `useRunDetail` 并把 runs 侧的桥接（`updateRunSummary` / `loadRuns` /
 * `startPolling` / `setError`）注入，返回值与 detail 侧合并。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, post } from "../api/client.ts";
import type { Run, RunPage, RunSummary } from "../api/types.ts";
import { useRunDetail } from "./useRunDetail.ts";
import type { RunDetailApi } from "./useRunDetail.ts";

/** 由其它切片（逐题 hook）提供的桥接；接上后 `loadActiveRun` 的并发集与 1.0 完全一致。 */
export interface UseRunsOptions {
  /** 1.0 `:2331` / `:2344`：`loadQaPage(page)`，`page` 缺省为当前页。 */
  loadQaPage?: (page?: number) => Promise<unknown>;
  /** 1.0 `:2324-2326`：清空 qaPage / qaDetails / openQaItems。 */
  resetQaState?: () => void;
  /**
   * 配置里的 `judge_prompt`（1.0 `config.value?.judge_prompt || ""`）。
   *
   * `/judge-prompts` 拿不到 `default`、或整个请求失败时，提示词初始值与「恢复默认」
   * 都靠它兜底。**不传的后果是提示词为空**，而 `canRejudge` 要求提示词非空——
   * 用户会看到一个永远灰着的「重新 Judge」按钮，且「恢复默认」点下去是清空。
   */
  judgePromptFallback?: string;
}

/**
 * 1.0 的 `runPage`（`:23`）**不含 `runs`**（那是独立的 `runs` ref，`:22`）——照搬这个形状。
 * 显式列出字段而不 `Omit<RunPage, "runs">`：`RunPage` 带 `[key: string]: unknown` 索引签名，
 * `Omit` 会把具体字段一起吞成 `unknown`。
 */
export interface RunPageState {
  page: number;
  page_size: number;
  total: number;
  pages: number;
  has_previous: boolean;
  has_next: boolean;
  active_count: number;
  scan?: RunPage["scan"];
}

const INITIAL_RUN_PAGE: RunPageState = {
  page: 1,
  page_size: 20,
  total: 0,
  pages: 1,
  has_previous: false,
  has_next: false,
  active_count: 0,
};

export type UseRunsApi = ReturnType<typeof useRuns>;

/** 1.0 `:352-353` 的判据，抽成纯函数供 state 渲染与轮询 ref 两条路共用。 */
function computeHasRunning(activeCount: number, runs: Run[]): boolean {
  return (
    Number(activeCount || 0) > 0 ||
    runs.some(
      (run) =>
        ["running", "pending", "cancelling"].includes(String(run.status)) ||
        run.rejudge?.status === "running",
    )
  );
}

export function useRuns(options: UseRunsOptions = {}) {
  const { loadQaPage, resetQaState, judgePromptFallback } = options;

  const [runs, setRuns] = useState<Run[]>([]);
  const [runPage, setRunPage] = useState<RunPageState>(INITIAL_RUN_PAGE);
  const [activeRunId, setActiveRunIdState] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [polling, setPolling] = useState(false);

  // 1.0 的 `activeRunId.value` 是同步可读的；state 读不到本轮 set 的值，
  // 所以 async 里的「是否已切换 run」判断（1.0 `:2314` / `:2267`）读 ref。
  const activeRunIdRef = useRef<string | null>(null);
  const runsRef = useRef<Run[]>([]);
  const runPageRef = useRef<RunPageState>(INITIAL_RUN_PAGE);
  /** 1.0 `#runs-region`（`:2989`）的落点，由视图挂到 `<section>` 上。 */
  const runsRegionRef = useRef<HTMLElement | null>(null);
  /** 1.0 `#qa-results`（`:3432`）的落点。 */
  const runDetailRef = useRef<HTMLElement | null>(null);

  const setActiveRunId = useCallback((id: string | null) => {
    activeRunIdRef.current = id;
    setActiveRunIdState(id);
  }, []);

  /** 1.0 `:2233`。timeout 8s / retries 2 是照搬的两个数。 */
  const loadRuns = useCallback(async (page?: number) => {
    const target = Math.max(1, Number(page ?? runPageRef.current.page) || 1);
    const payload = await api<RunPage>(
      `/runs?page=${target}&page_size=${runPageRef.current.page_size}`,
      { timeoutMs: 8000, retries: 2 },
    );
    const list = payload.runs || [];
    runsRef.current = list;
    setRuns(list);
    const next: RunPageState = {
      page: payload.page || 1,
      page_size: payload.page_size || 20,
      total: payload.total ?? list.length,
      pages: payload.pages || 1,
      has_previous: Boolean(payload.has_previous),
      has_next: Boolean(payload.has_next),
      active_count: Number(payload.active_count || 0),
      scan: payload.scan,
    };
    runPageRef.current = next;
    setRunPage(next);
  }, []);

  /** 1.0 `:2317-2319`：把 `effectiveRunSummary` 合回对应行。 */
  const updateRunSummary = useCallback((runId: string, summary: RunSummary) => {
    const next = runsRef.current.map((run) =>
      run.run_id === runId ? { ...run, summary: { ...(run.summary || {}), ...summary } } : run,
    );
    runsRef.current = next;
    setRuns(next);
  }, []);

  const startPolling = useCallback(() => setPolling(true), []);
  const onRunsReloaded = useCallback(async () => {
    await loadRuns();
  }, [loadRuns]);
  const onRejudgeStarted = useCallback(() => startPolling(), [startPolling]);

  const detail: RunDetailApi = useRunDetail({
    activeRunId,
    activeRunIdRef,
    updateRunSummary,
    loadQaPage,
    resetQaState,
    setError,
    onRunsReloaded,
    onRejudgeStarted,
    judgePromptFallback,
  });
  const { loadActiveRun, setActiveRun } = detail;
  // App 的 QA 桥接函数可在每次渲染时重建；它们只更新下一次轮询用的
  // loader，不能重启计时器，否则一次状态更新就会立即再发一轮请求。
  const pollDetailLoader = useRef(loadActiveRun);
  useEffect(() => {
    pollDetailLoader.current = loadActiveRun;
  }, [loadActiveRun]);

  /** 1.0 `:352-353`。用 ref 现算，好让轮询的异步闭包读到最新值。 */
  const hasRunningNow = useCallback(
    () => computeHasRunning(runPageRef.current.active_count, runsRef.current),
    [],
  );
  const hasRunning = useMemo(
    () => computeHasRunning(runPage.active_count, runs),
    [runPage.active_count, runs],
  );

  /**
   * 1.0 `:2697-2708`。1.0 是 `setTimeout` 自续期：跑完一次 → `hasRunning` 则 2s 后再来，
   * 异常则 5s 后再来，否则停。**保留自续期而不是平铺 `setInterval(2000)`**——
   * 后者表达不了 5s 的错误退避，也表达不了「不 running 就停」。
   * 卸载清理 = effect cleanup 清掉挂起的 timer（1.0 `onUnmounted` `:2835` 的意图）。
   *
   * `startPolling` 在已轮询时是 no-op（对应 1.0 `if (pollTimer || destroyed) return`）；
   * 轮询自然停止时把 `polling` 复位为 false，好让下一次 `startPolling` 能重新拉起。
   */
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      timer = null;
      try {
        await loadRuns();
        if (activeRunIdRef.current) await pollDetailLoader.current();
        if (cancelled) return;
        if (hasRunningNow() || ["pending", "running"].includes(String(runPageRef.current.scan?.state))) {
          timer = window.setTimeout(poll, 2000);
        } else {
          setPolling(false);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [polling, loadRuns, hasRunningNow]);

  const changeRunPage = useCallback(
    async (page: number) => {
      // 1.0 不夹取 page，直接交给 loadRuns 的 Math.max(1, ...)。
      await loadRuns(page);
      runsRegionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [loadRuns],
  );

  /** 1.0 `:2369-2378`：`nextTick` + `rAF` 后滚到详情；全局查询换成 ref。 */
  const scrollToRunDetail = useCallback(() => {
    requestAnimationFrame(() => {
      runDetailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  /** 1.0 `:2379-2386`。 */
  const selectRun = useCallback(
    (run: Run) => {
      setActiveRunId(run.run_id);
      setActiveRun({ ...run });
      scrollToRunDetail();
      void loadActiveRun({ resetPage: true }).catch((err: unknown) => {
        if ((err as Error).name !== "AbortError" && activeRunIdRef.current === run.run_id) {
          setError((err as Error).message || "读取评测详情失败");
        }
      });
    },
    [setActiveRunId, setActiveRun, scrollToRunDetail, loadActiveRun],
  );

  /**
   * 1.0 `:2691-2696`。1.0 的 `window.confirm` 做成可注入（缺省仍是它），
   * 测试与自定义弹窗都不用去 mock 全局。
   */
  const deleteRun = useCallback(
    async (run: Run, confirm: (message: string) => boolean = window.confirm) => {
      if (!confirm("删除此评测？")) return;
      await api(`/runs/${encodeURIComponent(run.run_id)}`, { method: "DELETE" });
      if (activeRunIdRef.current === run.run_id) {
        setActiveRunId(null);
        setActiveRun(null);
      }
      await loadRuns();
    },
    [setActiveRunId, setActiveRun, loadRuns],
  );

  const resumingRef = useRef(false);
  const [resuming, setResuming] = useState(false);
  const resumeRun = useCallback(async (runId: string, body: Record<string, unknown>) => {
    if (resumingRef.current || hasRunningNow()) return;
    resumingRef.current = true;
    setResuming(true);
    setError("");
    try {
      const result = await post<{run_id: string}>(`/runs/${encodeURIComponent(runId)}/resume`, body);
      await loadRuns();
      selectRun({run_id: result.run_id});
      startPolling();
    } catch (error) { setError((error as Error).message); }
    finally { resumingRef.current = false; setResuming(false); }
  }, [hasRunningNow, loadRuns, selectRun, startPolling]);

  return {
    runs,
    resuming, resumeRun,
    runPage,
    activeRunId,
    error,
    setError,
    hasRunning,
    loadRuns,
    changeRunPage,
    selectRun,
    deleteRun,
    startPolling,
    runsRegionRef,
    runDetailRef,
    ...detail,
  };
}
