import { describe, expect, it } from "vitest";
import type { Run } from "../api/types.ts";
import { pipelineProgressView } from "./pipelineProgress.ts";
const view = (phases: Run["phases"], status?: string) => pipelineProgressView({ run_id: "test", phases, status }, Date.parse("2026-10-29T00:01:00Z"));
describe("整体流水线进度", () => {
  it("媒体全部处理后仍需汇总、最终确认", () => {
    const result = view({ pipeline_processing: { status: "running", execution_mode: "explicit", progress: {
      total: 14, processed: 14, failed: 0, stage: "finalizing", request_status: "running",
    } } });
    expect(result.status).toBe("running");
    expect(result.steps.map(s => s.state)).toEqual(["done", "running", "pending"]);
  });
  it("请求返回到阶段落定之间仍不是整体完成", () => {
    expect(view({ pipeline_processing: { status: "running", progress: { request_status: "returned", stage: "completed" } } }).steps.map(s => s.state))
      .toEqual(["done", "done", "running"]);
  });
  it("流水线完成与QA是否结束无关", () => {
    const result = view({ pipeline_processing: { status: "done" }, qa_eval: { status: "running" } }, "running");
    expect(result.status).toBe("done");
    expect(result.steps.every(s => s.state === "done")).toBe(true);
  });
  it("全失败不能显示成功100%", () => {
    const result = view({ pipeline_processing: { status: "partial", progress: { total: 3, processed: 0, failed: 3, stage: "completed", request_status: "returned" } } });
    expect(result.status).toBe("partial");
    expect(result.steps.at(-1)?.state).toBe("stopped");
  });
  it("取消时冻结阶段，不继续显示运行", () => {
    expect(view({ pipeline_processing: { status: "running", progress: { total: 4, processed: 2 } } }, "cancelled").steps[0]?.state).toBe("stopped");
  });
  it("历史合并请求进行中，流水线同步展示正在收尾", () => {
    const result = view({ photo_import: { status: "running", progress: { current_directory: "photos", directories_total: 1, directories_done: 0,
      observation: { total: 14, completed: 14, stage: "person_insights" } } }, pipeline_processing: { status: "pending" } });
    expect(result.status).toBe("running");
    expect(result.label).toBe("人物画像");
    expect(result.steps.map(s => s.state)).toEqual(["done", "running", "pending"]);
  });
  it("首个目录完成不等于全部目录媒体完成", () => {
    const result = view({ photo_import: { status: "running", progress: { current_directory: "photos", directories_total: 2, directories_done: 0,
      observation: { total: 14, completed: 14, stage: "person_insights" } } } });
    expect(result.steps[0]?.state).toBe("running");
    expect(result.steps[0]?.fraction).toBe(0.5);
    expect(result.steps[1]?.state).toBe("pending");
  });
  it("历史导入失败不会被之后的资产确认覆盖为成功", () => {
    expect(view({ photo_import: { status: "partial", timings: { prepare: 1 } }, pipeline_processing: { status: "done" } }).status).toBe("partial");
  });
  it("未知计数保留未知，查询故障不隐藏", () => {
    const result = view({ photo_import: { status: "running", progress: { current_directory: "photos", availability: "unavailable", query_error: "timeout",
      observation: { total: null, completed: 0, stage: "scanning" } } } });
    expect(result.steps[0]?.fraction).toBeNull();
    expect(result.detail).toContain("0/?");
    expect(result.warning).toContain("timeout");
  });
  it("显式处理使用自己的进度，忽略旧导入观测", () => {
    const result = view({ photo_import: { status: "done", progress: { current_directory: "photos", observation: { total: 99, completed: 99 } } },
      pipeline_processing: { execution_mode: "explicit", status: "running", progress: { total: 10, processed: 2 } } });
    expect(result.combined).toBe(false);
    expect(result.steps[0]?.fraction).toBe(0.2);
  });
});

it("媒体计数全到齐但finalizing字段尚未刷新时，仍保留收尾段", () => {
  const result = view({ pipeline_processing: { status: "running", execution_mode: "explicit",
    progress: { total: 10, processed: 9, failed: 1, stage: "media_processing", request_status: "running" } } });
  expect(result.label).toBe("汇总与构建");
  expect(result.steps.map(step => step.state)).toEqual(["done", "running", "pending"]);
});
