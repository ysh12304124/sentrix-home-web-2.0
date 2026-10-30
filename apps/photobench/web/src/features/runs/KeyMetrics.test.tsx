import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Run } from "../../api/types.ts";
import { KeyMetrics } from "./KeyMetrics.tsx";

// 1.0 参考 `App.vue:3130-3146`。两种口径必须给出不同答案：
//   - 有 `image_ranking.question_count` → 「召回指标」折叠块出现；
//   - 没有（该 run 未落盘候选顺序）→ `recallMetricRows` 回空数组 → 整块不渲染。
afterEach(cleanup);

const RUN: Run = {
  run_id: "r1",
  mode: "full",
  status: "done",
  summary: {
    answer_quality_mean: 1.5,
    total: 4,
    judge_distribution: { "0": 0, "1": 2, "2": 2 },
    image_ranking: {
      question_count: 3,
      r_at_1: 0.33,
      r_at_5: 0.66,
      r_at_10: 1,
      hit_at_1: 0.33,
      hit_at_5: 0.66,
      hit_at_10: 1,
      average_precision: 0.5,
      mrr: 0.6,
      p_at_5: 0.4,
      r_at_1_question_count: 1,
      r_at_5_question_count: 3,
      r_at_10_question_count: 3,
      p_at_5_question_count: 2,
      multi_positive_count: 1,
      short_candidate_count: 0,
      coverage_short_count: 0,
      no_candidate_count: 0,
    },
  },
} as Run;

describe("KeyMetrics（1.0 :3130-3146）", () => {
  it("渲染关键指标卡的标题/值/note，以及折叠的召回指标", () => {
    const { container } = render(<KeyMetrics run={RUN} />);

    expect(screen.getByText("关键指标")).not.toBeNull();
    expect(container.querySelector(".key-metric-list")).not.toBeNull();
    expect(screen.getByText("回答质量均分")).not.toBeNull();
    expect(screen.getByText("1.5 / 2")).not.toBeNull();
    // 召回指标是**折叠**的（<details>），不是平铺。
    const recall = container.querySelector("details.recall-metric-card");
    expect(recall).not.toBeNull();
    expect(recall?.querySelector("summary")?.textContent).toBe("召回指标");
    expect(screen.getByText("R@1")).not.toBeNull();
  });

  it("没有候选顺序（question_count 缺失）时，整块召回指标不渲染", () => {
    const { container } = render(<KeyMetrics run={{ ...RUN, summary: { answer_quality_mean: 1 } }} />);

    expect(container.querySelector(".key-metric-list")).not.toBeNull();
    expect(container.querySelector(".recall-metric-card")).toBeNull();
    expect(screen.queryByText("召回指标")).toBeNull();
  });
});

describe("主模型内存与显存分别展示", () => {
  it("新版采样以独立内存、显存峰值生成六张卡片", () => {
    const run = { ...RUN, phases: { gpu_metrics: {
      status: "done", protocol_version: "photobench-resource-telemetry-v1", memory_accounting: "nvidia_pss_and_vram",
      model_process_system_memory_used_mib: { peak: 3901.44 },
      model_process_gpu_memory_mib: { peak: 8192 }, device_gpu_memory_mib: { peak: 16384 },
    } } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const cards = container.querySelectorAll(".key-metric-card");
    expect(cards).toHaveLength(6);
    expect(cards[4].textContent).toContain("主模型内存占用3.81 GiB");
    expect(cards[5].textContent).toContain("主模型显存占用8.00 GiB");
    expect(cards[5].textContent).not.toContain("16.00 GiB");
  });

  it("Mac 缺主模型GPU归属时主值显示整机参考，并标明包含其他应用", () => {
    const run = { ...RUN, phases: { gpu_metrics: {
      status: "done", protocol_version: "photobench-resource-telemetry-v1", source: "apple_unified",
      model_process_system_memory_used_mib: { peak: 2048 },
      model_process_gpu_memory_mib: { peak: null }, device_gpu_memory_mib: { peak: 4096 },
    } } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const gpu = container.querySelectorAll(".key-metric-card")[5];
    expect(gpu.querySelector(".key-metric-value")?.textContent).toBe("4.00 GiB");
    expect(gpu.textContent).toContain("整机GPU占用（参考）");
    expect(gpu.textContent).toContain("包含其他应用");
    expect(gpu.textContent).toContain("不相加");
  });

  it.each([
    { source: "apple_unified", model: 1024, device: 4096, label: "主模型显存占用", value: "1.00 GiB" },
    { source: "apple_unified", model: 0, device: 4096, label: "主模型显存占用", value: "0.00 GiB" },
    { source: "apple_unified", model: null, device: null, label: "主模型显存占用", value: "未记录" },
    { source: "host_nvml", model: null, device: 4096, label: "主模型显存占用", value: "未记录" },
    { source: "jetson_local_pss", model: null, device: 4096, label: "主模型显存占用", value: "未记录" },
  ])("$source 模型=$model、整机=$device 时显示 $label $value", ({ source, model, device, label, value }) => {
    const run = { ...RUN, phases: { gpu_metrics: {
      status: "done", protocol_version: "photobench-resource-telemetry-v1", source,
      model_process_gpu_memory_mib: { peak: model }, device_gpu_memory_mib: { peak: device },
    } } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const gpu = container.querySelectorAll(".key-metric-card")[5];
    expect(gpu.querySelector(".key-metric-value")?.textContent).toBe(value);
    expect(gpu.textContent).toContain(label);
  });

  it("Jetson GPU/UMA分量独立展示，不拿合计内存代替", () => {
    const run = { ...RUN, phases: { gpu_metrics: {
      status: "done", source: "jetson_local_pss", memory_accounting: "jetson_pss_plus_nvmap_estimate",
      model_process_system_memory_used_mib: { peak: 6144 }, model_process_gpu_memory_mib: { peak: 4096 },
    } } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const cards = container.querySelectorAll(".key-metric-card");
    expect(cards[4].querySelector(".key-metric-value")?.textContent).toBe("6.00 GiB");
    expect(cards[5].querySelector(".key-metric-value")?.textContent).toBe("4.00 GiB");
    expect(cards[5].textContent).toContain("不相加");
  });

  it("实时页面采用完整采样统计，不从截短曲线重算峰值", () => {
    const run = { ...RUN, status: "running", telemetry_live: {
      protocol_version: "photobench-resource-telemetry-v1", source: "host_nvml",
      samples_count: 1000, history: [], latest: { model_process_gpu_memory_mib: null },
      field_stats: { model_process_system_memory_used_mib: { peak: 1024 },
        model_process_gpu_memory_mib: { peak: 8192 } },
    } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const cards = container.querySelectorAll(".key-metric-card");
    expect(cards[4].querySelector(".key-metric-value")?.textContent).toBe("1.00 GiB");
    expect(cards[5].querySelector(".key-metric-value")?.textContent).toBe("8.00 GiB");
  });

  it("旧NVML记录只有显存时不复制成内存，两项均不把缺失改为零", () => {
    const run = { ...RUN, phases: { gpu_metrics: {
      status: "done", model_process_memory_used_mib: { peak: 3072 },
    } } } as Run;
    const { container } = render(<KeyMetrics run={run} />);
    const cards = container.querySelectorAll(".key-metric-card");
    expect(cards[4].querySelector(".key-metric-value")?.textContent).toBe("未记录");
    expect(cards[5].querySelector(".key-metric-value")?.textContent).toBe("3.00 GiB");
  });
});
