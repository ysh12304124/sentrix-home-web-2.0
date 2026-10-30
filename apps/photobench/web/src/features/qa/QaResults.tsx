import { taxonomyLabel } from "../../lib/qaTaxonomy.ts";
/**
 * 「QA 逐题结果」入口。移植 1.0 `App.vue:3432-3670` 的
 * `<section id="qa-results" class="qa-results-section">`：
 * 头部（计数 + 分页 + 每页条数）→ `<details class="qa-results-body">`（筛选表单 + 列表 + 底部分页）。
 *
 * 状态来自组合层传入的 `qa`（`hooks/useQaPage` 的实例；1.0 `qaPage`/`qaDetails`/
 * `openQaItems`/`loadingQaItems`/`qaPageSize`/`qaFilters` 的移植）——组合层持有它，
 * 因为 `RunDetail` 的记忆面板要用**同一份** `qaDetails`。切 run 的重置/重拉由组合层
 * 经 `useRuns` 的 `resetQaState`/`loadQaPage` 触发（1.0 `loadActiveRun({resetPage})`）。
 * 本组件只负责：
 *   - 把 `resultsRef` 挂到 `#qa-results` 对应的元素上（`changeQaPage` 用它滚回顶部）；
 *   - 组装筛选表单（8 个下拉 + 搜索）与分页控件；
 *   - 逐条渲染 `QaItemCard`，并把「JUDGE 输入弹窗」这个视图内状态管起来。
 *
 * 人工复核（1.0 `:3457` / `:3665`）的草稿态在 1.0 由 run 详情层 `loadActiveRun`(`:2296`) 装载、
 * `saveReviews`(`:2338`) 写回，**不在 `useQaPage`**；这里按可选 props 接收，缺省时结构照旧、
 * 交互为空操作（接线归 runs 组合层）。
 *
 * **图片灯箱**：1.0 的 `.lightbox` 是 app 根级的单例元素（`:3767-3772`，QA 浏览视图也用），
 * 所以这里不再自己渲染一份，改成把点击目标经 `onOpenMedia` 上抛给组合层持有的共享灯箱。
 */
import { useState } from "react";
import type { RefObject } from "react";

import type { MediaLike } from "../../api/media.ts";
import type { Run, RunItemDetail, RunItemSummary } from "../../api/types.ts";
import { openImageTarget } from "../../lib/details.ts";
import { esc, modelName } from "../../lib/format.ts";
import { attributionLabel } from "../../lib/trace.ts";
import type { UseQaPageApi } from "../../hooks/useQaPage.ts";

import { JudgeInputModal } from "./JudgeInputModal.tsx";
import { QaItemCard } from "./QaItemCard.tsx";
import type { QaPageView, ReviewDraft } from "./qaShared.ts";

export interface QaResultsProps {
  /** 1.0 `activeRun`。取其 `run_id` / `rejudge` / `album_id` / `status`。 */
  activeRun: Run | null;
  /**
   * 逐题状态。**由组合层持有**（`useQaPage`）——它必须与 `RunDetail` 的记忆面板共享同一份
   * `qaDetails`，所以不能在本组件内部再起一个 hook 实例（1.0 是单组件、天然共享）。
   */
  qa: UseQaPageApi;
  /**
   * 1.0 `runDetailRef`（`App.vue:2372`，由 `useRuns` 定义）——`scrollToRunDetail` 滚动的
   * 锚点就是 `#qa-results`。组合层把那个 ref 传进来，与 `useQaPage` 的 `resultsRef`
   * （`changeQaPage` 回页顶用）一起挂到同一个元素上，避免两处各挂一个、滚到不确定的那个。
   */
  resultRef?: RefObject<HTMLElement | null>;
  /** 1.0 `reviewFor(summary)`（草稿由 run 详情层持有）。 */
  reviewFor?: (summary: RunItemSummary) => ReviewDraft;
  /** 1.0 复核编辑器 `v-model`（草稿写回）。 */
  onReviewChange?: (summary: RunItemSummary, patch: ReviewDraft) => void;
  /** 1.0 `reviewSaving`。 */
  reviewSaving?: boolean;
  /** 1.0 `saveReviews`。 */
  onSaveReviews?: () => void;
  /**
   * 点击图片时上抛给组合层的**共享**灯箱（1.0 `:3767` 的根级元素，两个视图共用）。
   * 缺省时为无操作（与 1.0 的灯箱是根级、本切片不持有状态一致）。
   */
  onOpenMedia?: (target: { url: string; name: string }) => void;
}

