import { describe, expect, it } from "vitest";
import type { Run } from "../api/types.ts";
import { qaProgress } from "./qaProgress.ts";

describe("复制答卷的进度来源", () => {
  const sample: Run = {run_id: "sample", source_run_id: "full", qa_count: 3, item_count: 3,
    summary: {judge_valid_count: 3}, phases: {qa_eval: {progress: {
      agent_completed: 487, total: 487, judge_completed: 487, judge_total: 487,
    }}}};

  it("小样使用保存的答卷数，源任务阶段日志保持", () => {
    const before = structuredClone(sample);
    expect(qaProgress(sample)).toMatchObject({copiedAnswers: true, agentRatio: "3/3",
      validRatio: "3/3", sourceAgentRatio: "487/487", attempted: null});
    expect(sample).toEqual(before);
  });

  it("普通运行保留异常计数，不截成总数", () => {
    expect(qaProgress({...sample, source_run_id: undefined}).agentRatio).toBe("487/3");
  });

  it("复制答卷缺少数量时显示未知，不借用来源数量", () => {
    expect(qaProgress({...sample, item_count: undefined}).agentRatio).toBe("—/3");
    expect(qaProgress({...sample, item_count: 0}).agentRatio).toBe("0/3");
  });
});
