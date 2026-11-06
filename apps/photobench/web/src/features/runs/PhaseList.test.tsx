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
  it("隔离小样区分答卷与源任务日志", () => {
    const {container} = render(<PhaseList activeRun={{run_id:'sample', source_run_id:'full',
      qa_count:3, item_count:3, summary:{judge_valid_count:3},
      phases:{qa_eval:{status:'done',progress:{agent_completed:487,total:487}}}}}/>);
    expect(container.textContent).toContain('复用答卷 3/3');
    expect(container.textContent).toContain('源任务答题 487/487');
    expect(container.textContent).not.toContain('487/3');
  });
  it("补评分单独显示本轮日志，首次执行错误默认折叠", () => {
    const run: Run = {
      ...activeRun({qa_eval:{status:"partial",error_details:[{sample_id:"q1",reason:"旧错误"}]}}),
      qa_count:1, summary:{judge_valid_count:1}, rejudge:{rejudge_id:"new-task",status:"completed",completed:1,total:1,failed:0,results:[]},
    };
    const {container}=render(<PhaseList activeRun={run}/>);
    expect(container.querySelector('.scoring-task-log')?.textContent).toContain("本轮评分 1/1 项 · 失败 0 项");
    expect(container.querySelector('.scoring-task-log')?.textContent).not.toContain("旧错误");
    expect(container.querySelector('.scoring-task-log .phase-status')?.textContent).toBe("完成");
    expect(container.querySelector('.initial-execution-log')?.hasAttribute('open')).toBe(false);
    expect(container.querySelector('.initial-execution-log')?.textContent).toContain("旧错误");
    expect(container.textContent).not.toContain("有效评分 1/1");
    expect(run.phases?.qa_eval?.status).toBe("partial");
  });

  it("每次补评都有独立记录，后续成功不覆盖上次错误", () => {
    const {container}=render(<PhaseList activeRun={{run_id:'r',status:'completed_with_errors',
      rejudge_history:[{rejudge_id:'old',status:'failed',failed:1,error_details:[{sample_id:'q1',reason:'上次错误'}]}],
      rejudge:{rejudge_id:'new',status:'completed',completed:2,total:2,failed:0,results:[{sample_id:'q1',status:'completed',score:0,max_score:1}]}}}/>);
    const logs=container.querySelectorAll('.scoring-task-log');
    expect(logs).toHaveLength(2);
    expect(logs[0]?.textContent).toContain('new');
    expect(logs[0]?.textContent).toContain('0/1');
    expect(logs[0]?.textContent).not.toContain('上次错误');
    expect(logs[0]?.hasAttribute('open')).toBe(true);
    expect(logs[1]?.textContent).toContain('上次错误');
    expect(logs[1]?.hasAttribute('open')).toBe(false);
  });
  it("历史外层已有70个Judge终态时，阶段卡不能显示0/487", () => {
    const { container } = render(<PhaseList activeRun={activeRun({ qa_eval: {
      status: "running", judge_completed: 70, judge_total: 487,
      progress: { completed: 71, total: 487 },
    } })} />);
    expect(container.textContent).toContain("答题已执行 71/487 · 有效评分 —/487");
    expect(container.textContent).not.toContain("评分尝试已结束 0/487");
    expect(container.textContent).toContain("评分尝试已结束 70/487（含失败）");
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

  it("媒体计数只填充第一段，不能当作流水线总进度", () => {
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
    const fill = phase?.querySelector(".pipeline-step:first-child i") as HTMLElement | null;
    expect(fill?.style.width).toBe("50%");
    expect(phase?.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBeNull();
    expect(phase?.querySelectorAll(".pipeline-step.done")).toHaveLength(0);
  });

  it("运行中的流水线用实时墙钟算每张图片均值，而不是显示「未记录」", () => {
    // 阶段还没结束：没有 total_seconds / finished_at，只有 started_at 与 image_count。
    // 指标必须拿 ``displayPhase`` 的实时墙钟，否则永远「未记录」。
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T03:10:00Z"));
    const { container } = render(
      <PhaseList
        activeRun={activeRun({
          pipeline_processing: {
            status: "running",
            started_at: "2026-10-29T03:00:00Z",
            progress: { total: 300, processed: 10, failed: 0, skipped: 0 },
            pipeline_metrics: { image_count: 300 },
          },
        })}
      />,
    );

    const phase = container.querySelectorAll(".phase-card")[4];
    expect(phase?.textContent).toContain("每张图片折算耗时");
    expect(phase?.textContent).toContain("2s");
    expect(phase?.textContent).toContain("记录阶段耗时 ÷ 300 张图片");
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
  expect(getByTestId("import-waiting").textContent).toContain("人物画像");
  expect(container.querySelector(".pipeline-phase-card .phase-status")?.textContent).toBe("进行中");
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
  expect(container.textContent).toContain("显示上次记录");
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
  expect(cards[4]?.textContent).toContain("汇总与构建");
  expect(cards[4]?.querySelector(".phase-status")?.textContent).toBe("进行中");
  expect(cards[4]?.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBeNull();
  expect(cards[4]?.querySelectorAll(".pipeline-step.done")).toHaveLength(1);
  expect(cards[4]?.querySelector(".pipeline-step.running")?.textContent).toBe("汇总与构建");
  expect(cards[4]?.querySelector(".phase-bar")).toBeNull();
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
  const processing = getByRole("table", { name: "媒体处理与事件总结", hidden: true });
  expect(processing.textContent).toContain("任务累计 3分20秒（辅助统计）");
  expect(processing.textContent).toContain("未记录整段实际耗时");
  expect(getByRole("table", { name: "图谱与人物收尾", hidden: true }).textContent).toContain("占后端 20.0%");
  expect(pipeline?.querySelector("details")?.open).toBe(false);
});

it("只有流水线成功终态才显示100%，无需等待QA结束", () => {
  const { getByRole, container } = render(<PhaseList activeRun={activeRun({
    pipeline_processing: { status: "done", progress: { total: 3, processed: 3, stage: "completed", request_status: "returned" } },
    qa_eval: { status: "running" },
  })} />);
  expect(getByRole("progressbar", { name: "流水线整体进度" }).getAttribute("aria-valuenow")).toBe("100");
  expect(container.querySelectorAll(".pipeline-step.done")).toHaveLength(3);
});


it("导入首屏保留数量、失败和墙钟，登记细节折叠且终态不再多占一条进度栏", () => {
  const { container } = render(<PhaseList activeRun={activeRun({photo_import: {
    status: "done", execution_mode: "explicit", scanned_count: 14, accepted_count: 14,
    deduplicated_count: 0, failed_count: 0, started_at: "2026-10-29T01:00:00Z",
    finished_at: "2026-10-29T01:00:01.290Z", progress: { total: 14, processed: 14 }, timings: { asset_import: 0.1 },
  }})} />);
  const card = container.querySelectorAll(".phase-card")[3]!;
  const summary = card.querySelector("details > summary")!;
  expect(summary.textContent).toContain("媒体 14/14 · 失败 0 · 1.29s");
  expect(summary.textContent).not.toContain("登记落库累计");
  expect(card.querySelector("details")?.open).toBe(false);
  expect(card.textContent).toContain("登记落库累计 0.1s");
  expect(card.querySelector(".phase-bar")).toBeNull();
});

it("取消任务的9题答题与3题有效评分分别展示，跳过题不当作已完成", () => {
  const { container } = render(<PhaseList activeRun={{ run_id: "cancelled", status: "cancelled", qa_count: 12,
    summary: { judge_valid_count: 3 }, phases: { qa_eval: { status: "cancelled", progress: {
      completed: 9, total: 12, judge_completed: 9, judge_total: 12, judge_skipped: 3,
    } } } }} />);
  expect(container.textContent).toContain("答题已执行 9/12 · 有效评分 3/12");
  expect(container.textContent).toContain("评分尝试已结束 9/12（含失败） · 未进入评分 3 题");
});

it("复用相册的三个准备阶段显示跳过，不借用任务异常或虚构进度", () => {
  const run: Run = { run_id: "resume", mode: "reuse", status: "completed_with_errors", phases: {
    model_deploy: { status: "done", total_seconds: 0.042 },
    scope_setup: { status: "done", total_seconds: 0 },
    qa_eval: { status: "partial", progress: { completed: 1171, total: 1171 } },
  } };
  const saved = JSON.stringify(run);
  const { container, queryByRole } = render(<PhaseList activeRun={run} />);
  const cards = container.querySelectorAll(".phase-card");
  expect(cards).toHaveLength(6);
  expect(cards[0]?.textContent).toContain("模型连接");
  expect(cards[1]?.textContent).toContain("连接已有相册");
  expect(cards[1]?.textContent).toContain("使用已有相册，不创建新相册");
  expect(cards[1]?.textContent).not.toContain("<1ms");
  for (const index of [2, 3, 4]) {
    expect(cards[index]?.querySelector(".phase-status")?.textContent).toBe("跳过");
    expect(cards[index]?.querySelector("details")).toBeNull();
    expect(cards[index]?.textContent).not.toMatch(/等待|\?|有异常/);
  }
  expect(queryByRole("progressbar")).toBeNull();
  expect(cards[5]?.querySelector(".phase-status")?.textContent).toBe("部分完成");
  expect(JSON.stringify(run)).toBe(saved);
});

it("复用模式尚未开始时仍区分待执行与跳过，明确跳过的阶段不显示登记详情", () => {
  const { container } = render(<PhaseList activeRun={{run_id: "reuse", mode: "reuse", status: "pending",
    phases: { identity_seed: {status: "pending"}, photo_import: {status: "skipped"} },
  }} />);
  const cards = container.querySelectorAll(".phase-card");
  expect(cards[0]?.querySelector(".phase-status")?.textContent).toBe("等待");
  expect(cards[1]?.querySelector(".phase-status")?.textContent).toBe("等待");
  expect(container.querySelectorAll(".phase-status.skipped")).toHaveLength(3);
  expect(cards[3]?.querySelector("details")).toBeNull();
});

it("复用历史实际记录的失败保留错误，全链路缺失阶段继续显示等待", () => {
  const { container, rerender } = render(<PhaseList activeRun={{run_id: "legacy", mode: "reuse", phases: {
    pipeline_processing: { status: "failed", error_details: [{reason: "真实历史错误"}] },
  }}} />);
  expect(container.querySelectorAll(".phase-card")[4]?.textContent).toContain("真实历史错误");
  expect(container.querySelectorAll(".phase-card")[4]?.querySelector(".phase-status")?.textContent).toBe("失败");
  rerender(<PhaseList activeRun={{run_id: "full", mode: "full", status: "running", phases: {}}} />);
  expect(container.querySelectorAll(".phase-status.skipped")).toHaveLength(0);
  expect(container.querySelectorAll(".phase-card")[1]?.textContent).toContain("创建相册");
  expect(container.querySelectorAll(".phase-card")[2]?.querySelector(".phase-status")?.textContent).toBe("等待");
});
