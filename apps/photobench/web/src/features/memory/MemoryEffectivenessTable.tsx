/**
 * 「创建记忆 vs 测评覆盖」逐层表。逐行照搬 1.0 `App.vue:3173-3203`。
 *
 * `answer-correctness-panel`（`:3204`）与 `memory-answer-attribution-panel`（`:3224`）
 * 在 1.0 里**嵌在这个 `<section>` 内**（`:3204-3247` 直到 `:3248` 才闭合本节），
 * 所以它们由组合层作为 `children` 传进来，不另起一层 DOM。
 *
 * `memoryLayerSpecialMetrics`（1.0 `:177-206` 的组件内函数）没有落进 `lib/`，
 * 只服务本表「专项指标」一列，因此留在本文件内；1.0 读的
 * `memoryEffectiveness` / `keyframeAnalysis` 两个全局 ref 改成显式入参。
 */
import type { ReactNode } from "react";

import type { KeyframeAnalysis, MemoryEffectiveness, MemoryLevel } from "../../api/types.ts";
import { fmtPct } from "../../lib/format.ts";

export interface SpecialMetric {
  label: string;
  value: string;
  note: string;
}

/**
 * 1.0 `:177-206`。四层各一套专项指标；`L4+` 是兜底分支（`if (level.key === ...)` 落空）。
 * 1.0 的 `visual.total ? ... : "-"` / `level.created ? ... : "-"` 与 `|| 0` 回退逐条照搬。
 */
function memoryLayerSpecialMetrics(
  level: MemoryLevel,
  memoryEffectiveness: MemoryEffectiveness | null | undefined,
  keyframeAnalysis: KeyframeAnalysis | null | undefined,
): SpecialMetric[] {
  const analysis = keyframeAnalysis?.status === "ready" ? keyframeAnalysis : null;
  const details = memoryEffectiveness?.details || {};
  const quality = memoryEffectiveness?.data_quality || {};
  const correctness = details.answer_correctness || {};
  if (level.key === "L0") {
    const retrieval = analysis?.retrieval || {};
    return [
      {
        label: "关键帧父级召回",
        value: retrieval.video_parent_recall == null ? "未计算" : fmtPct(retrieval.video_parent_recall),
        note: analysis
          ? `${retrieval.video_gt_targets_hit_by_keyframe || 0}/${retrieval.video_gt_targets || 0} 个源视频被覆盖`
          : "关键帧接口尚未返回",
      },
      {
        label: "候选 / 最终精确率",
        value:
          retrieval.candidate_precision == null
            ? "未计算"
            : `${fmtPct(retrieval.candidate_precision)} / ${fmtPct(retrieval.predicted_precision)}`,
        note: analysis
          ? `候选 ${retrieval.candidate_relevant_keyframes || 0}/${retrieval.candidate_keyframes || 0} · 最终 ${retrieval.predicted_relevant_keyframes || 0}/${retrieval.predicted_keyframes || 0}`
          : "候选池 / 最终回答返回",
      },
      {
        label: "关键帧压缩率",
        value: analysis?.processing?.compression_ratio == null ? "未保存原始帧" : fmtPct(analysis.processing.compression_ratio),
        note: analysis?.processing?.raw_frame_count
          ? `${analysis.assets?.keyframes}/${analysis.processing.raw_frame_count} 原始帧`
          : "本次为复用记忆，未保存原始帧总数",
      },
    ];
  }
  if (level.key === "L1") {
    const visual = details.visual_calls || {};
    const ocr = details.ocr_calls || {};
    return [
      {
        label: "视觉解析成功率",
        value: visual.total ? fmtPct(Number(visual.effective) / Number(visual.total)) : "-",
        note: `${visual.effective || 0}/${visual.total || 0} 次 inspect_photo 成功`,
      },
      {
        label: "OCR 成功率",
        value: ocr.total ? fmtPct(Number(ocr.effective) / Number(ocr.total)) : "-",
        note: `${ocr.effective || 0}/${ocr.total || 0} 次 read_photo_text 成功`,
      },
      {
        label: "图谱加载",
        value: quality.graph_ready ? "已校验" : "不完整",
        note: `${quality.preview_count || 0} 条媒体预览 · ${quality.event_context_count || 0} 个事件上下文`,
      },
    ];
  }
  if (level.key === "L2") {
    return [
      {
        label: "事件图谱覆盖率",
        value: level.created ? fmtPct(Number(level.effective) / Number(level.created)) : "-",
        note: `${level.effective}/${level.created} 个事件在工具图谱预览出现；不是事件事实正确率`,
      },
      {
        label: "跨媒体关联",
        value: quality.graph_ready ? "可追溯" : "待校验",
        note: "事件 ID → 媒体引用 → 时间/地点字段",
      },
    ];
  }
  if (level.key === "L3") {
    const status = details.requirement_status_counts || {};
    return [
      {
        label: "高层证据闭合率（代理）",
        value: level.created ? fmtPct(Number(level.effective) / Number(level.created)) : "-",
        note: `${status.satisfied || 0} satisfied / ${level.created} 项证据需求；不是回答正确率`,
      },
      {
        label: "未闭合需求",
        value: `${(status.open || 0) + (status.running || 0) + (status.failed || 0)}`,
        note: `open ${status.open || 0} · running ${status.running || 0} · failed ${status.failed || 0}`,
      },
    ];
  }
  return [
    {
      label: "Agent 完整闭环率",
      value: level.created ? fmtPct(Number(level.effective) / Number(level.created)) : "-",
      note: `${level.effective}/${level.created} 条 complete`,
    },
    {
      label: "回答 Exact Accuracy",
      value: fmtPct(correctness.exact_accuracy),
      note: `正确 ${correctness.correct || 0} · 部分 ${correctness.partial || 0} · 错误 ${correctness.incorrect || 0}`,
    },
  ];
}

