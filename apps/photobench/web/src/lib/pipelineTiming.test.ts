import { describe, expect, it } from "vitest";
import { isCombinedImport, phaseElapsed, pipelineDuration, pipelineTimingView } from "./pipelineTiming.ts";

describe("流水线计时口径", () => {
  it("完整起止与后端 total_seconds 不同，分别展示，不改原始数据", () => {
    const phase = { execution_mode: "explicit", status: "done", total_seconds: 701.651,
      started_at: "2026-10-28T22:39:33.565294+08:00", finished_at: "2026-10-28T22:51:17.084693+08:00",
      timings: { total: 701.651, pipeline_core: 585.211, source_memory_prepare: 768.567, source_memory_commit: 450.045,
        event_summarize: 13.229, participant_projection: 0.257, memory_graph: 0.041, causal_graph: 0,
        family_graph: 15.636, person_insights: 100.506 } };
    const snapshot = JSON.stringify(phase);
    const view = pipelineTimingView(phase);
    expect(phaseElapsed(phase).seconds).toBeCloseTo(703.519, 3);
    expect(view.overview.map(row => row.value)).toEqual(["11分43.5秒", "11分41.7秒"]);
    expect(view.groups.find(group => group.key === "core")?.summary).toBe("实际经过 9分45.2秒 · 占后端 83.4%");
    const prepare = view.groups.find(group => group.key === "core")!.rows.find(row => row.key === "source_memory_prepare")!;
    expect(prepare.value).toBe("未记录");
    expect(prepare.description).toContain("任务累计 12分48.6秒（辅助统计）");
    expect(prepare.basis).toBe("整段实际耗时");
    expect(prepare.share).toBeNull();
    const tail = view.groups.find(group => group.key === "finalize")!;
    expect(tail.summary).toBe("实际经过 1分56.4秒 · 占后端 16.6%");
    expect(tail.rows.find(row => row.key === "person_insights")?.share).toBe("14.3%");
    expect(tail.rows.some(row => ["memory_graph", "causal_graph"].includes(row.key))).toBe(false);
    expect(view.groups.find(group => group.key === "memory_flow")?.rows.find(row => row.key === "memory_graph")?.value).toBe("41ms");
    expect(JSON.stringify(phase)).toBe(snapshot);
  });

  it("视频处理合并展示，不再重复列出内部阶段", () => {
    const view = pipelineTimingView({ execution_mode: "explicit", status: "done", timings: {
      total: 100, pipeline_core: 80, video_total: 35, video_scene_planning: 1,
      video_scene_write: 2, video_keyframe_extraction: 3, video_keyframe_asset_write: 4, video_keyframe_memory: 25,
    } });
    expect(view.groups.some(group => group.key === "video")).toBe(false);
    expect(view.groups.find(group => group.key === "core")!.rows.find(row => row.key === "video_total"))
      .toMatchObject({ value: "未记录", description: expect.stringContaining("任务累计 35s（辅助统计）") });
  });

  it("旧合并接口保留主线程等待含义，不当作模型累计或独立流水线墙钟", () => {
    const imported = { status: "done", total_seconds: 120,
      timings: { stage: 2, prepare: 50, finish: 30, summarize: 5, participant_refresh: 3, person_insights: 20, other: 1 } };
    const view = pipelineTimingView({ total_seconds: 1 }, imported);
    expect(view.combined).toBe(true);
    expect(view.overview[0]).toMatchObject({ label: "历史合并请求耗时", value: "2分" });
    expect(view.groups[0]!.rows.find(row => row.key === "prepare")?.basis).toBe("主线程等待累计");
    expect(view.groups[0]!.rows.some(row => row.key === "stage")).toBe(false);
    expect(view.groups[0]!.rows.find(row => row.key === "person_insights")?.value).toBe("20s");
    expect(view.groups[0]!.rows.every(row => row.share === null)).toBe(true);
    expect(isCombinedImport({ ...imported, execution_mode: "explicit" })).toBe(false);
  });

  it("缺失、负数、非有限值不显示成零；未存起止时不称实际墙钟", () => {
    const view = pipelineTimingView({ execution_mode: "explicit", status: "done", total_seconds: 5,
      timings: { source_memory_prepare: NaN, source_memory_commit: -1, event_summarize: null } });
    expect(view.overview[0]!.label).toBe("记录耗时");
    expect(view.groups.find(group => group.key === "core")!.rows.every(row => row.value === "未记录")).toBe(true);
    const running = pipelineTimingView({ execution_mode: "explicit", status: "running", total_seconds: 5 });
    expect(running.groups.find(group => group.key === "core")!.rows.every(row => row.value === "待回传")).toBe(true);
    expect(phaseElapsed({ started_at: "bad", finished_at: "bad", total_seconds: 0 })).toEqual({ seconds: 0, label: "记录耗时" });
  });

  it("保留已有显式运行的调用统计，不因执行模式而隐藏", () => {
    const view = pipelineTimingView({ execution_mode: "explicit", pipeline_metrics: {
      effective_workers: 4, configured_workers: 8, event_summary_call_count: 0,
      stage_timings: { vlm_image_description_seconds: { count: 10, mean_seconds: 8, sum_seconds: 80, p95_seconds: 20 } },
    } });
    const stats = view.groups.find(group => group.key === "statistics")!;
    expect(stats.rows.find(row => row.key === "workers")?.value).toBe("4 路");
    expect(stats.rows.find(row => row.key === "summary_calls")?.value).toBe("0 次");
    expect(stats.rows.find(row => row.key === "vlm_image_description_seconds")).toMatchObject({ value: "8s", basis: "单次均值", share: null });
  });

  it("缺后端总计或分项大于总计时不输出误导占比，不拿累计推算墙钟", () => {
    for (const total of [undefined, 0, 5]) {
      const view = pipelineTimingView({ execution_mode: "explicit", timings: { total, memory_graph: 10 } });
      expect(view.groups.find(group => group.key === "memory_flow")!.rows.find(row => row.key === "memory_graph")?.share).toBeNull();
    }
    expect(pipelineTimingView({}).groups).toEqual([]);
    expect(pipelineDuration(60945.1)).toBe("16小时55分45.1秒");
    expect(pipelineDuration(59.999)).toBe("60s");
    expect(pipelineDuration(3599.999)).toBe("1小时");
  });
});


