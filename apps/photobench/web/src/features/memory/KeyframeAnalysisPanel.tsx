/**
 * 「关键帧有效性评估」8 格指标 + 指标定义。逐行照搬 1.0 `App.vue:3261-3282`，
 * 以及组件内常量 `keyframeMetricDefinitions`（1.0 `:2046-2054`，逐字照搬）。
 */
import type { KeyframeAnalysis } from "../../api/types.ts";
import { fmtPct, keyframeDeltaLabel, keyframeReasonLabel } from "../../lib/format.ts";

/** 1.0 `:2046-2054`。七条指标定义。 */
const keyframeMetricDefinitions = [
  { name: "保留率 / 压缩率", formula: "保留率 = 关键帧数 ÷ 原始视频帧数；压缩率 = 1 − 保留率", meaning: "衡量视频被压缩成关键帧后，计算量减少了多少；压缩越高不代表质量越高，必须结合父视频召回率观察。" },
  { name: "源视频父级召回", formula: "被候选或工具轨迹实际读取关键帧覆盖的 GT 源视频 ÷ GT 源视频目标", meaning: "回答需要某段视频时，检索或实际读取的关键帧是否指向了这段视频；允许使用关键帧而非原视频。" },
  { name: "候选关键帧精确率", formula: "与本题 GT 父视频一致的候选或实际读取关键帧 ÷ 去重后的关键帧总数", meaning: "合并检索候选与工具轨迹中实际读取的关键帧，反映视频问答实际使用证据的相关度。" },
  { name: "最终返回精确率", formula: "与本题 GT 父视频一致的最终关键帧 ÷ 最终返回关键帧总数", meaning: "模型实际用于回答的关键帧相关程度，反映重排和工具选择后的有效性。" },
  { name: "时间间隔 / 覆盖跨度", formula: "同一视频关键帧相邻时间差的均值 / 最早至最晚时间差的均值", meaning: "间隔反映采样密度，跨度反映单个视频被覆盖的时间范围；二者都不是答案准确率。" },
  { name: "回答质量差值", formula: "有关键帧候选题 Judge 均分 − 无关键帧候选题 Judge 均分", meaning: "用于观察关键帧与回答质量的关联；受题型、样本量和模型状态影响，不能单独视为因果结论。" },
  { name: "视频处理速度", formula: "原始视频总时长 ÷ 视频处理耗时总和（real-time factor）", meaning: "1.0× 表示处理 1 秒视频约需 1 秒；低于 1.0× 表示处理慢于实时。页面同时给出总耗时和每视频均值。" },
];

/** 1.0 `:2043-2045` 的 `keyframeReasonLabel` 已由 `lib/format.ts` 移植。 */
const secondsLabel = (value: unknown) =>
  value == null ? "-" : `${Number(value).toFixed(1)}s`;

const stageTimingLabels = [
  ["decode_seconds", "10fps 解码采样"],
  ["svd_dedup_seconds", "SVD 相似帧去重"],
  ["katna_seconds", "Katna 选帧"],
  ["scene_construction_seconds", "场景构建"],
] as const;

export interface KeyframeAnalysisPanelProps {
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  keyframeAnalysisLoading: boolean;
  keyframeAnalysisError: string;
}

