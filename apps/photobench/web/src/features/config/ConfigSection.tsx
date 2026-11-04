/**
 * 评测配置区外壳。逐行移植 1.0 `App.vue:2861-2987` 的
 * `<section class="section config-section">`。
 *
 * 拆成四块：本文件（外壳 + 任务范围 + 评测服务）、`ReuseBasePicker`、`ModelServicePanel`、
 * `StartActions`。**1.0 的 DOM 层级保持不变**（`.config-groups` → `.config-group` →
 * `.config-grid`），因为 `photobench.css` 的选择器是逐行移植来的，层级一变样式就散。
 *
 * 状态全部来自 hooks（`useConfig` / `useModelEndpoint` / `useReuseMode`）与组合层持有的
 * `selectedAlbum` / `selectedQa` / `selectedModels` / 启动动作；这里只做展示与转发，
 * **不发任何请求**。少了 1.0 `:2845-2856` 的 arbiter 面板——端点在 S9 已删，是死 UI
 * （`docs/refactor/s10-deviations.md` §3）。
 */
import type { useConfig } from "../../hooks/useConfig.ts";
import type { useReuseMode } from "../../hooks/useReuseMode.ts";
import type { Run } from "../../api/types.ts";
import { ModelAdvancedPanel, ModelServicePanel, type CloudModelProfile, type ModelServicePanelProps } from "./ModelServicePanel.tsx";
import { ReuseBasePicker } from "./ReuseBasePicker.tsx";
import { StartActions, type StartActionsProps } from "./StartActions.tsx";
import { filterGraphBaselineRuns } from "./baselineRuns.ts";
import { albumCountLabel, qaOptionsFor, RUN_MODES_UI, runModeMeta } from "./rules.ts";

type ConfigApi = ReturnType<typeof useConfig>;
type ReuseApi = ReturnType<typeof useReuseMode>;

/** 1.0 `selectedAlbum` / `selectedQa` 两个 ref（`:223` / `:229`）不在三个 hook 里，由组合层注入。 */
export interface ConfigAlbumProps {
  selectedAlbum: string;
  setSelectedAlbum: (album: string) => void;
  selectedQa: string;
  setSelectedQa: (qa: string) => void;
}

export interface ConfigSectionProps {
  connection: Pick<
    ConfigApi,
    | "config"
    | "manifests"
    | "sentrixUrl"
    | "setSentrixUrl"
    | "judgeUrl"
    | "setJudgeUrl"
    | "judgeModel"
    | "setJudgeModel"
    | "judgeApiKey"
    | "setJudgeApiKey"
    | "setJudgeApiKeyDirty"
    | "markConnectionConfigDirty"
    | "connectionConfigState"
    | "connectionConfigMessage"
    | "saveConnectionConfig"
  >;
  /**
   * 模型服务面板的输入。`cloudModelProfile` 由本组件从 `config` 推出来再注入
   * （1.0 `:17-20` 的兜底默认在这个组件里），所以组合层不用管它。
   */
  model: Omit<ModelServicePanelProps, "cloudModelProfile">;
  reuse: Pick<
    ReuseApi,
    | "runMode"
    | "onModeChange"
    | "reuseBases"
    | "selectedReuseBaseId"
    | "setSelectedReuseBaseId"
    | "memorySpacesLoading"
    | "scopeAlbumHint"
    | "existingScopeId"
  >;
  album: ConfigAlbumProps;
  start: StartActionsProps;
  /** 当前已加载的运行记录，用于在配置区直接选择匹配的无图基线。 */
  baselineRuns?: Run[];
  /** 1.0 `error`（`:286`）。 */
  error: string;
}

/** `runtime_config` 里评测服务那一格读到的两个键（1.0 `:2909`）。用 `type` 以便从 `Record<string, unknown>` 断言。 */
type JudgeApiKeyState = {
  judge_api_key_set?: unknown;
  judge_api_key_hint?: unknown;
};

