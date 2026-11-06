import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CapacityReport } from "./CapacityReport.tsx";
import type { CapacityEvidence, CapacityRound } from "./CapacityReport.tsx";

afterEach(cleanup);

const round = (label: string, concurrency: number, passed: boolean): CapacityRound => ({
  label, run_id: label, concurrency, passed, questions: 64, completed: 64,
  judge_completed: 64, judge_failed: 0, status: "completed", qa_per_min: 64,
  qa_seconds: 60, judge_seconds: 80, latency_p50_s: 1, latency_p95_s: 2,
  ttft_p95_s: null, actual_batch_peak: 3, queue_peak: 1,
  memory_peak_gib: null, swap_delta_gib: 0, failures: {}, samples: [],
});

it("keeps prior failed-speed rounds and diagnostics out of current acceptance", () => {
  render(<CapacityReport evidence={{
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "prefix",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [],
    prior_optimization_rounds: [round("C16-R1-v9", 16, true), round("C16-R2-v9", 16, true),
      { ...round("C16-FULL487-v10", 16, false), questions: 487, completed: 474, judge_completed: 487 }],
    diagnostic_rounds: [{ ...round("profile16", 16, true), questions: 16, completed: 16 }],
    rounds: [{ ...round("C16-R1-prefix", 16, true), prefix_cache_evidence: {
      verified: true, observed_requests: 260, expected_requests: 260, requests_with_reuse: 200,
      cached_tokens: 204800, token_hit_rate: 0.4,
    } }],
  }} />);
  expect(screen.queryByText("C16")).toBeNull();
  const prior = screen.getByRole("region", { name: "先前优化结果" });
  expect(prior.textContent).toContain("旧版 Judge 终态计数不代表有效评分数");
  expect(screen.getByRole("button", { name: "C16-FULL487-v10" }).parentElement?.textContent).toContain("执行验收未通过");
  expect(screen.getByRole("button", { name: "C16-R1-v9" }).parentElement?.textContent).toContain("执行验收通过");
  expect(screen.getByRole("region", { name: "独立诊断结果" }).textContent).toContain("不计入正式速度门槛");
  expect(screen.getByText(/实际前缀复用：200\/260 次调用/).textContent).toContain("40.0%");
  expect(screen.getByRole("combobox", { name: "选择资源曲线轮次" }).querySelectorAll("option")).toHaveLength(5);
});

describe("capacity evidence", () => {
  it("requires both rounds and separates requested concurrency from actual model batch", () => {
    const evidence: CapacityEvidence = {
      schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "vllm-mlx",
      model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
      context_tokens: 32768, conditions: [],
      rounds: [round("C4-R1", 4, true), round("C4-R2", 4, true), round("C16-R1", 16, true)],
    };
    render(<CapacityReport evidence={evidence} />);
    expect(screen.getByText("C4")).toBeTruthy();
    expect(screen.getByText("C4 · 64.0 题/分钟")).toBeTruthy();
    expect(screen.queryByText("C16")).toBeNull();
    expect(screen.getAllByText("未采集").length).toBeGreaterThan(0);
    expect(screen.getByText("实际批次峰值")).toBeTruthy();
    expect(screen.getByText("尚未开始")).toBeTruthy();
  });
});

it("shows the independent 487 run's measurements separately from the 64-question matrix", () => {
  const full: CapacityRound = {
    ...round("C8-FULL487", 8, true), questions: 487, completed: 487, judge_completed: 487,
    qa_seconds: 600, judge_seconds: 700, qa_per_min: 48.7, actual_batch_peak: 6, queue_peak: 2,
  };
  const evidence: CapacityEvidence = {
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "vllm-mlx",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [], rounds: [full],
  };
  render(<CapacityReport evidence={evidence} />);
  const overview = screen.getByRole("region", { name: "487 题独立测试总览" });
  expect(overview.textContent).toContain("487 题独立测试 · C8");
  expect(overview.textContent).toContain("48.7 题/分钟");
  expect(overview.textContent).toContain("6 / 2");
  expect(overview.textContent).toContain("客户端 QA 并发 8");
});

it("discloses scrape gaps separately from verified model execution", () => {
  const measured = {
    ...round("C4-R1", 4, true),
    resource_summary: { samples: 348, engine_metrics_missing_samples: 11 },
    model_call_evidence: { expected_calls: 315, recorded_calls: 315, verified: true },
  };
  render(<CapacityReport evidence={{
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "vllm-mlx",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [], rounds: [measured],
  }} />);
  expect(screen.getByText(/模型指标采样 337\/348 次成功/)).toBeTruthy();
  expect(screen.getByText(/实际模型调用记录 315\/315 次/)).toBeTruthy();
  expect(screen.getByText(/指标缺失 11 次/)).toBeTruthy();
});

it("labels completed QA as failed acceptance when model evidence disagrees", () => {
  const failed = {
    ...round("C4-R2", 4, false),
    failures: { incomplete_model_call_evidence: 1 },
    model_call_evidence: { expected_calls: 290, recorded_calls: 290, verified: false },
  };
  render(<CapacityReport evidence={{
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "vllm-mlx",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [], rounds: [round("C4-R1", 4, true), failed],
  }} />);
  expect(screen.getByRole("row", { name: /C4-R2/ }).textContent).toContain("未通过");
  expect(screen.queryByText("C4")).toBeNull();
  expect(screen.getByText(/尚未核对通过/)).toBeTruthy();
});

it("keeps baseline rows separate and does not count them toward optimized acceptance", () => {
  render(<CapacityReport evidence={{
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "optimized",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [],
    baseline_rounds: [round("C16-R1-baseline", 16, true), round("C16-R2-baseline", 16, true)],
    rounds: [round("C16-R1-optimized", 16, true)],
    speed_gate: { passed: false, baseline_qpm: 2.2, candidate_qpm: 2.3, weighted_speedup: 2.3 / 2.2 },
  }} />);
  expect(screen.getByRole("heading", { name: "原配置基线 · 四档并发 · 同一组 64 题 · 各两轮" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "优化配置 · 16 并发 · 同一组 64 题 · 两轮" })).toBeTruthy();
  expect(screen.queryByText("C16")).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("优化速度验收：未通过");
  expect(screen.getByRole("combobox", { name: "选择资源曲线轮次" }).querySelectorAll("option")).toHaveLength(3);
});

it("discloses a user-cancelled baseline without counting its partial work as throughput", () => {
  render(<CapacityReport evidence={{
    schema_version: 1, updated_at: "2026-11-04", hardware: "M4", engine: "optimized",
    model: "Qwen", model_sha256: "model-hash", dataset_sha256: "dataset-hash",
    context_tokens: 32768, conditions: [], baseline_rounds: [round("C16-R1-v7", 16, true)],
    rounds: [], baseline_reference_mode: "single_completed_trial_user_cancel",
    interrupted_rounds: [{ label: "C16-R2-v7", qa_completed: 22, judge_completed: 21, reason: "user_cancelled" }],
  }} />);
  expect(screen.getByText(/速度对照仅采用原配置 C16 第一轮/)).toBeTruthy();
  expect(screen.getByText(/C16-R2-v7 · 按用户要求取消/)).toBeTruthy();
  expect(screen.queryByRole("row", { name: /C16-R2-v7/ })).toBeNull();
  expect(screen.queryByText("C16")).toBeNull();
});
