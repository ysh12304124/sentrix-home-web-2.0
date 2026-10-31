import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import type { Run } from "../../api/types.ts";
import type { RejudgeCardProps } from "./RejudgeCard.tsx";
import { RunDetail } from "./RunDetail.tsx";
import type { MemoryLayerPanelProps } from "../memory/MemoryLayerPanel.tsx";

// 夹具口径：fatal_error 有 / 无；导出勾选三种组合；mode build / 非 build。
// 1.0 参考 `App.vue:3039-3058`。

afterEach(cleanup);

it("切换运行和轮询更新时分析入口与记忆面板保持独立", () => {
  const memory: MemoryLayerPanelProps = {
    run: null, manifests: [], qaDetails: {}, qaPageItems: [],
    memoryEffectiveness: null, keyframeAnalysis: null, summary: null,
    chainDetail: null, onOpenChainNode: vi.fn(), onCloseChainDetail: vi.fn(),
  };
  const props = {
    exportScores: [], onExportScoresChange: vi.fn(), onExportTraces: vi.fn(),
    onExportSftTraces: vi.fn(), rejudge: rejudgeProps(), memory,
  };
  const view = render(<RunDetail {...props} activeRun={run()} />);
  for (const id of ["r2", "r2", "r1", "r1"]) {
    view.rerender(<RunDetail {...props} activeRun={run({run_id: id})} />);
    expect(screen.getAllByRole("button", {name: "错误分析 展开"})).toHaveLength(1);
  }
});

function run(overrides: Partial<Run> = {}): Run {
  return { run_id: "r1", mode: "full", status: "done", ...overrides };
}

function rejudgeProps(overrides: Partial<RejudgeCardProps> = {}): RejudgeCardProps {
  return {
    activeRejudge: null,
    judgePromptKinds: [],
    activePromptKind: "answer_quality",
    onSwitchPromptKind: vi.fn(),
    rejudgePrompt: "",
    onRejudgePromptChange: vi.fn(),
    onResetPrompt: vi.fn(),
    canRejudge: false,
    rejudgeSubmitting: false,
    rejudgeConfirmation: null,
    onRequestRejudge: () => false,
    onConfirmRejudge: vi.fn(),
    onCancelRejudge: vi.fn(),
    rejudgePercent: 0,
    ...overrides,
  };
}

interface ExportHandlers {
  onExportScoresChange: Mock<(scores: string[]) => void>;
  onExportTraces: Mock<() => void>;
  onExportSftTraces: Mock<() => void>;
}

function renderDetail(
  activeRun: Run | null,
  scores: string[] = ["0", "1", "2"],
  handlers?: Partial<ExportHandlers> & { rejudge?: RejudgeCardProps },
) {
  const merged: ExportHandlers = {
    onExportScoresChange: vi.fn<(scores: string[]) => void>(),
    onExportTraces: vi.fn<() => void>(),
    onExportSftTraces: vi.fn<() => void>(),
    ...handlers,
  };
  const view = render(
    <RunDetail
      activeRun={activeRun}
      exportScores={scores}
      onExportScoresChange={merged.onExportScoresChange}
      onExportTraces={merged.onExportTraces}
      onExportSftTraces={merged.onExportSftTraces}
      rejudge={handlers?.rejudge ?? rejudgeProps()}
    />,
  );
  return { ...view, ...merged };
}

describe("fatal_error 横幅（1.0 :3058）", () => {
  it("有 fatal_error 时渲染终止原因与失败阶段中文名", () => {
    renderDetail(
      run({ status: "failed", fatal_error: "磁盘写满", failed_phase: "pipeline_processing" }),
    );

    expect(screen.getByText("任务终止原因")).not.toBeNull();
    expect(screen.getByText("磁盘写满")).not.toBeNull();
    // 阶段标签里也有「流水线处理」，锚定横幅那句而不是阶段卡。
    expect(screen.getByText(/^失败阶段：流水线处理$/)).not.toBeNull();
  });

  it("无 fatal_error 时没有横幅", () => {
    renderDetail(run());

    expect(screen.queryByText("任务终止原因")).toBeNull();
  });
});

describe("导出轨迹控制（1.0 :3046-3054）", () => {
  it("三种勾选组合回传出不同的分数数组", () => {
    // [初始勾选, 点击第几个, 期望回传]；三种输入 → 三种不同答案。
    const combos: Array<[string[], number, string[]]> = [
      [["0", "1", "2"], 1, ["0", "2"]], // 取消「1 分」
      [["0"], 1, ["0", "1"]], // 勾上「1 分」
      [["0", "2"], 0, ["2"]], // 取消「0 分」
    ];
    for (const [initial, index, expected] of combos) {
      cleanup();
      const { onExportScoresChange } = renderDetail(run(), initial);
      const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
      fireEvent.click(boxes[index]!);
      const next = onExportScoresChange.mock.calls[0]?.[0] as string[];
      expect([...next].sort()).toEqual([...expected].sort());
    }
  });

  it("「导出轨迹 JSON」与「导出 SFT JSON」并排，各自只调自己的回调", () => {
    const { onExportTraces, onExportSftTraces } = renderDetail(run());

    fireEvent.click(screen.getByRole("button", { name: "导出轨迹 JSON" }));
    expect(onExportTraces).toHaveBeenCalledTimes(1);
    expect(onExportSftTraces).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "导出 SFT JSON" }));
    expect(onExportSftTraces).toHaveBeenCalledTimes(1);
    expect(onExportTraces).toHaveBeenCalledTimes(1);
  });
});

describe("重评卡的显隐（1.0 :3059 v-if mode !== 'build'）", () => {
  it("build 模式不渲染重评卡", () => {
    renderDetail(run({ mode: "build" }));
    expect(screen.queryByRole("button", { name: "补齐缺失评分" })).toBeNull();
  });

  it("非 build 模式渲染重评卡", () => {
    renderDetail(run({ mode: "reuse" }));
    expect(screen.getByRole("button", { name: "补齐缺失评分" })).not.toBeNull();
    expect(screen.getByText("为已有回答评分")).not.toBeNull();
  });
});

describe("无选中运行（1.0 :3040）", () => {
  it("activeRun 为 null 时渲染提示，不渲染详情头", () => {
    renderDetail(null);
    expect(screen.getByText("点击上方列表中的某条记录查看详情")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "导出轨迹 JSON" })).toBeNull();
  });
});
