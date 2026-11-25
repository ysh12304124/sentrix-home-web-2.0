import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import type { Run } from "../../api/types.ts";

import { MemoryLayerPanel } from "./MemoryLayerPanel.tsx";
import type { MemoryLayerPanelProps } from "./MemoryLayerPanel.tsx";

// 1.0 参考 `App.vue:3147-3284`：面板外壳、`chainMode` 两个 tab、树状图节点点击
// （`openChainNode(chainMode, layer, node)`）。两种链路口径必须给出**不同**的树。
afterEach(cleanup);

interface Bound {
  props: MemoryLayerPanelProps;
  onOpenChainNode: Mock<MemoryLayerPanelProps["onOpenChainNode"]>;
  onCloseChainDetail: Mock<() => void>;
}

function bind(overrides: Partial<MemoryLayerPanelProps> = {}): Bound {
  const onOpenChainNode = vi.fn<MemoryLayerPanelProps["onOpenChainNode"]>();
  const onCloseChainDetail = vi.fn<() => void>();
  const base: Omit<MemoryLayerPanelProps, "onOpenChainNode" | "onCloseChainDetail"> = {
    run: { run_id: "r1", album_id: "album3" } as Run,
    manifests: [],
    qaDetails: {},
    qaPageItems: [],
    memoryEffectiveness: {
      status: "ready",
      levels: [],
      details: { answer_correctness: { correct: 1, partial: 0, incorrect: 0, unjudged: 0 } },
    },
    keyframeAnalysis: null,
    summary: {},
    chainDetail: null,
  };
  return { props: { ...base, ...overrides, onOpenChainNode, onCloseChainDetail }, onOpenChainNode, onCloseChainDetail };
}

describe("MemoryLayerPanel · 外壳与 tab（1.0 :3147-3156）", () => {
  it("渲染面板标题、层级徽标与两个 tab，默认选中「原始创建链路」", () => {
    const { props } = bind();
    const { container } = render(<MemoryLayerPanel {...props} />);

    expect(container.querySelector("details.memory-layer-panel")).not.toBeNull();
    expect(screen.getByText("记忆层级与有效证据链")).not.toBeNull();
    expect(screen.getByText("通用层级 · 时间 / 事件 / 地点 / 人物 / 场景")).not.toBeNull();
    expect(screen.getByText("点击节点查看具体图、语句、工具调用和保存的过程记录")).not.toBeNull();

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["原始创建链路", "测评时生效链路"]);
    expect(tabs[0]?.getAttribute("aria-selected")).toBe("true");
    expect(tabs[1]?.getAttribute("aria-selected")).toBe("false");
  });

  it("切到「测评时生效链路」后，树状图换成生效链路，且点击节点回传 effective", () => {
    const bound = bind();
    const { container } = render(<MemoryLayerPanel {...bound.props} />);
    const treeNodes = () => Array.from(container.querySelectorAll(".memory-tree-node"));

    // 创建链路 L0 的第一个节点是「原始媒体」。
    expect(treeNodes()[0]?.textContent).toContain("原始媒体");

    fireEvent.click(screen.getByRole("tab", { name: "测评时生效链路" }));
    expect(screen.getByRole("tab", { name: "测评时生效链路" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "原始创建链路" }).getAttribute("aria-selected")).toBe("false");

    // 生效链路 L0 的第一个节点是「问题」——两种口径是两份数据，不是同一份渲染两遍。
    expect(treeNodes()[0]?.textContent).toContain("问题");
    expect(treeNodes()[0]?.textContent).not.toContain("原始媒体");

    fireEvent.click(treeNodes()[0] as HTMLElement);
    expect(bound.onOpenChainNode).toHaveBeenCalledWith(
      "effective",
      expect.objectContaining({ key: "L0" }),
      expect.objectContaining({ kind: "问题" }),
    );
  });

  it("创建链路下点击树状图节点，回传 (creation, layer, node)", () => {
    const bound = bind();
    const { container } = render(<MemoryLayerPanel {...bound.props} />);

    const node = container.querySelectorAll(".memory-tree-node")[0] as HTMLElement;
    fireEvent.click(node);

    expect(bound.onOpenChainNode).toHaveBeenCalledTimes(1);
    expect(bound.onOpenChainNode).toHaveBeenCalledWith(
      "creation",
      expect.objectContaining({ key: "L0", name: "原始流" }),
      expect.objectContaining({ kind: "原始媒体" }),
    );
  });

  it("字段证据明细图的节点走同一个回调", () => {
    const bound = bind();
    const { container } = render(<MemoryLayerPanel {...bound.props} />);

    expect(container.querySelector(".memory-graph-caption")?.textContent).toBe("字段证据明细（与上方树状关系图对应）");
    fireEvent.click(container.querySelectorAll(".memory-graph-node")[0] as HTMLElement);

    expect(bound.onOpenChainNode).toHaveBeenCalledWith(
      "creation",
      expect.objectContaining({ key: "L0" }),
      expect.objectContaining({ kind: "原始媒体" }),
    );
  });

  it("面板包含有效性表、正确性对比、命中矩阵与脚注，关键帧由运行详情独立展示", () => {
    const { props } = bind();
    const { container } = render(<MemoryLayerPanel {...props} />);

    expect(container.querySelector(".memory-effectiveness-panel")).not.toBeNull();
    expect(container.querySelector(".answer-correctness-panel")).not.toBeNull();
    expect(container.querySelector(".memory-answer-attribution-panel")).not.toBeNull();
    expect(container.querySelector(".keyframe-analysis-panel")).toBeNull();
    expect(screen.getByText(/指标口径：运行级指标来自本次 run 的汇总轨迹/)).not.toBeNull();
  });

  it("chainDetail 有值时弹出证据链弹窗，关闭回调接在弹窗上", () => {
    const bound = bind({
      chainDetail: {
        mode: "creation",
        layer: { key: "L2", name: "事件层", color: "event", stage: "跨媒体聚合", nodes: [], transition: "x" },
        node: { kind: "事件上下文", value: "3", evidence: "event_context.id" },
        records: [],
        page: 1,
        pageSize: 20,
        total: 0,
      },
    });
    render(<MemoryLayerPanel {...bound.props} />);

    expect(document.querySelector(".chain-detail-modal")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(bound.onCloseChainDetail).toHaveBeenCalledTimes(1);
  });
});
