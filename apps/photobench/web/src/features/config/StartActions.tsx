/** Controlled scoring settings and run actions. */
import { ScoringSettings } from '../../components/ScoringSettings.tsx';
import { ScoringPromptEditor, type ScoringPromptEditorProps } from '../../components/ScoringPromptEditor.tsx';
import { defaultScoring, type ScoringSelection } from '../../lib/scoring.ts';
import { runModeMeta, startDisabledReason, type StartDisabledInput } from "./rules.ts";

export interface StartActionsProps extends StartDisabledInput {
  prompts?: ScoringPromptEditorProps;
  scoring?: ScoringSelection;
  onScoringChange?: (v: ScoringSelection) => void;
  deleteScopeAfterRun: boolean;
  onDeleteScopeAfterRunChange: (next: boolean) => void;
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
  return (
    <div className="start-settings">
      {runMode !== "build" && (
        <ScoringSettings value={props.scoring ?? defaultScoring()} onChange={v => props.onScoringChange?.(v)} disabled={locked} />
      )}
      {runMode !== 'build' && props.prompts && <ScoringPromptEditor {...props.prompts} running={locked} />}
      <div className="start-action-bar">
        <div className="start-primary-actions">
          <button className="btn" type="button" disabled={Boolean(reason)} title={reason || undefined} onClick={onStart}>
            {buttonLabel}
          </button>
          <button className="btn warn" type="button" disabled={Boolean(props.stopping)} onClick={onStop}>停止全部</button>
        </div>
        {runMode === "full" && (
          <label className={`delete-scope-toggle${deleteScopeAfterRun ? " active" : ""}${locked ? " disabled" : ""}`}
            title="只在完整链路测试结束后删除本次生成的相册，谨慎开启">
            <input type="checkbox" checked={deleteScopeAfterRun} disabled={locked}
              onChange={event => onDeleteScopeAfterRunChange(event.target.checked)} />
            完成后删除相册
          </label>
        )}
      </div>
      {reason && reason !== buttonLabel && <span className="config-help start-disabled-reason">{reason}</span>}
    </div>
  );
}