export function KeyframeAnalysisPanel({
  keyframeAnalysis,
  keyframeAnalysisLoading,
  keyframeAnalysisError,
}: KeyframeAnalysisPanelProps) {
  const ready = keyframeAnalysis?.status === "ready";
  const assets = keyframeAnalysis?.assets;
  const retrieval = keyframeAnalysis?.retrieval;
  const temporal = keyframeAnalysis?.temporal;
  const usefulness = keyframeAnalysis?.usefulness;
  const processing = keyframeAnalysis?.processing;
  return (
    <section className="keyframe-analysis-panel">
      <div className="keyframe-analysis-head">
        <div>
          <strong>关键帧有效性评估</strong>
          <span>只保留关键帧规模、覆盖/检索、时间采样、回答对照、压缩率和视频处理速度</span>
          {keyframeAnalysis?.asset_source_note ? (
            <span className="keyframe-source-note">证据源：{keyframeAnalysis.asset_source_note}</span>
          ) : null}
        </div>
        {keyframeAnalysisLoading ? (
          <span className="phase-status running">分析中</span>
        ) : ready ? (
          <span className="phase-status completed">已计算</span>
        ) : null}
      </div>
      {keyframeAnalysisError ? (
        <p className="error">{keyframeAnalysisError}</p>
      ) : ready ? (
        <div className="keyframe-analysis-grid">
          <div>
            <small>关键帧规模</small>
            <b>{assets?.keyframes}</b>
            <span>
              {assets?.source_videos} 个源视频 · 平均 {assets?.keyframes_per_video_mean ?? "-"} 帧/视频
            </span>
          </div>
          <div>
            <small>源视频父级召回</small>
            <b>{fmtPct(retrieval?.video_parent_recall)}</b>
            <span>
              {retrieval?.video_gt_targets} 个 GT 视频目标中，{retrieval?.video_gt_targets_hit_by_keyframe} 个被关键帧覆盖
            </span>
          </div>
          <div>
            <small>候选关键帧精确率</small>
            <b>{fmtPct(retrieval?.candidate_precision)}</b>
            <span>
              {retrieval?.candidate_relevant_keyframes}/{retrieval?.candidate_keyframes} 个候选与 GT 父视频一致
            </span>
          </div>
          <div>
            <small>模型最终返回精确率</small>
            <b>{fmtPct(retrieval?.predicted_precision)}</b>
            <span>
              {retrieval?.predicted_relevant_keyframes}/{retrieval?.predicted_keyframes} 个返回关键帧有效
            </span>
          </div>
          <div>
            <small>时间采样</small>
            <b>{secondsLabel(temporal?.mean_gap_sec)}</b>
            <span>平均间隔 · 平均覆盖跨度 {secondsLabel(temporal?.mean_span_sec)}</span>
          </div>
          <div>
            <small>回答质量对照</small>
            <b>{keyframeDeltaLabel(usefulness?.answer_quality_delta)}</b>
            <span>
              有关键帧 {usefulness?.answer_quality_with_keyframe ?? "-"} · 无关键帧{" "}
              {usefulness?.answer_quality_without_keyframe ?? "-"}（非因果）
            </span>
          </div>
          <div>
            <small>全量帧保留 / 压缩</small>
            <b>
              {processing?.retained_frame_ratio == null
                ? "未保存原始帧"
                : `${fmtPct(processing.retained_frame_ratio)} / ${fmtPct(processing.compression_ratio)}`}
            </b>
            <span>
              {processing?.raw_frame_count
                ? `${String(processing.keyframe_count ?? assets?.keyframes)} / ${processing.raw_frame_count} 原始帧`
                : "复用记忆未重新执行视频处理，无法计算原始帧压缩率"}
            </span>
          </div>
          <div>
            <small>视频处理速度</small>
            <b>
              {processing?.realtime_factor == null
                ? "未记录"
                : `${Number(processing.realtime_factor).toFixed(3)}×`}
            </b>
            <span>
              {processing?.processing_seconds_total
                ? `总耗时 ${processing.processing_seconds_total}s · 均值 ${processing.processing_seconds_mean ?? "-"}s/视频`
                : "复用记忆没有视频处理耗时，不估算速度"}
            </span>
          </div>
          <div>
            <small>分段耗时</small>
            {stageTimingLabels.map(([key, label]) => {
              const timing = processing?.stage_timings?.[key];
              return (
                <span key={key}>
                  {label}：{timing?.total_seconds == null
                    ? "未记录"
                    : `${secondsLabel(timing.total_seconds)} 总计 · ${secondsLabel(timing.mean_seconds)}/视频（${timing.videos_measured ?? 0} 个）`}
                </span>
              );
            })}
            <span>场景构建含关键帧取图落库、事件、音视频对齐和记忆构建；总耗时还含视频探测等前置阶段。</span>
          </div>
        </div>
      ) : null}
      {ready ? (
        <details className="metric-definition-panel">
          <summary>指标定义、计算式与判读</summary>
          {keyframeMetricDefinitions.map((definition) => (
            <div className="metric-definition-row" key={definition.name}>
              <strong>{definition.name}</strong>
              <span>
                <b>计算式：</b>
                {definition.formula}
              </span>
              <span>
                <b>含义：</b>
                {definition.meaning}
              </span>
            </div>
          ))}
        </details>
      ) : null}
      {ready ? (
        <p className="keyframe-analysis-note">
          选择策略：{keyframeReasonLabel(temporal?.selection_reasons)} · 已覆盖 {temporal?.videos_sampled} 个视频、
          {usefulness?.event_context_count} 个事件上下文。父级召回衡量“关键帧能否代表源视频”，候选精确率衡量“是否混入无关帧”，两者应同时观察。
        </p>
      ) : !keyframeAnalysisLoading ? (
        <p className="keyframe-analysis-note">尚未取得完整 run 轨迹，暂不能计算关键帧有效性。</p>
      ) : null}
    </section>
  );
}
