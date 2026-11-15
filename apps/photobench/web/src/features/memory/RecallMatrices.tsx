/**
 * 「命中 × 回答结果分层矩阵」。逐行照搬 1.0 `App.vue:3224-3247`。
 *
 * 两张矩阵（工具召回命中 / 模型使用命中）由 `lib/graphs.ts:memoryRecallMatrices`
 * 移植，这里只消费产出。
 */
import { fmtPct } from "../../lib/format.ts";
import type { RecallMatrix } from "../../lib/graphs.ts";

/** 1.0 `:3243` 的三选一桥接文案。 */
const BRIDGE_LABELS = ["进入命中判定", "进入 Judge 评分", "核对最终素材交付"];

export interface RecallMatricesProps {
  /** = `memoryRecallMatrices(memoryEffectiveness)`；空数组时本节不渲染（1.0 `v-if`）。 */
  matrices: RecallMatrix[];
}

export function RecallMatrices({ matrices }: RecallMatricesProps) {
  if (!matrices.length) return null;
  return (
    <section className="memory-answer-attribution-panel">
      <div className="answer-correctness-head">
        <strong>命中 × 回答结果分层矩阵</strong>
        <span>
          工具召回命中 / 模型使用命中分开统计；只统计有 GT 媒体的可归因题目（{matrices[0]?.eligible} 题），数字由当前
          run 实时计算
        </span>
      </div>
      <div className="recall-matrices">
        {matrices.map((matrix) => (
          <div
            className={`answer-layer-architecture recall-layer-architecture recall-matrix recall-matrix-${matrix.key}`}
            key={matrix.key}
          >
            <div className="answer-layer-title">
              <strong>{matrix.title}</strong>
              <span>{matrix.note}</span>
            </div>
            {matrix.layers.map((layer, layerIndex) => (
              <div
                className={`answer-layer-row answer-layer-${layer.key.toLowerCase().replace("+", "")}`}
                key={`${matrix.key}-${layer.key}`}
              >
                <div className="answer-layer-label">
                  <b>{layer.key}</b>
                  <strong>{layer.name}</strong>
                  <small>{layer.stage}</small>
                </div>
                {layer.key === "L2" ? (
                  <div className="answer-layer-track answer-layer-branch-track">
                    {(layer.groups || []).map((group) => (
                      <div className="answer-layer-branch-group" key={group.label}>
                        <div className="answer-layer-branch-head">
                          <strong>{group.label}</strong>
                          <b>{fmtPct(group.total / matrix.eligible)}</b>
                          <small>
                            {group.total} 题 · {group.note}
                          </small>
                        </div>
                        <div className="answer-layer-branch-nodes">
                          {group.nodes.map((node) => (
                            <div
                              className="answer-layer-node"
                              key={`${matrix.key}-${group.label}-${node.label}`}
                            >
                              <small>{node.label}</small>
                              <b>{node.value}</b>
                              <span>{node.note}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="answer-layer-track">
                    {layer.nodes.map((node) => (
                      <div className="answer-layer-node" key={`${matrix.key}-${layer.key}-${node.label}`}>
                        <small>{node.label}</small>
                        <b>{node.value}</b>
                        <span>{node.note}</span>
                      </div>
                    ))}
                  </div>
                )}
                {layerIndex < matrix.layers.length - 1 ? (
                  <div className="answer-layer-bridge">
                    <i>↓</i>
                    <span>{BRIDGE_LABELS[layerIndex] ?? BRIDGE_LABELS[BRIDGE_LABELS.length - 1]}</span>
                  </div>
                ) : null}
              </div>
            ))}
            <p className="answer-layer-note">
              口径：{matrix.note}。L2 展示 Judge 评分；L3 按实际交付与同类型 GT 核对，不使用 Judge。
              正确交付表示至少交付一份对应素材，额外交付可与正确交付重叠；未评分题也纳入交付统计，不可回答题排除。
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