export interface MemoryEffectivenessTableProps {
  memoryEffectiveness: MemoryEffectiveness | null | undefined;
  /** `memoryLayerSpecialMetrics` 的 L0 分支要用。 */
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  /** 1.0 嵌在本节内的正确性对比与命中矩阵（`:3204-3247`）。 */
  children?: ReactNode;
}

export function MemoryEffectivenessTable({
  memoryEffectiveness,
  keyframeAnalysis,
  children,
}: MemoryEffectivenessTableProps) {
  const ready = memoryEffectiveness?.status === "ready";
  const quality = memoryEffectiveness?.data_quality;
  return (
    <section className="memory-effectiveness-panel">
      <div className="memory-effectiveness-head">
        <div>
          <strong>创建记忆 vs 测评覆盖（工程指标，非正确率）</strong>
          <span>这张表只说明数据是否被工具返回、图谱是否挂接、证据需求是否闭合；不能直接代表记忆正确或回答正确。</span>
        </div>
        {quality?.graph_ready ? (
          <span className="phase-status completed">工具 / 图谱已校验</span>
        ) : ready ? (
          <span className="phase-status failed">数据不完整</span>
        ) : (
          <span className="phase-status pending">计算中</span>
        )}
      </div>
      {ready ? (
        <div className="memory-effect-quality">
          <span>工具轨迹 {quality?.tool_trace_count} 条</span>
          <span>成功 {quality?.tool_success_count} 条</span>
          <span>媒体预览 {quality?.preview_count} 条</span>
          <span>事件上下文 {quality?.event_context_count} 个</span>
        </div>
      ) : null}
      {ready && quality?.note ? <p className="muted">{quality.note}</p> : null}
      {ready ? (
        <div className="memory-effect-table-wrap">
          <table className="memory-effect-table">
            <thead>
              <tr>
                <th>层级</th>
                <th>创建 / 可用基数</th>
                <th>评测轨迹覆盖</th>
                <th>未覆盖 / 未闭合</th>
                <th>覆盖率（非正确率）</th>
                <th>专项指标（正确性/可用性）</th>
                <th>判定口径</th>
              </tr>
            </thead>
            <tbody>
              {(memoryEffectiveness?.levels || []).map((level) => (
                <tr key={level.key}>
                  <td>
                    <b>{level.key}</b>
                    <strong>{level.name}</strong>
                  </td>
                  <td>
                    <b>{level.created}</b>
                    <small>{level.created_detail}</small>
                  </td>
                  <td className="effect-good">
                    <b>{level.effective}</b>
                    <small>{level.effective_detail}</small>
                  </td>
                  <td className="effect-bad">
                    <b>{level.ineffective}</b>
                    <small>{level.ineffective_detail}</small>
                    {level.ineffective_reasons?.length ? (
                      <ul className="memory-invalid-reasons">
                        {level.ineffective_reasons.map((reason) => (
                          <li key={`${level.key}-${reason.label}`}>
                            <strong>
                              {reason.label}：{reason.count}
                            </strong>
                            <span>{reason.meaning}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </td>
                  <td>
                    <b>{fmtPct(level.rate)}</b>
                    <div className="memory-effect-bar">
                      <i style={{ width: `${Math.min(100, Math.max(0, Number(level.rate || 0) * 100))}%` }}></i>
                    </div>
                  </td>
                  <td>
                    {memoryLayerSpecialMetrics(level, memoryEffectiveness, keyframeAnalysis).map((metric) => (
                      <div key={metric.label} className="memory-special-metric">
                        <b>
                          {metric.label}：{metric.value}
                        </b>
                        <small>{metric.note}</small>
                      </div>
                    ))}
                  </td>
                  <td>
                    <small>创建 → 生效：{level.name} 下游实际可追溯引用</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {ready ? (
        <p className="memory-effect-note">
          口径边界：L0/L1/L2/L3 的数字是工程覆盖、工具可用或证据闭合代理指标，不是语义正确率；只有关键帧相对 GT
          的召回/精确率和最终回答 Judge
          结果可以作为正确性依据。真正的“记忆是否导致答错”见下方归因矩阵。
        </p>
      ) : memoryEffectiveness?.error ? (
        <p className="error">{memoryEffectiveness.error}</p>
      ) : null}
      {children}
    </section>
  );
}
