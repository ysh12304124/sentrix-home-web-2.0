import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { MemoryEffectiveness } from "../../api/types.ts";
import { memoryRecallMatrices } from "../../lib/graphs.ts";

import { RecallMatrices } from "./RecallMatrices.tsx";

// 夹具口径：**命中与未命中并存，且工具口径与模型口径给出不同答案**
// （tool 命中 6 / 未命中 5；usage 命中 2 / 未命中 9）。1.0 参考 `App.vue:3224-3247`。
afterEach(cleanup);

const effectiveness: MemoryEffectiveness = {
  status: "ready",
  levels: [{ key: "L4+", name: "总结/回答", created: 11, effective: 5, ineffective: 6 }],
  details: {
    answer_correctness: { correct: 4, partial: 1, incorrect: 6 },
    memory_answer_attribution: {
      tool: {
        eligible: 11,
        hit_items: 6,
        miss_items: 5,
        matrix: { hit_correct: 3, hit_partial: 1, hit_incorrect: 2, miss_correct: 1, miss_partial: 0, miss_incorrect: 4 },
      },
      usage: {
        eligible: 11,
        hit_items: 2,
        miss_items: 9,
        matrix: { hit_correct: 2, hit_partial: 0, hit_incorrect: 0, miss_correct: 1, miss_partial: 0, miss_incorrect: 8 },
      },
    },
    delivery_statistics: {
      image: {eligible_items: 10, delivered_items: 6, not_delivered_items: 4, extra_delivery_items: 2, unrecorded_items: 0, delivery_rate: 0.6},
      video_keyframe: {candidate_hit_items: 6, candidate_hit_rate: 0.75, candidate_unrecorded_items: 0, eligible_items: 8, delivered_items: 2, not_delivered_items: 6, extra_delivery_items: 1, unrecorded_items: 0, delivery_rate: 0.25},
      event: {status: "not_recorded", reason: "缺少事件交付记录"},
    },
    evidence_effectiveness: {
      tool: {
        image: { returned_items: 10, effective_items: 8, ineffective_items: 2, effective_rate_on_returned: 0.8 },
        video_keyframe: { returned_items: 4, effective_items: 1, ineffective_items: 3, effective_rate_on_returned: 0.25 },
      },
      usage: {
        image: { returned_items: 8, effective_items: 6, ineffective_items: 2, effective_rate_on_returned: 0.75 },
      },
      event: { returned_items: 5, effective_items: 4, ineffective_items: 1, effective_rate_on_returned: 0.8 },
    },
  },
};

function renderMatrices() {
  const { container } = render(<RecallMatrices matrices={memoryRecallMatrices(effectiveness)} />);
  return {
    tool: container.querySelector(".recall-matrix-tool") as HTMLElement,
    usage: container.querySelector(".recall-matrix-usage") as HTMLElement,
  };
}

describe("RecallMatrices · 命中 × 回答结果（1.0 :3224-3247）", () => {
  it("两张矩阵各自渲染，命中 / 未命中的分档数落在正确的格子上", () => {
    const { tool, usage } = renderMatrices();

    expect(tool).not.toBeNull();
    expect(usage).not.toBeNull();

    // 工具口径：命中分支 6 题（6/11 = 54.5%），未命中分支 5 题（45.5%）。
    expect(tool.textContent).toContain("6 题 · 命中分支");
    expect(tool.textContent).toContain("54.5%");
    expect(tool.textContent).toContain("5 题 · 未命中分支");
    expect(tool.textContent).toContain("45.5%");
    // L1 的命中结果节点带 `/11` 的分母。
    expect(tool.textContent).toContain("6/11 题");
    expect(tool.textContent).toContain("5/11 题");

    // 模型口径：命中 2 题、未命中 9 题——与工具口径必须不同。
    expect(usage.textContent).toContain("2 题 · 命中分支");
    expect(usage.textContent).toContain("18.2%");
    expect(usage.textContent).toContain("9 题 · 未命中分支");
    expect(usage.textContent).toContain("81.8%");
    expect(usage.textContent).toContain("2/11 题");
    expect(usage.textContent).toContain("9/11 题");
  });

  it("两维矩阵的同一格子给出不同数字（不是同一份数据渲染两遍）", () => {
    const { tool, usage } = renderMatrices();

    expect(tool.textContent).not.toEqual(usage.textContent);
    // 工具口径的「命中分支」占比只在 tool 卡里出现。
    expect(usage.textContent).not.toContain("54.5%");
    expect(tool.textContent).not.toContain("81.8%");
  });

  it("L3 同时呈现本次视频候选和交付，不再展示单独的有效率卡", () => {
    const {tool, usage} = renderMatrices();
    for (const panel of [tool, usage]) {
      expect(panel.textContent).toContain("候选中找到对应视频关键帧");
      expect(panel.textContent).toContain("最终交付正确视频素材");
      expect(panel.textContent).toContain("6/8 题");
      expect(panel.textContent).toContain("2/8 题");
      expect(panel.textContent).not.toContain("原图片交付情况");
      expect(panel.textContent).not.toContain("关键帧有效率");
    }
  });

  it("没有可归因题时整节不渲染", () => {
    const { container } = render(<RecallMatrices matrices={[]} />);
    expect(container.querySelector(".memory-answer-attribution-panel")).toBeNull();
  });
});