export function ConfigSection({
  connection,
  model,
  reuse,
  album,
  start,
  baselineRuns = [],
  error,
}: ConfigSectionProps) {
  const meta = runModeMeta(reuse.runMode);
  const qaOptions = qaOptionsFor(connection.manifests, album.selectedAlbum);
  const graphEnabled = start.graphEnabled ?? true;
  const baselineCandidates = filterGraphBaselineRuns(baselineRuns, {
    albumId: album.selectedAlbum,
    qaSet: album.selectedQa,
    modelName: model.selectedEndpointModel,
  });

  const baselineLabel = (run: Run): string => {
    const finished = run.finished_at
      ? new Date(run.finished_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      : "时间未知";
    const count = run.qa_count ?? run.item_count;
    const recall = run.summary?.media_retrieval_recall_macro ?? run.summary?.retrieval_recall_macro;
    const recallLabel = typeof recall === "number" ? ` · 媒体召回 ${(recall * 100).toFixed(1)}%` : "";
    return `${run.run_id} · ${finished} · ${count ?? "?"} 题${recallLabel}`;
  };

  // 1.0 `:17-20`：`config?.cloud_model_profile || {...}`（整对象兜底，不逐字段补）。
  const cloudModelProfile: CloudModelProfile = connection.config?.cloud_model_profile
    ? (connection.config.cloud_model_profile as CloudModelProfile)
    : {
        id: "big_model",
        label: "big_model（云端 API）",
        source: "cloud_api",
        available: true,
        model: "deepseek-v4.1-flash",
      };

  const runtimeConfig = (connection.config?.runtime_config || {}) as JudgeApiKeyState;

  // 1.0 `:2866`：保存时模型服务三个字段由 `useModelEndpoint` 持有，这里读出来一起提交，
  // 保存成功后的回写仍走那个 hook 的 `applySavedConnection`（1.0 `:2448-2459`）。
  const onSaveConnection = () => {
    void connection.saveConnectionConfig(
      {
        vllmManagerUrl: model.vllmManagerUrl,
        modelEndpoint: model.modelEndpoint,
        endpointModel: model.selectedEndpointModel,
      },
      { onModelConfigSaved: model.applySavedConnection },
    );
  };

  const albumLabel =
    connection.manifests.find((manifest) => manifest.album_id === album.selectedAlbum)?.album_name ||
    album.selectedAlbum ||
    "未选择";
  const modelLabel =
    model.selectedEndpointModel ||
    [...model.selectedModels].filter((id) => id !== "__current__").join(" / ") ||
    "未选择";
  const judgeLabel = connection.judgeModel.trim() || "未配置";
  const qaLabel = reuse.runMode === "build" ? "构建相册，不跑 QA" : album.selectedQa || "未选择";

  return (
    <section className="section config-section">
      <div className="section-head">
        <div className="section-title">
          <h2>评测配置</h2>
          <p>先选任务范围与模型，启动评测；评测服务、模型管理器和认证参数放在高级设置里。</p>
        </div>
        <div className="section-head-actions">
          {start.hasRunning && <span className="live-badge">实时更新中</span>}
          <button
            className="btn ghost compact"
            type="button"
            disabled={connection.connectionConfigState === "saving"}
            onClick={onSaveConnection}
          >
            {connection.connectionConfigState === "saving" ? "保存中…" : "保存测评配置"}
          </button>
          <span className={`config-save-status state-${connection.connectionConfigState}`}>
            {connection.connectionConfigMessage}
          </span>
        </div>
      </div>

      <div className="config-overview" aria-label="本次评测摘要">
        <span className="config-overview-item"><b>模式</b>{meta.label}</span>
        <span className="config-overview-item"><b>{reuse.runMode === "reuse" ? "复用相册" : "照片"}</b>{reuse.runMode === "reuse" ? reuse.selectedReuseBaseId || "未选择" : albumLabel}</span>
        <span className="config-overview-item"><b>QA</b>{qaLabel}</span>
        <span className="config-overview-item"><b>模型</b>{modelLabel}</span>
        <span className="config-overview-item"><b>Judge</b>{judgeLabel}</span>
      </div>

      <div className="config-core-grid">
        <div className="config-group">
          <div className="config-group-head">
            <div>
              <strong>1. 任务范围</strong>
              <span>选择本次要构建或测评的数据范围</span>
            </div>
          </div>
          <div className="config-grid config-grid-scope">
            <label>
              工作模式
              <select
                value={reuse.runMode}
                disabled={start.suiteRunning || start.hasRunning}
                onChange={(event) => reuse.onModeChange(event.target.value)}
              >
                {RUN_MODES_UI.map((mode) => (
                  <option key={mode.id} value={mode.id}>
                    {mode.label}
                  </option>
                ))}
              </select>
              <span className="config-help mode-hint">{meta.hint}</span>
            </label>
            {reuse.runMode !== "reuse" && (
              <label>
                照片
                <select
                  value={album.selectedAlbum}
                  onChange={(event) => album.setSelectedAlbum(event.target.value)}
                >
                  {connection.manifests.map((manifest) => (
                    <option key={manifest.album_id} value={manifest.album_id}>
                      {manifest.album_name} ({albumCountLabel(manifest)})
                    </option>
                  ))}
                </select>
                <span className="config-help">
                  {reuse.runMode === "build"
                    ? "本次要导入的照片、视频范围；处理后相册保留。"
                    : "本次要导入的照片、视频范围；相册越大，准备越久。"}
                </span>
              </label>
            )}
            {reuse.runMode === "reuse" && (
              <ReuseBasePicker
                reuseBases={reuse.reuseBases}
                selectedReuseBaseId={reuse.selectedReuseBaseId}
                onSelectReuseBase={reuse.setSelectedReuseBaseId}
                memorySpacesLoading={reuse.memorySpacesLoading}
                scopeAlbumHint={reuse.scopeAlbumHint}
              />
            )}
            {reuse.runMode !== "build" && (
              <label>
                QA 数据集
                <select
                  value={album.selectedQa}
                  onChange={(event) => album.setSelectedQa(event.target.value)}
                >
                  {qaOptions.map((qa) => (
                    <option key={qa} value={qa}>
                      {qa}
                    </option>
                  ))}
                </select>
                <span className="config-help">
                  {reuse.runMode === "reuse"
                    ? "题目与对照随现有相册匹配；这里选择题量。"
                    : "从当前相册的题目集中选题；10 题适合快速验证。"}
                </span>
              </label>
            )}
            {reuse.runMode !== "build" && (
              <label className="config-checkbox-label">
                <span>图记忆检索</span>
                <span>
                  <input
                    type="checkbox"
                    checked={graphEnabled}
                    disabled={start.suiteRunning || start.hasRunning}
                    onChange={(event) => start.onGraphEnabledChange?.(event.target.checked)}
                  />
                  启用图检索
                </span>
                <span className="config-help">
                  关闭后会跑同一题集的无图对照；有图 run 可填写对应的无图 run ID。
                </span>
              </label>
            )}
            {reuse.runMode !== "build" && graphEnabled && (
              <>
                <label>
                  无图基线 run
                  <select
                    value={start.graphBaselineRunId ?? ""}
                    disabled={start.suiteRunning || start.hasRunning}
                    onChange={(event) => start.onGraphBaselineRunIdChange?.(event.target.value)}
                  >
                    <option value="">
                      {baselineCandidates.length > 0 ? "请选择同配置的无图测评" : "暂无匹配的已完成无图测评"}
                    </option>
                    {baselineCandidates.map((run) => (
                      <option key={run.run_id} value={run.run_id}>
                        {baselineLabel(run)}
                      </option>
                    ))}
                  </select>
                  <span className="config-help">
                    已按当前相册、QA 数据集和模型过滤，只显示已完成且关闭图检索的测评。
                  </span>
                </label>
                <label>
                  或手动输入无图对照 run ID（可选）
                <input
                  type="text"
                  value={start.graphBaselineRunId ?? ""}
                  disabled={start.suiteRunning || start.hasRunning}
                  placeholder="先跑无图，再把 run_id 填到这里"
                  onChange={(event) => start.onGraphBaselineRunIdChange?.(event.target.value)}
                />
                <span className="config-help">
                  只有关联 graph_enabled=false 的完整 run，才会显示最终媒体召回率的无图→有图变化。
                </span>
                </label>
              </>
            )}
          </div>
        </div>

        <ModelServicePanel {...model} cloudModelProfile={cloudModelProfile} />
      </div>

      <details className="config-advanced">
        <summary>
          <span>
            <strong>高级设置</strong>
            <small>评测服务、Judge 认证、模型管理器和云端模型</small>
          </span>
          <span className="config-advanced-hint">默认收起</span>
        </summary>
        <div className="config-advanced-body">
          <div className="config-group">
            <div className="config-group-head">
              <div>
                <strong>2. 评测服务</strong>
                <span>Sentrix 后端与 Judge 评分服务；本地评测通常保持默认</span>
              </div>
            </div>
            <div className="config-grid config-grid-judge">
              <label>
                Sentrix 后端
                <input
                  type="text"
                  value={connection.sentrixUrl}
                  placeholder="例如 127.0.0.1:8291"
                  onChange={(event) => {
                    connection.setSentrixUrl(event.target.value);
                    connection.markConnectionConfigDirty();
                  }}
                />
              </label>
              <label>
                Judge 服务
                <input
                  type="text"
                  value={connection.judgeUrl}
                  placeholder="例如 192.168.1.65:1234/v1"
                  onChange={(event) => {
                    connection.setJudgeUrl(event.target.value);
                    connection.markConnectionConfigDirty();
                  }}
                />
              </label>
              <label>
                Judge 模型
                <input
                  type="text"
                  value={connection.judgeModel}
                  placeholder="例如 doubao-seed-2.1-lite"
                  onChange={(event) => {
                    connection.setJudgeModel(event.target.value);
                    connection.markConnectionConfigDirty();
                  }}
                />
                <span className="config-help">Judge 请求使用此 model 字段。</span>
              </label>
              <label>
                Judge API key
                <input
                  type="password"
                  autoComplete="new-password"
                  value={connection.judgeApiKey}
                  placeholder="留空表示不使用或保留已保存值"
                  onChange={(event) => {
                    connection.setJudgeApiKey(event.target.value);
                    connection.setJudgeApiKeyDirty(true);
                    connection.markConnectionConfigDirty();
                  }}
                />
                <span className="config-help">
                  仅保存到评测编排器本机环境变量；
                  {runtimeConfig.judge_api_key_set
                    ? `已配置（${String(runtimeConfig.judge_api_key_hint)}）`
                    : "当前未配置"}
                  。
                </span>
              </label>
            </div>
          </div>
          <ModelAdvancedPanel {...model} cloudModelProfile={cloudModelProfile} />
        </div>
      </details>

      <StartActions {...start} />
      {error && <p className="error">{error}</p>}
    </section>
  );
}
