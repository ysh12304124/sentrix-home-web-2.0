import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../../api/types.ts";
import { PhaseList } from "./PhaseList.tsx";

// 1.0 参考 `App.vue:3097-3129`。夹具口径：6 张卡固定；status / error_details
// 有与无必须给出不同答案。

afterEach(() => { cleanup(); vi.useRealTimers(); });

function activeRun(phases: Run["phases"]): Run {
  return { run_id: "r1", phases };
}

describe("阶段卡（1.0 :3099-3129）", () => {
  it("历史外层已有70个Judge终态时，阶段卡不能显示0/487", () => {
    const { container } = render(<PhaseList activeRun={activeRun({ qa_eval: {
      status: "running", judge_completed: 70, judge_total: 487,
      progress: { completed: 71, total: 487 },
    } })} />);
    expect(container.textContent).toContain("Agent 71/487 · Judge 已结束 70/487");
    expect(container.textContent).not.toContain("Judge 已结束 0/487");
  });

  it("固定渲染 6 张卡，状态文案随 phase.status 变化", () => {
    const { container } = render(
      <PhaseList activeRun={activeRun({ model_deploy: { status: "done" }, qa_eval: { status: "running" } })} />,
    );

    expect(container.querySelectorAll(".phase-card")).toHaveLength(6);
    const statuses = container.querySelectorAll(".phase-card .phase-status");
    expect(statuses[0]?.textContent).toBe("完成");
    expect(statuses[5]?.textContent).toBe("进行中");
    expect(statuses[1]?.textContent).toBe("等待"); // 未提供 → pending
  });

  it("错误明细有/无两种口径", () => {
    const absent = render(<PhaseList activeRun={activeRun({ model_deploy: { status: "done" } })} />);
    expect(absent.container.querySelector(".phase-errors")).toBeNull();
    cleanup();

    const present = render(
      <PhaseList
        activeRun={activeRun({
          model_deploy: {
            status: "failed",
            error_details: [{ sample_id: "s1", reason: "原因", status: "failed", asset_id: "a1" }],
          },
        })}
      />,
    );
    const errors = present.container.querySelector(".phase-errors");
    expect(errors).not.toBeNull();
    expect(errors?.querySelector("summary")?.textContent).toBe("1 条错误记录");
    expect(errors?.textContent).toContain("s1");
    expect(errors?.textContent).toContain("原因");
    expect(errors?.textContent).toContain("a1");
  });

  it("pipeline_processing 进度条宽度 = (processed+failed+skipped)/total", () => {
    const { container } = render(
      <PhaseList
        activeRun={activeRun({
          pipeline_processing: {
            status: "running",
            progress: { total: 10, processed: 3, failed: 1, skipped: 1 },
          },
        })}
      />,
    );

    // EXECUTION_PHASES 第 5 个（index 4）是 pipeline_processing。
    const phase = container.querySelectorAll(".phase-card")[4];
    const fill = phase?.querySelector(".phase-bar-fill") as HTMLElement | null;
    expect(fill?.style.width).toBe("50%");
  });
});


it("build 显示评分未适用并隐藏问答阶段", () => {
  const { container, getByText } = render(<PhaseList activeRun={{ run_id: "b", mode: "build" }} />);
  expect(container.querySelectorAll(".phase-card")).toHaveLength(5);
  expect(getByText(/QA、Judge 和评分有效性不适用/)).toBeTruthy();
});

it("同步导入显示目录与等待时长，不显示伪造媒体进度", () => {
  const { getByTestId } = render(<PhaseList activeRun={{ run_id: "i", phases: { photo_import: {
    status: "failed", finished_at: "2026-10-22T01:02:00Z", progress: {
      current_directory: "photos/day1", directories_done: 1, directories_total: 3,
      request_started_at: "2026-10-22T01:00:00Z"
    }
  } } }} />);
  expect(getByTestId("import-waiting").textContent).toContain("photos/day1");
  expect(getByTestId("import-waiting").textContent).toContain("120 秒");
  expect(getByTestId("import-waiting").textContent).toContain("1/3");
});


