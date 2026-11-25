/**
 * 「记忆层级与有效证据链」面板外壳 + 链路 tab。逐行照搬 1.0 `App.vue:3147-3284` 的
 * `<details class="memory-layer-panel">` 与 `:3152-3156` 的 `memory-chain-tabs`。
 *
 * 数据全部来自 props（组合层传入），组件内不发请求；`chainMode` 是纯 UI 状态，
 * 留在本组件内（1.0 的 `chainMode` ref `:280`）。
 *
 * 本节内部顺序（1.0 逐行）：
 *   树状图 `:3157` → 「创建 vs 覆盖」表 `:3173`（正确性对比 `:3204` 与命中矩阵 `:3224`
 *   **嵌在它内部**）→ 字段证据明细图 `:3249` → 关键帧面板 `:3261` → 脚注 `:3283`。
 */
import { useMemo, useState } from "react";

import type {
  KeyframeAnalysis,
  ManifestSummary,
  MemoryEffectiveness,
  Run,
  RunItemSummary,
  RunSummary,
} from "../../api/types.ts";
import {
  activeEvidenceGraphView,
  effectiveEvidenceGraph,
  memoryEvidenceGraph,
  memoryLayerMetrics,
  memoryRecallMatrices,
  memoryValidityArchitecture,
} from "../../lib/graphs.ts";
import type { ChainDetail, ChainMedia, GraphLayer, GraphNode, QaDetailMap } from "../../lib/graphs.ts";

import { AnswerCorrectnessPanel } from "./AnswerCorrectnessPanel.tsx";
import { ChainDetailModal } from "./ChainDetailModal.tsx";
import { EvidenceTree } from "./EvidenceTree.tsx";
import { MemoryEffectivenessTable } from "./MemoryEffectivenessTable.tsx";
import { RecallMatrices } from "./RecallMatrices.tsx";

export interface MemoryLayerPanelProps {
  run: Run | null | undefined;
  manifests: readonly ManifestSummary[];
  qaDetails: QaDetailMap;
  qaPageItems: readonly RunItemSummary[];
  memoryEffectiveness: MemoryEffectiveness | null | undefined;
  keyframeAnalysis: KeyframeAnalysis | null | undefined;
  keyframeAnalysisLoading?: boolean;
  keyframeAnalysisError?: string;
  /** = `effectiveRunSummary(run)`。 */
  summary: RunSummary | null | undefined;
  /** 1.0 `chainDetail` ref（`:281`）；由组合层在点开节点后置位。 */
  chainDetail: ChainDetail | null;
  /** 1.0 `openChainNode(chainMode, layer, node)` 的取数半留在组合层。 */
  onOpenChainNode: (mode: string, layer: GraphLayer, node: GraphNode) => void;
  onCloseChainDetail: () => void;
  /** 1.0 `openImage(media)`（lightbox 归另一切片）。 */
  onOpenImage?: (media: ChainMedia) => void;
  onExpandedChange?: (open: boolean) => void;
}

export function MemoryLayerPanel({
  run,
  manifests,
  qaDetails,
  qaPageItems,
  memoryEffectiveness,
  keyframeAnalysis,
  summary,
  chainDetail,
  onOpenChainNode,
  onCloseChainDetail,
  onOpenImage,
  onExpandedChange,
}: MemoryLayerPanelProps) {
  const [chainMode, setChainMode] = useState("creation");

  const view = useMemo(
    () =>
      memoryLayerMetrics({
        run,
        manifests,
        qaDetails,
        qaPageItems,
        memoryEffectiveness,
        keyframeAnalysis,
        summary,
      }),
    [run, manifests, qaDetails, qaPageItems, memoryEffectiveness, keyframeAnalysis, summary],
  );
  const memoryGraph = useMemo(
    () => memoryEvidenceGraph({ run, qaDetails, keyframeAnalysis, view, summary }),
    [run, qaDetails, keyframeAnalysis, view, summary],
  );
  // 1.0 `:1956`：`computed(effectiveEvidenceGraph)`，函数内部取「当前展开的第一条 QA」。
  const effectiveGraph = useMemo(
    () => effectiveEvidenceGraph(Object.values(qaDetails).find(Boolean) || null),
    [qaDetails],
  );
  const activeView = activeEvidenceGraphView(chainMode, memoryGraph, effectiveGraph);
  const validityLayers = useMemo(
    () => memoryValidityArchitecture({ memoryEffectiveness, keyframeAnalysis, run, summary }),
    [memoryEffectiveness, keyframeAnalysis, run, summary],
  );
  const matrices = useMemo(() => memoryRecallMatrices(memoryEffectiveness), [memoryEffectiveness]);

  const ready = memoryEffectiveness?.status === "ready";
  const correctness = memoryEffectiveness?.details?.answer_correctness;

  return (
    <details className="memory-layer-panel" onToggle={(event) => onExpandedChange?.(event.currentTarget.open)}>
      <summary className="memory-layer-head">
        <div>
          <h3>记忆层级与有效证据链</h3>
          <p>分别查看“原始创建链路”和“测评时生效链路”。图中节点都绑定实际字段；点击节点可查看图片、语句、工具记录和已保存的执行轨迹。</p>
        </div>
        <span className="layer-scope-badge">通用层级 · 时间 / 事件 / 地点 / 人物 / 场景</span>
      </summary>
      <div className="memory-chain-tabs" role="tablist" aria-label="证据链类型">
        <button
          type="button"
          role="tab"
          aria-selected={chainMode === "creation"}
          className={chainMode === "creation" ? "active" : undefined}
          onClick={() => setChainMode("creation")}
        >
          原始创建链路
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={chainMode === "effective"}
          className={chainMode === "effective" ? "active" : undefined}
          onClick={() => setChainMode("effective")}
        >
          测评时生效链路
        </button>
        <span>点击节点查看具体图、语句、工具调用和保存的过程记录</span>
      </div>
      <EvidenceTree layers={activeView} onOpenNode={(layer, node) => onOpenChainNode(chainMode, layer, node)} />
      <MemoryEffectivenessTable memoryEffectiveness={memoryEffectiveness} keyframeAnalysis={keyframeAnalysis}>
        {ready && correctness ? (
          <AnswerCorrectnessPanel memoryEffectiveness={memoryEffectiveness} validityLayers={validityLayers} />
        ) : null}
        <RecallMatrices matrices={matrices} />
      </MemoryEffectivenessTable>
      <EvidenceTree
        variant="graph"
        layers={activeView}
        onOpenNode={(layer, node) => onOpenChainNode(chainMode, layer, node)}
      />
      <p className="memory-layer-footnote">
        指标口径：运行级指标来自本次 run 的汇总轨迹；“已展开轨迹”只对当前打开的 QA 逐题核验。这样可以直接定位是 L0
        媒体、L1 解析、L2 事件聚合、L3 目标分解还是 L4+ 回答阶段出了问题。
      </p>
      <ChainDetailModal chainDetail={chainDetail} onClose={onCloseChainDetail} onOpenImage={onOpenImage} />
    </details>
  );
}