it("收尾汇总只相加四个顺序执行段，缺项不补零，因果图不重复计入", () => {
  const view = pipelineTimingView({ execution_mode: "explicit", status: "done", timings: {
    total: 100, pipeline_core: 60, source_memory_prepare: 110,
    participant_projection: 1, memory_graph: 9, causal_graph: 8, family_graph: 10, person_insights: 20,
  } });
  expect(view.groups.find(group => group.key === "finalize")?.summary).toBe("实际经过 40s · 占后端 40.0%");
  expect(view.groups.find(group => group.key === "core")?.summary).toBe("实际经过 1分 · 占后端 60.0%");
  for (const status of ["done", "running"]) {
    const missing = pipelineTimingView({ execution_mode: "explicit", status, timings: { total: 100, person_insights: 20 } });
    expect(missing.groups.find(group => group.key === "finalize")?.summary).toBe(status === "running" ? "待回传" : "未记录");
  }
});

it("并行任务累计20秒，实际经过10秒，阶段主值与占比使用实际经过时间", () => {
  const phase = { execution_mode: "explicit", status: "done", timings: {
    total: 20, pipeline_core: 15, source_memory_prepare: 20, source_memory_prepare_wall: 10,
    video_total: 8, video_total_wall: 7, video_scene_planning: 2, video_scene_planning_wall: 3,
    finalize_wall: 5, participant_projection: 1, memory_graph: 1, family_graph: 1, person_insights: 1,
  } };
  const before = JSON.stringify(phase);
  const view = pipelineTimingView(phase);
  expect(view.groups.find(group => group.key === "core")!.rows.find(row => row.key === "source_memory_prepare"))
    .toMatchObject({ value: "10s", basis: "整段实际耗时", share: "50.0%" });
  expect(view.groups.find(group => group.key === "core")!.rows.find(row => row.key === "source_memory_prepare")?.description)
    .toContain("任务累计 20s（辅助统计）");
  expect(view.groups.some(group => group.key === "video")).toBe(false);
  expect(view.groups.find(group => group.key === "finalize")?.summary).toBe("实际经过 5s · 占后端 25.0%");
  expect(JSON.stringify(phase)).toBe(before);
});

it("记忆构建全流程按流水线和 QA Agent 两段真实墙钟展示，不把 Judge 或并发累计相加", () => {
  const view = pipelineTimingView({
    execution_mode: "explicit", status: "done",
    started_at: "2026-11-09T10:00:00Z", finished_at: "2026-11-09T10:01:40Z",
    timings: { total: 100, pipeline_core: 70, memory_graph: 8, video_keyframe_memory: 25 },
  }, {}, { qa: { status: "done", agent_phase_wall_ms: 120000, judge_phase_wall_ms: 200000 }, graphEnabled: true });
  const flow = view.groups.find(group => group.key === "memory_flow")!;
  expect(flow.summary).toBe("流水线＋Agent 合计 3分40秒");
  expect(flow.rows.map(row => [row.label, row.value])).toEqual([
    ["基础记忆构建", "1分10秒"],
    ["图结构构建", "8s"],
    ["检索与推理（Agent）", "2分"],
  ]);
  expect(flow.rows[2].share).toBeNull();
  expect(view.groups.find(group => group.key === "finalize")?.rows.some(row => row.key === "memory_graph")).toBe(false);
});

it("无图和进行中 QA 如实标注，旧记录缺 Agent 时间不补零", () => {
  const phase = { execution_mode: "explicit", status: "done", timings: { total: 10, pipeline_core: 8, memory_graph: 0 } };
  const off = pipelineTimingView(phase, {}, {
    graphEnabled: false, now: Date.parse("2026-11-09T10:00:12Z"),
    qa: { status: "running", agent_phase_started_at: "2026-11-09T10:00:00Z" },
  });
  expect(off.groups.find(group => group.key === "memory_flow")?.rows.map(row => row.value)).toEqual(["8s", "已关闭", "12s"]);
  expect(pipelineTimingView(phase, {}, { qa: { status: "done" } }).groups
    .find(group => group.key === "memory_flow")?.rows[2].value).toBe("未记录");
  expect(pipelineTimingView(phase, {}, { qa: { status: "pending" } }).groups
    .find(group => group.key === "memory_flow")?.rows[2].value).toBe("待开始");
});
