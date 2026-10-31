/** Score existing answers without running the answering model. */
import { useEffect, useRef } from "react";

import { GtImageToggle } from "../../components/GtImageToggle.tsx";
import type { RejudgeTask } from "../../api/types.ts";
import type { PromptKindMeta, RejudgeScope, RecoveryPlan } from "../../hooks/useRunDetail.ts";
import { statusLabel } from "../../lib/format.ts";

/**
 * 1.0 `App.vue:2465` 的三类提示词中文名。2.0 在 `hooks/useConfig.ts:69` 是私有的
 * （未 export），本切片不能改 hooks，照搬一份只读副本。
 */
const PROMPT_KIND_LABELS: Record<string, string> = {
  answer_quality: "回答质量",
  task_decision: "任务判断",
  evidence: "证据核验",
};

export interface RejudgeCardProps {
  scope?: RejudgeScope;
  onScopeChange?: (scope: RejudgeScope) => void;
  judgeUseGtImages?: boolean;
  onJudgeUseGtImagesChange?: (value: boolean) => void;
  recoveryPlan?: RecoveryPlan | null;
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
  activeRejudge, scope = "missing", onScopeChange, judgeUseGtImages = false, onJudgeUseGtImagesChange, recoveryPlan,
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
    missing: "补齐缺失评分", failed: "重试失败评分", unscored: "为未评分回答打分", all: "重新评分全部回答",
  };
  const count = recoveryPlan?.scoring?.[scope];

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
    <section className="rejudge-card scoring-card">
      <div className="rejudge-head">
        <div>
          <h3>为已有回答评分</h3>
          <p>由 Judge（评分模型）为已保存的回答打分，答题模型不重新答题。</p>
        </div>
        {activeRejudge ? (
          <span className={"phase-status " + (activeRejudge.status || "")}>{statusLabel(activeRejudge.status)}</span>
        ) : null}
      </div>
      <div className="scoring-options">
        <label className="scoring-scope">评分范围
          <select aria-label="评分范围" value={scope} disabled={running || rejudgeSubmitting} onChange={event => onScopeChange?.(event.target.value as RejudgeScope)}>
            <option value="missing">失败或未评分的回答</option>
            <option value="failed">评分失败的回答</option>
            <option value="unscored">尚未评分的回答</option>
            <option value="all">全部已有回答</option>
          </select>
        </label>
        <GtImageToggle checked={judgeUseGtImages} disabled={running || rejudgeSubmitting}
          onChange={value => onJudgeUseGtImagesChange?.(value)} />
      </div>
      <div className="scoring-summary">
        {count ? <div className="scoring-counts" aria-label="本次评分数量">
          <span><strong>{count.qa_count}</strong> 题</span>
          <span><strong>{count.turn_count}</strong> 轮对话</span>
          <span><strong>{count.subscore_count}</strong> 项评分</span>
        </div> : <span className="muted small">按所选范围处理已有回答</span>}
        <button type="button" className="btn" disabled={!canRejudge || rejudgeSubmitting || running || count?.turn_count === 0}
          onClick={() => onRequestRejudge()}>{running ? "评分进行中…" : rejudgeSubmitting ? "正在提交…" : actionLabel[scope]}</button>
      </div>
      {count?.turn_count === 0 ? <p className="muted small">此范围内没有需要评分的回答。</p> : null}
      <p className="scoring-note">{scope === "all" ? "本次将替换全部已有评分，旧评分保留在历史中。" : "保留已有有效评分，包括 0 分。"}</p>
      <details className="scoring-details">
        <summary>评分说明</summary>
        <p>GT 指标准答案与证据。开启图片辅助后，只将标注图片提供给质量评分模型，不附视频，也不传给答题模型。没有图片标注时使用文字评分；已标注的图片缺失或读取失败会记为评分失败。</p>
        <p>每轮可包含回答质量、任务判断等多项评分；补评只处理所选范围内缺失或失败的项目，保留其他有效分数。已有评分保留当时的评分模式。</p>
      </details>
      <details className="scoring-details scoring-prompt-editor">
        <summary>评分提示词 <span>{judgeModel || "未配置评分模型"}</span></summary>
        <div className="prompt-kind-tabs">
          {judgePromptKinds.map((meta) => {
            const kind = meta.kind || "";
            return (
              <button
                key={kind}
                type="button"
                className={"btn ghost compact prompt-kind-tab" + (activePromptKind === kind ? " active" : "")}
                onClick={() => onSwitchPromptKind(kind)}
              >
                {PROMPT_KIND_LABELS[kind] || kind}
                {meta.custom ? <span className="custom-badge">已自定义</span> : null}
              </button>
            );
          })}
        </div>
        <label className="rejudge-prompt">
          系统提示词（{PROMPT_KIND_LABELS[activePromptKind] || activePromptKind}）
          <textarea
            value={rejudgePrompt}
            disabled={running}
            rows={8}
            spellCheck={false}
            onChange={(event) => onRejudgePromptChange(event.target.value)}
          />
        </label>
        <div className="rejudge-toolbar">
          <span className="muted small">
            {rejudgePrompt.length} 字符 · Judge {judgeModel || "-"}
          </span>
          <div className="rejudge-actions">
            <button className="btn ghost compact" disabled={running} onClick={() => onSavePrompt?.()}>
              保存提示词
            </button>
            <button className="btn ghost compact" disabled={running} onClick={onResetPrompt}>
              恢复默认
            </button>

          </div>
        </div>
      </details>
      {activeRejudge ? (
        <div className="rejudge-progress">
          {"judge_use_gt_images" in activeRejudge ? <p className="muted small">本次评分方式：{activeRejudge.judge_use_gt_images ? "GT 图片辅助" : "文字 GT"}</p> : null}
          <div className="rejudge-progress-meta">
            <span>
              {activeRejudge.completed || 0}/{activeRejudge.total || 0} 轮
            </span>
            <span>
              失败 {Number(activeRejudge.failed || 0)} · {rejudgePercent}%
            </span>
          </div>
          <div className="phase-bar">
            <div className="phase-bar-fill" style={{ width: `${rejudgePercent}%` }} />
          </div>
          {activeRejudge.error ? <p className="error">{String(activeRejudge.error)}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