it("显示真实媒体计数与收尾阶段，媒体100%仍保持进行中，墙钟每秒刷新", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-23T01:01:00Z"));
  const { container, getByLabelText, getByTestId, unmount } = render(<PhaseList activeRun={activeRun({
    photo_import: {
      status: "running", started_at: "2026-10-23T01:00:00Z",
      progress: {
        current_directory: "photos", directories_done: 0, directories_total: 2,
        request_started_at: "2026-10-23T01:00:00Z", availability: "available",
        last_received_at: "2026-10-23T01:01:00Z",
        observation: {
          total: 3, completed: 3, imported: 1, deduplicated: 1, failed: 1,
          status: "running", stage: "person_insights", updated_at: "2026-10-23T01:00:59Z",
        },
      },
    },
  })} />);
  expect(container.textContent).toContain("当前目录已处理 3/3 个媒体 · 新增 1 · 去重 1 · 失败 1 · 登记与处理合并计时，详见流水线");
  expect(getByLabelText("当前目录媒体处理进度").getAttribute("value")).toBe("3");
  expect(getByTestId("import-waiting").textContent).toContain("生成人物画像");
  expect(getByTestId("import-waiting").textContent).toContain("后端状态：running");
  act(() => vi.advanceTimersByTime(1000));
  expect(container.textContent).toContain("1分1秒");
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it("未扫描的total保持未知，查询故障和上次观测同时可见", () => {
  const { container, queryByLabelText } = render(<PhaseList activeRun={activeRun({
    photo_import: { status: "running", progress: {
      current_directory: "videos", availability: "unavailable", query_error: "GET disconnected",
      observation: { total: null, completed: 0, imported: 0, deduplicated: 0, failed: 0, stage: "scanning" },
    } },
  })} />);
  expect(container.textContent).toContain("当前目录已处理 0/? 个媒体");
  expect(container.textContent).toContain("GET disconnected");
  expect(container.textContent).toContain("保留的计数不是当前实时进度");
  expect(queryByLabelText("当前目录媒体处理进度")).toBeNull();
});


it("导入终态保留真实扫描分母及去重，manifest数量单列保存", () => {
  const { container } = render(<PhaseList activeRun={activeRun({
    photo_import: {
      status: "partial", total_media: 99, scanned_count: 3, accepted_count: 1,
      deduplicated_count: 1, failed_count: 1, total_seconds: 20,
    },
  })} />);
  expect(container.textContent).toContain("已处理 3/3 个媒体 · 新增 1 · 去重 1 · 失败 1");
  expect(container.textContent).not.toContain("/99 个媒体");
});


it("历史处理详情归入流水线卡，新导入卡只显示登记摘要", () => {
  const { container } = render(<PhaseList activeRun={activeRun({
    photo_import: { status: "done", progress: { current_directory: "photos", observation: {
      completed: 3, total: 3, stage: "completed",
    } } },
    pipeline_processing: { status: "done" },
  })} />);
  const cards = container.querySelectorAll(".phase-card");
  expect(cards[3]?.querySelector(".import-progress-panel")).toBeNull();
  expect(cards[4]?.querySelector(".import-progress-panel")).not.toBeNull();
  expect(cards[4]?.textContent).toContain("计时保留原记录");
});

it("显式流水线媒体100%时仍显示等待汇总，导入不承载处理详情", () => {
  const { container } = render(<PhaseList activeRun={activeRun({
    photo_import: { status: "done", execution_mode: "explicit", progress: { current_directory: "photos" } },
    pipeline_processing: { status: "running", progress: {
      total: 3, processed: 3, stage: "finalizing", request_status: "running",
    } },
  })} />);
  const cards = container.querySelectorAll(".phase-card");
  expect(cards[3]?.querySelector(".import-progress-panel")).toBeNull();
  expect(cards[4]?.textContent).toContain("等待流水线汇总");
  expect(cards[4]?.querySelector(".phase-status")?.textContent).toBe("进行中");
});

it("旧合并请求的观察、归属和画像时延只放在流水线，不混入登记耗时", () => {
  const run = activeRun({
    photo_import: { status: "done", total_seconds: 120, accepted_count: 2, total_photos: 2, failed_count: 0,
      timings: { stage: 1, prepare: 50, finish: 30, summarize: 5, person_insights: 20 } },
    pipeline_processing: { status: "done", total_seconds: 0.5 },
  });
  const saved = JSON.stringify(run);
  const { container } = render(<PhaseList activeRun={run} />);
  const cards = container.querySelectorAll(".phase-card");
  expect(cards[3]?.textContent).toContain("登记落库累计 1s");
  expect(cards[3]?.textContent).not.toMatch(/50s|30s|20s/);
  expect(cards[4]?.textContent).toContain("历史合并请求耗时 2分");
  expect(cards[4]?.textContent).toContain("主线程等待累计");
  expect(cards[4]?.textContent).toContain("人物画像人物画像生成与刷新。20s");
  expect(JSON.stringify(run)).toBe(saved);
});

it("显式阶段区分请求墙钟、后端总计和嵌套步骤，长耗时显示分钟", () => {
  const { container, getByRole } = render(<PhaseList activeRun={activeRun({
    photo_import: { execution_mode: "explicit", status: "done", timings: { asset_import: 1 } },
    pipeline_processing: { execution_mode: "explicit", status: "done", total_seconds: 100,
      started_at: "2026-10-29T01:00:00Z", finished_at: "2026-10-29T01:02:00Z",
      timings: { total: 100, pipeline_core: 70, source_memory_prepare: 200, person_insights: 20 } },
  })} />);
  const pipeline = container.querySelector(".pipeline-phase-card");
  expect(pipeline?.textContent).toContain("实际总耗时 2分");
  expect(pipeline?.textContent).toContain("后端处理耗时 1分40秒");
  expect(pipeline?.textContent).toContain("1分10秒 · 占后端 70.0%");
  expect(getByRole("table", { name: "媒体处理与事件总结", hidden: true }).textContent).toContain("3分20秒并行任务累计");
  expect(getByRole("table", { name: "图谱与人物收尾", hidden: true }).textContent).toContain("占后端 20.0%");
  expect(pipeline?.querySelector("details")?.open).toBe(false);
});
