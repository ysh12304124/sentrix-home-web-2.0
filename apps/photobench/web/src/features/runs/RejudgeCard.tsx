import { ScoringPromptEditor } from '../../components/ScoringPromptEditor.tsx';
/** Score existing answers without running the answering model. */
import { useEffect, useRef } from "react";

import { ScoringSettings } from '../../components/ScoringSettings.tsx';
import { defaultScoring, PROFILE_LABELS, type ScoringSelection } from '../../lib/scoring.ts';
import type { RejudgeTask } from "../../api/types.ts";
import type { PromptKindMeta, RejudgeScope } from "../../hooks/useRunDetail.ts";
import { statusLabel } from "../../lib/format.ts";

/**
 * 1.0 `App.vue:2465` 的三类提示词中文名。2.0 在 `hooks/useConfig.ts:69` 是私有的
 * （未 export），本切片不能改 hooks，照搬一份只读副本。
 */
export interface RejudgeCardProps {
  batchMode?: 'auto' | 'resume' | 'new';
  onBatchModeChange?: (mode:'auto' | 'resume' | 'new')=>void;
  batches?: Array<{batch_id:string;profile_id:string;created_at:string}>;
  resumeBatches?: Record<string,string>;
  onResumeBatchChange?: (profile:string,batch:string)=>void;
  scoring?: ScoringSelection;
  onScoringChange?: (v: ScoringSelection) => void;
  scoringPreview?: {qa_count:number;turn_count:number;subscore_count:number;profiles:Record<string,number>} | null;
  scoringPreviewError?: string;
  embedded?: boolean;
  disabledReason?: string;
  scope?: RejudgeScope;
  onScopeChange?: (scope: RejudgeScope) => void;
  activeRejudge: RejudgeTask | null;
  judgePromptKinds: PromptKindMeta[];
  activePromptKind: string;
  onSwitchPromptKind: (kind: string) => void;
  rejudgePrompt: string;
  onRejudgePromptChange: (value: string) => void;
  /** 归配置切片；缺省与 1.0 `judgeModel || '-'` 同形。 */
  judgeModel?: string;
  /** 归配置切片；未接线时可点但无副作用。 */
  onSavePrompt?: () => void;
  onResetPrompt: () => void;
  canRejudge: boolean;
  rejudgeSubmitting: boolean;
  rejudgeConfirmation: { message: string } | null;
  onRequestRejudge: () => boolean;
  onConfirmRejudge: () => void;
  onCancelRejudge: () => void;
  rejudgePercent: number;
}

