/**
 * 「QA 数据集浏览」视图外壳。逐行移植 1.0 `App.vue:3676-3685`（`<section class="qa-browser-page">`
 * + hero）与 `:3701-3762`（加载态 / 列表外壳）：
 *
 *   - hero：`DATASET REVIEW` kicker、`<h1>QA 数据集审阅</h1>`、说明文案、右上角
 *     `可见条数 / 总条数` 计数（`:3678-3685`）；
 *   - 工具栏（相册 / 题集 / 搜索 / 标签 + 错误 / 媒体来源提示，`:3686-3700`）在
 *     `QaBrowserToolbar.tsx`；
 *   - 单条审阅卡（`:3703-3761`）在 `QaReviewCard.tsx`，这里只做列表装配。
 *
 * 三处 2.0 适配（都不改 1.0 的可见行为）：
 *   1. 1.0 在「QA 数据集浏览」tab 按钮的 `@click` 里调 `loadQaBrowser()`（`:2842`）。
 *      2.0 的 tab 切换在 `App.tsx`（本切片不可改），等价物是**挂载时加载**——切到本视图
 *      才挂载、切回 runs 即卸载，和 1.0「每次点 tab 都重载」一致。
 *   2. `sentrixUrl` / `manifests` 在 1.0 是全局 ref（配置层 / `init()`），2.0 归组合层；
 *      本切片以 **props** 接收（`App.tsx` 仍以 `<QaBrowserView />` 空参渲染）。
 *   3. 1.0 的 `lightbox` 是 app 根级的共享元素（`:3767-3772`，runs 视图也用）；2.0 由
 *      组合层（`App.tsx`）持有，本切片把点击目标经 `onOpenMedia` 上抛，不再自己渲染一份。
 */
import { taxonomyLabel } from "../../lib/qaTaxonomy.ts";
import { useEffect, useRef } from "react";

import type { ManifestSummary } from "../../api/types.ts";
import { useQaBrowser } from "../../hooks/useQaBrowser.ts";
import { QaBrowserToolbar } from "./QaBrowserToolbar.tsx";
import { QaReviewCard } from "./QaReviewCard.tsx";

export interface QaBrowserViewProps {
  /** 1.0 `sentrixUrl`（`:231`）：拼进 `/qa-dataset` 的查询参数。组合层从 `useConfig` 注入。 */
  sentrixUrl?: string;
  /** 1.0 `manifests`：相册下拉的数据源。组合层从 `useConfig` 注入。 */
  manifests?: ManifestSummary[];
  /** 点击 GT 图片时上抛给组合层的共享灯箱（1.0 `:3767` 的根级元素）。 */
  onOpenMedia?: (target: { url: string; name: string }) => void;
}

export function QaBrowserView({ sentrixUrl = "", manifests = [], onOpenMedia }: QaBrowserViewProps = {}) {
  const qa = useQaBrowser({ sentrixUrl, manifests });

  // 1.0 `:2842` 在切到本视图时调 loadQaBrowser；挂载触发即等价。依赖只钉相册 / 题集：
  // 1.0 的两个 `@change`（`:3687` / `:3690`）也只在这两者变化时重载，sentrixUrl 变化不重载。
  // 用 ref 取最新函数、依赖里不放它，是为了不把 `sentrixUrl` 也带进依赖（那会多一次重载）。
  const loadQaBrowserRef = useRef(qa.loadQaBrowser);
  useEffect(() => {
    loadQaBrowserRef.current = qa.loadQaBrowser;
  });
  useEffect(() => {
    // 相册/题集还没落到本机真实数据时不要发请求；否则会先打出一条
    // album not found / qa_set not found，随后才被正确的选择覆盖。
    const qaSets = manifests.find((manifest) => manifest.album_id === qa.qaBrowserAlbum)?.qa_sets || [];
    if (!qa.qaBrowserAlbum || !qa.qaBrowserSet || !qaSets.includes(qa.qaBrowserSet)) return;
    void loadQaBrowserRef.current();
  }, [manifests, qa.qaBrowserAlbum, qa.qaBrowserSet]);

  const { qaBrowserItems, visibleQaBrowserItems } = qa;

  return (
    <section className="qa-browser-page" data-testid="qa-browser-view">
      <header className="qa-browser-hero">
        <div>
          <p className="qa-browser-kicker">DATASET REVIEW</p>
          <h1>QA 数据集审阅</h1>
          <p>按题检查对话设计、参考回答、图视频 GT 和证据元数据。</p>
        </div>
        <div className="qa-browser-count">
          <strong>{visibleQaBrowserItems.length}</strong>
          <span>
            {visibleQaBrowserItems.length === qaBrowserItems.length
              ? "道题目"
              : `/ ${qaBrowserItems.length} 道`}
          </span>
        </div>
      </header>

      <QaBrowserToolbar
        manifests={manifests}
        album={qa.qaBrowserAlbum}
        setAlbum={qa.setQaBrowserAlbum}
        qaSets={qa.qaBrowserOptions}
        qaSet={qa.qaBrowserSet}
        setQaSet={qa.setQaBrowserSet}
        search={qa.qaBrowserSearch}
        setSearch={qa.setQaBrowserSearch}
        tags={qa.qaBrowserTags}
        tag={qa.qaBrowserTag}
        setTag={qa.setQaBrowserTag}
        error={qa.qaBrowserError}
        mediaResolution={qa.qaBrowserMediaResolution}
        onRefresh={qa.loadQaBrowser}
      />

      <div className="qa-browser-toolbar">
        <label>内容大类<select aria-label="内容大类" value={qa.qaTask} onChange={e => { qa.setQaTask(e.target.value); qa.setQaSubtype(""); }}>
          <option value="">全部大类</option>
          {[...new Set(qaBrowserItems.map(x => x.task_type).filter(Boolean))].map(x => <option key={x} value={x}>{taxonomyLabel(x)}</option>)}
        </select></label>
        <label>细分题型<select aria-label="细分题型" value={qa.qaSubtype} onChange={e => qa.setQaSubtype(e.target.value)}>
          <option value="">全部小类</option>
          {[...new Set(qaBrowserItems.filter(x => !qa.qaTask || x.task_type === qa.qaTask).map(x => x.question_type).filter(Boolean))].map(x => <option key={x} value={x}>{taxonomyLabel(x)}</option>)}
        </select></label>
        <label>行为预期<select aria-label="行为预期" value={qa.qaAction} onChange={e => qa.setQaAction(e.target.value)}>
          <option value="">全部行为</option>
          {[...new Set(qaBrowserItems.map(x => x.expected_action).filter(Boolean))].map(x => <option key={x} value={x}>{taxonomyLabel(x)}</option>)}
        </select></label>
      </div>
      {qa.qaBrowserLoading ? (
        <div className="qa-browser-empty">正在加载数据集…</div>
      ) : (
        <div className="qa-browser-list">
          {visibleQaBrowserItems.map((item, idx) => (
            <QaReviewCard
              key={item.qa_id || idx}
              item={item}
              index={idx}
              onOpenMedia={(url, name) => onOpenMedia?.({ url, name })}
            />
          ))}
        </div>
      )}
    </section>
  );
}