export function QaResults({
  activeRun,
  qa,
  resultRef,
  reviewFor,
  onReviewChange,
  onOpenMedia,
}: QaResultsProps) {
  const activeRejudge = activeRun?.rejudge ?? null;
  const {
    qaPage,
    qaDetails,
    openQaItems,
    loadingQaItems,
    qaPageSize,
    qaFilters,
    visibleQaItems,
    resultsRef,
    toggleQa,
    changeQaPage,
    changeQaPageSize,
    applyQaFilters,
    resetQaFilters,
    setQaFilter,
  } = qa;

  const [judgeItem, setJudgeItem] = useState<RunItemDetail | null>(null);

  const page = qaPage as QaPageView;
  const facets = page.facets ?? {};
  const modelLabel = modelName(activeRun);
  const albumId = activeRun?.album_id ?? "";

  const openImage = (media: MediaLike): void => {
    const target = openImageTarget(media);
    if (target) onOpenMedia?.(target);
  };

  // 同一元素被两个 ref 需要：`useQaPage` 的 `resultsRef`（切页回顶）与组合层的
  // `runDetailRef`（`scrollToRunDetail`）。回调 ref 一次写两个，别让两处各挂各的。
  const setResultsNode = (node: HTMLElement | null): void => {
    resultsRef.current = node;
    if (resultRef) resultRef.current = node;
  };

  return (
    <section id="qa-results" className="qa-results-section" ref={setResultsNode}>
      <div className="qa-results-heading">
        <div>
          <h3>QA 逐题结果</h3>
          <span className="muted small">
            筛选结果 {page.total} / 全部 {esc(page.unfiltered_total ?? page.total)} 条
          </span>
        </div>
        {page.pages > 1 && (
          <div className="pager">
            <button
              className="btn ghost compact"
              disabled={!page.has_previous}
              onClick={() => void changeQaPage(page.page - 1)}
            >
              上一页
            </button>
            <span>
              {page.page} / {page.pages}
            </span>
            <button
              className="btn ghost compact"
              disabled={!page.has_next}
              onClick={() => void changeQaPage(page.page + 1)}
            >
              下一页
            </button>
          </div>
        )}
        <label className="page-size-control">
          每页
          <select value={qaPageSize} onChange={(event) => void changeQaPageSize(Number(event.target.value))}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
      </div>
      <details className="qa-results-body">
        <summary>展开逐题结果（{page.total} 条）</summary>
        <form
          className="qa-filters"
          onSubmit={(event) => {
            event.preventDefault();
            void applyQaFilters();
          }}
        >
          <input
            type="search"
            placeholder="搜索题号或问题"
            value={qaFilters.search}
            onChange={(event) => setQaFilter("search", event.target.value)}
          />
          <select value={qaFilters.score} onChange={(event) => setQaFilter("score", event.target.value)}>
            <option value="">全部 Judge 分数</option>
            <option value="2">2 分</option>
            <option value="1">1 分</option>
            <option value="0">0 分</option>
          </select>
          <select value={qaFilters.task_type} onChange={(event) => setQaFilter("task_type", event.target.value)}>
            <option value="">全部任务类型</option>
            {(facets.task_types ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select aria-label="运行细分题型" value={qaFilters.question_type || ""} onChange={event => setQaFilter("question_type", event.target.value)}>
            <option value="">全部细分题型</option>
            {(facets.question_types ?? []).map(value => <option key={value} value={value}>{taxonomyLabel(value)}</option>)}
          </select>
          <select value={qaFilters.tag} onChange={(event) => setQaFilter("tag", event.target.value)}>
            <option value="">全部标签</option>
            {(facets.tags ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select value={qaFilters.angle} onChange={(event) => setQaFilter("angle", event.target.value)}>
            <option value="">全部问题角度</option>
            {(facets.angles ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select value={qaFilters.difficulty} onChange={(event) => setQaFilter("difficulty", event.target.value)}>
            <option value="">全部难度</option>
            {(facets.difficulties ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select
            value={qaFilters.answerability}
            onChange={(event) => setQaFilter("answerability", event.target.value)}
          >
            <option value="">全部可回答性</option>
            {(facets.answerabilities ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select
            value={qaFilters.agent_status}
            onChange={(event) => setQaFilter("agent_status", event.target.value)}
          >
            <option value="">全部 Agent 状态</option>
            {(facets.agent_statuses ?? []).map((value) => (
              <option key={value} value={value}>
                {taxonomyLabel(value)}
              </option>
            ))}
          </select>
          <select value={qaFilters.primary} onChange={(event) => setQaFilter("primary", event.target.value)}>
            <option value="">全部归因层</option>
            {(facets.attribution_layers ?? []).map((value) => (
              <option key={value} value={value}>
                {attributionLabel(value)}
              </option>
            ))}
          </select>
          <button className="btn compact" type="submit">
            筛选
          </button>
          <button className="btn ghost compact" type="button" onClick={() => void resetQaFilters()}>
            重置
          </button>

        </form>
        {activeRejudge?.status === "running" && !visibleQaItems.length && (
          <div className="qa-results-empty">正在等待本轮第一条 Judge 评分结果…</div>
        )}
        {visibleQaItems.map((summary) => (
          <QaItemCard
            key={summary.index}
            summary={summary}
            detail={qaDetails[summary.index]}
            loading={loadingQaItems.has(summary.index)}
            open={openQaItems.has(summary.index)}
            albumId={albumId}
            modelLabel={modelLabel}
            activeRejudge={activeRejudge}
            onToggle={() => void toggleQa(summary)}
            onOpenImage={openImage}
            onOpenJudgeInput={setJudgeItem}
            review={reviewFor?.(summary)}
            onReviewChange={onReviewChange ? (patch) => onReviewChange(summary, patch) : undefined}
          />
        ))}
        {page.pages > 1 && (
          <div className="pager pager-bottom">
            <button
              className="btn ghost compact"
              disabled={!page.has_previous}
              onClick={() => void changeQaPage(page.page - 1)}
            >
              上一页
            </button>
            <span>
              第 {page.page} / {page.pages} 页
            </span>
            <button
              className="btn ghost compact"
              disabled={!page.has_next}
              onClick={() => void changeQaPage(page.page + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </details>
      {judgeItem && <JudgeInputModal item={judgeItem} onClose={() => setJudgeItem(null)} />}
    </section>
  );
}
