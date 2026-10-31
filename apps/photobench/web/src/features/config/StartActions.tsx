/**
 * 启动 / 停止评测按钮行。移植 1.0 `App.vue:2981-2985` 的 `<div class="actions">`。
 *
 * 与 1.0 的两处差别（都记在报告里）：
 *   1. 按钮的 `disabled` 由 `startDisabledReason`（1.0 `:2555-2564`，原样搬到
 *      `rules.ts`）决定；1.0 只把那个原因塞进 `:disabled`，用户看不到为什么点不动。
 *      这里额外把原因挂到 `title`，并在它与按钮文案不同时用一条 `.config-help` 显示
 *      出来——文案仍是 1.0 的原文，没有新造。
 *   2. 三个交互都是受控的（`onStart` / `onStop` / `onDeleteScopeAfterRunChange`）：
 *      `startSuite` / `stopSuite` / `deleteScopeAfterRun` 不在已写好的三个 hook 里，
 *      由组合层注入（1.0 它们是同一个组件里的方法，2.0 拆到 hook 之外）。
 */
import { GtImageToggle } from "../../components/GtImageToggle.tsx";
import { runModeMeta, startDisabledReason, type StartDisabledInput } from "./rules.ts";

export interface StartActionsProps extends StartDisabledInput {
  deleteScopeAfterRun: boolean;
  onDeleteScopeAfterRunChange: (next: boolean) => void;
  onStart: () => void;
  onStop: () => void;
  stopping?: boolean;
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

  return (
    <div className="actions">
      {runMode !== "build" && <GtImageToggle checked={props.judgeUseGtImages ?? false}
        disabled={suiteRunning || hasRunning} onChange={value => props.onJudgeUseGtImagesChange?.(value)} />}
      {runMode === "full" && (
        <label
          className={`btn danger delete-scope-toggle${deleteScopeAfterRun ? " active" : ""}${
            suiteRunning || hasRunning ? " disabled" : ""
          }`}
          title="只在完整链路测试结束后删除本次生成的相册，谨慎开启"
        >
          <input
            type="checkbox"
            checked={deleteScopeAfterRun}
            disabled={suiteRunning || hasRunning}
            onChange={(event) => onDeleteScopeAfterRunChange(event.target.checked)}
          />
          完成后删除相册
        </label>
      )}
      <button
        className="btn"
        type="button"
        disabled={Boolean(reason)}
        title={reason || undefined}
        onClick={onStart}
      >
        {buttonLabel}
      </button>
      <button className="btn warn" type="button" disabled={Boolean(props.stopping)} onClick={onStop}>
        停止全部
      </button>
      {reason && reason !== buttonLabel && <span className="config-help">{reason}</span>}
    </div>
  );
}
