/** Controlled scoring settings and run actions. */
import { ScoringSettings } from '../../components/ScoringSettings.tsx';
import { ScoringPromptEditor, type ScoringPromptEditorProps } from '../../components/ScoringPromptEditor.tsx';
import { defaultScoring, runScoring, type ScoringSelection } from '../../lib/scoring.ts';
import type { Run } from '../../api/types.ts';
import { runModeMeta, startDisabledReason, type StartDisabledInput } from "./rules.ts";

export interface StartActionsProps extends StartDisabledInput {
  prompts?: ScoringPromptEditorProps;
  scoring?: ScoringSelection;
  runningRun?: Run | null;
  runningScoringError?: string;
  onScoringChange?: (v: ScoringSelection) => void;
  deleteScopeAfterRun: boolean;
  onDeleteScopeAfterRunChange: (next: boolean) => void;
  graphEnabled?: boolean;
  reasoning?: boolean;
  onReasoningChange?: (next: boolean) => void;
  onGraphEnabledChange?: (next: boolean) => void;
  graphBaselineRunId?: string;
  onGraphBaselineRunIdChange?: (next: string) => void;
  onStart: () => void;
  onStop: () => void;
  stopping?: boolean;
  openScale?: "ternary" | "binary";
  onOpenScaleChange?: (value: "ternary" | "binary") => void;
  choiceSemanticAux?: boolean;
  onChoiceSemanticAuxChange?: (value: boolean) => void;
  judgeUseGtImages?: boolean;
  onJudgeUseGtImagesChange?: (value: boolean) => void;
}

export function StartActions(props: StartActionsProps) {
  const {
    runMode,
    hasRunning,
    suiteRunning,
    deleteScopeAfterRun,
    onDeleteScopeAfterRunChange,
    onStart,
    onStop,
  } = props;

  const reason = startDisabledReason(props);
  const buttonLabel = hasRunning ? "已有任务运行中" : runModeMeta(runMode).button;

  const locked = suiteRunning || hasRunning;
  const frozenRun = locked && props.runningRun?.scoring_profiles?.length ? props.runningRun : null;
  const readingFrozen = locked && props.runningRun && !frozenRun;
  const scoringMode = locked && props.runningRun ? props.runningRun.mode : runMode;
  return (
    <div className="start-settings">
      {scoringMode !== "build" && (<>
        <p className="config-help">{frozenRun
          ? `运行任务评分（已冻结） · ${frozenRun.qa_set || '题集未记录'} · ${frozenRun.run_id}`
          : readingFrozen ? props.runningScoringError ? `读取运行评分配置失败：${props.runningScoringError}` : '正在读取运行任务的冻结评分配置…'
          : '新任务评分设置（切换题集时自动选择，可手动调整）'}</p>
        {!readingFrozen && <ScoringSettings value={frozenRun ? runScoring(frozenRun) : props.scoring ?? defaultScoring()} onChange={v => props.onScoringChange?.(v)} disabled={locked} />}
      </>)}
      {runMode !== 'build' && props.prompts && !props.runningRun && (
        <ScoringPromptEditor {...props.prompts} running={locked} />
      )}
      <div className="start-action-bar">
        <div className="start-primary-actions">
          <button className="btn" type="button" disabled={Boolean(reason)} title={reason || undefined} onClick={onStart}>
            {buttonLabel}
          </button>
          <button className="btn warn" type="button" disabled={Boolean(props.stopping)} onClick={onStop}>停止全部</button>
        </div>
        <div className="start-toggle-actions">
          {runMode !== "build" && (
            <label className={`reasoning-mode-toggle${(props.reasoning ?? true) ? " active" : ""}${locked ? " disabled" : ""}`}
              title="开启后放开工具与调用次数；关闭后恢复系统执行限制，适合端侧小模型。">
              <input type="checkbox" aria-label="测评推理模式" checked={props.reasoning ?? true} disabled={locked}
                onChange={event => props.onReasoningChange?.(event.target.checked)} />
              开启推理模式
            </label>
          )}
          {runMode === "full" && (
            <label className={`delete-scope-toggle${deleteScopeAfterRun ? " active" : ""}${locked ? " disabled" : ""}`}
              title="只在完整链路测试结束后删除本次生成的相册，谨慎开启">
              <input type="checkbox" checked={deleteScopeAfterRun} disabled={locked}
                onChange={event => onDeleteScopeAfterRunChange(event.target.checked)} />
              完成后删除相册
            </label>
          )}
        </div>
      </div>
      {reason && reason !== buttonLabel && <span className="config-help start-disabled-reason">{reason}</span>}
    </div>
  );
}
