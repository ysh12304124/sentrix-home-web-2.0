/**
 * 「最终回答正确性对比」+「记忆有效性分层指标」。逐行照搬 1.0 `App.vue:3204-3223`。
 *
 * `memoryValidityArchitecture`（1.0 `:32-87` 的 computed）已由 `lib/graphs.ts` 移植，
 * 这里只消费它的产出（`layers`），不在组件内重算。
 */
import { scoreChoices } from "../analysis/presentation.ts";
import type { MemoryEffectiveness } from "../../api/types.ts";
import { fmtPct } from "../../lib/format.ts";
import type { ValidityLayer } from "../../lib/graphs.ts";

/** 1.0 `:3219` 的桥接文案四选一（按下标，不是按图层 key）。 */
const BRIDGE_LABELS = ["解析并形成场景记忆", "聚合为事件记忆", "形成可引用证据", "生成并判定回答"];

export interface AnswerCorrectnessPanelProps {
  memoryEffectiveness: MemoryEffectiveness | null | undefined;
  /** = `memoryValidityArchitecture({ memoryEffectiveness, keyframeAnalysis, run, summary })`。 */
  validityLayers: ValidityLayer[];
}

export function AnswerCorrectnessPanel({ memoryEffectiveness, validityLayers }: AnswerCorrectnessPanelProps) {
  const correctness = memoryEffectiveness?.details?.answer_correctness;
  const choices = scoreChoices({evaluation_summary: correctness?.evaluation_summary});
  const label = (value: string) => choices.find(s => s.value === value)?.label;
  const created = memoryEffectiveness?.levels?.find((level) => level.key === "L4+")?.created || 0;
  return (
    <section className="answer-correctness-panel">
      <div className="answer-correctness-head">
        <strong>最终回答正确性对比</strong>
        <span>按 Judge 评分统计，不等同于工具调用成功率</span>
      </div>
      <div className="answer-correctness-grid">
        <div className="answer-correct answer-correct-good">
          <small>{label("2")}</small>
          <b>{correctness?.correct}</b>
          <span>
            {fmtPct(correctness?.exact_accuracy)} · 共 {created} 题
          </span>
        </div>
        {label("1") && <div className="answer-correct answer-correct-partial">
          <small>{label("1")}</small>
          <b>{correctness?.partial}</b>
          <span>{fmtPct((correctness?.partial || 0) / (created || 1))}</span>
        </div>}
        <div className="answer-correct answer-correct-bad">
          <small>{label("0")}</small>
          <b>{correctness?.incorrect}</b>
          <span>{fmtPct((correctness?.incorrect || 0) / (created || 1))}</span>
        </div>
        <div className="answer-correct answer-correct-na">
          <small>未评分</small>
          <b>{correctness?.unjudged}</b>
          <span>不纳入正确率</span>
        </div>
      </div>
      <div className="answer-layer-architecture memory-validity-architecture">
        <div className="answer-layer-title">
          <strong>记忆有效性分层指标</strong>
          <span>原始媒体与关键帧从 L0 逐层进入最终回答</span>
        </div>
        {validityLayers.map((layer, layerIndex) => (
          <div
            className={`answer-layer-row answer-layer-${layer.key.toLowerCase().replace("+", "")}`}
            key={layer.key}
          >
            <div className="answer-layer-label">
              <b>{layer.key}</b>
              <strong>{layer.name}</strong>
              <small>{layer.stage}</small>
            </div>
            <div className="answer-layer-track">
              {layer.nodes.map((node) => (
                <div className="answer-layer-node" key={`${layer.key}-${node.label}`}>
                  <small>{node.label}</small>
                  <b>{node.value}</b>
                  <span>{node.note}</span>
                </div>
              ))}
            </div>
            {layerIndex < validityLayers.length - 1 ? (
              <div className="answer-layer-bridge">
                <i>↓</i>
                <span>{BRIDGE_LABELS[layerIndex] ?? BRIDGE_LABELS[BRIDGE_LABELS.length - 1]}</span>
              </div>
            ) : null}
          </div>
        ))}
        <p className="answer-layer-note">
          口径：L0 的创建量只是记忆库规模；原始图片用测评 Recall，关键帧用“父级视频 Recall / 返回关键帧
          Precision”。L1–L3 是测评期间的解析、图谱和证据链覆盖代理，只有 L4+ 的 Exact Accuracy
          才是最终回答正确率。
        </p>
      </div>
    </section>
  );
}