export function RejudgeCard({
  batchMode='auto', onBatchModeChange, batches=[], resumeBatches={}, onResumeBatchChange,
  embedded = false, disabledReason, scoring = defaultScoring(), onScoringChange, scoringPreview, scoringPreviewError,
  activeRejudge, scope = "missing", onScopeChange,
  judgePromptKinds,
  activePromptKind,
  onSwitchPromptKind,
  rejudgePrompt,
  onRejudgePromptChange,
  judgeModel,
  onSavePrompt,
  onResetPrompt,
  canRejudge,
  rejudgeSubmitting,
  rejudgeConfirmation,
  onRequestRejudge,
  onConfirmRejudge,
  onCancelRejudge,
  rejudgePercent,
}: RejudgeCardProps) {
  const running = activeRejudge?.status === "running";
  const actionLabel: Record<RejudgeScope, string> = {
    missing: "补齐缺失评分", failed: "重试失败评分", unscored: "为未评分回答打分", all: batchMode === 'new' ? "新建批次重算所选回答" : "补齐全部回答的未完成评分",
    needs_review: "重新评估待复核回答",
  };
  const count = scoringPreview;
  // requestRejudge 只置位 rejudgeConfirmation，真正弹窗与执行在这里。用 ref 保证同一
  // 待确认对象只弹一次（回调 identity 变化也不会重复弹，StrictMode 双调也安全）。
  const handledConfirmation = useRef<{ message: string } | null>(null);
  useEffect(() => {
    if (!rejudgeConfirmation || handledConfirmation.current === rejudgeConfirmation) return;
    handledConfirmation.current = rejudgeConfirmation;
    if (window.confirm(rejudgeConfirmation.message)) onConfirmRejudge();
    else onCancelRejudge();
  }, [rejudgeConfirmation, onConfirmRejudge, onCancelRejudge]);

  return (
    <section className={embedded ? "recovery-column scoring-card" : "rejudge-card scoring-card"} aria-label="重新评分">
      <div className="rejudge-head">
        <div>
          {embedded ? <h4>重新评分</h4> : <h3>为已有回答评分</h3>}
          <p>复用已有回答，只调用评分模型。</p>
        </div>
        {activeRejudge ? (
          <span className={"phase-status " + (activeRejudge.status || "")}>{statusLabel(activeRejudge.status)}</span>
        ) : null}
      </div>
      {activeRejudge ? (
        <div className="rejudge-progress" aria-label="最近评分任务进度">
          <div className="rejudge-progress-meta">
            <strong>评分项目 {activeRejudge.completed || 0}/{activeRejudge.total || 0} 项</strong>
            <span>失败 {Number(activeRejudge.failed || 0)} · {rejudgePercent}%</span>
          </div>
          <div className="phase-bar"><div className="phase-bar-fill" style={{ width: `${rejudgePercent}%` }} /></div>
          {activeRejudge.qa_count != null && <p className='scoring-task-scope'>{Number(activeRejudge.qa_count)}题 · {Number(activeRejudge.turn_count)}轮对话 · {Number(activeRejudge.subscore_count)}个评分项目；同一轮按每套标准各计1项。</p>}
          {running && <p className='scoring-task-scope'>当前任务使用提交时冻结的评分配置，可在两套评分结果中查看。下方为下一次补评的配置。</p>}
          {"judge_use_gt_images" in activeRejudge ? <p className="muted small">最近任务的GT方式：{activeRejudge.judge_use_gt_images ? "GT 图片辅助" : "文字 GT"}</p> : null}
          {activeRejudge.error ? <p className="error">{String(activeRejudge.error)}</p> : null}
        </div>
      ) : null}
      <div className='scoring-controls'>
      <label>批次处理<select aria-label="批次处理" value={batchMode} disabled={running || rejudgeSubmitting} onChange={e => onBatchModeChange?.(e.target.value as 'auto'|'resume'|'new')}>
        <option value="auto">按当前配置补评（相同配置续写原批次）</option>
        <option value="resume">续评已有批次（沿用冻结配置）</option>
        <option value="new">新建批次重新评分</option>
      </select></label>
      <label>评分范围
        <select aria-label="评分范围" value={scope} disabled={running || rejudgeSubmitting} onChange={event => onScopeChange?.(event.target.value as RejudgeScope)}>
          <option value="missing">失败或未评分的回答</option>
          <option value="failed">评分失败的回答</option>
          <option value="unscored">尚未评分的回答</option>
          <option value="needs_review">待复核的回答</option>
          <option value="all">全部已有回答</option>
        </select>
      </label>
      </div>
      <ScoringSettings value={scoring} onChange={v => onScoringChange?.(v)} disabled={running || rejudgeSubmitting} hideGt={batchMode === 'resume'} />
      {batchMode === 'resume' && scoring.profiles.map(profile => <label key={profile}>{PROFILE_LABELS[profile]}续评批次
        <select value={resumeBatches[profile] || batches.filter(b => b.profile_id === profile).at(-1)?.batch_id || ''} onChange={e => onResumeBatchChange?.(profile,e.target.value)} disabled={running || rejudgeSubmitting}>
          <option value="">请选择已有批次</option>{batches.filter(b => b.profile_id === profile).map(b => <option key={b.batch_id} value={b.batch_id}>{b.created_at} · {b.batch_id}</option>)}
        </select></label>)}
      <div className="scoring-summary">
        {count ? <div className="scoring-counts" aria-label="本次评分数量">
          <span><strong>{count.qa_count}</strong> 题</span>
          <span><strong>{count.turn_count}</strong> 轮对话</span>
          <span><strong>{count.subscore_count}</strong> 项评分</span>
        </div> : <span className="muted small">按所选范围处理已有回答</span>}
        <button type="button" className="btn" disabled={!canRejudge || rejudgeSubmitting || running || !count || !!scoringPreviewError || count.turn_count === 0}
          onClick={() => onRequestRejudge()}>{running ? "评分进行中…" : rejudgeSubmitting ? "正在提交…" : actionLabel[scope]}</button>
      </div>
      {disabledReason ? <p className="recovery-note">{disabledReason}</p> : null}
      {count?.turn_count === 0 ? <p className="muted small">此范围内没有需要评分的回答。</p> : null}
      {scoringPreviewError && <p className="error">评分预览失败：{scoringPreviewError}</p>}
      <p className="scoring-note">同一轮按每套标准各计1个评分项目，总分按题目指定的评估轮计算。同配置只补未完成项目；修改配置会建立新批次。</p>
      {batchMode === 'resume' ? <p className="small muted">沿用所选批次冻结的Judge、提示词和GT配置。修改配置请使用另外两种批次处理方式。</p> : <ScoringPromptEditor {...{judgePromptKinds,activePromptKind,onSwitchPromptKind,rejudgePrompt,onRejudgePromptChange,judgeModel,onSavePrompt,onResetPrompt,running}} />}
    </section>
  );
}
